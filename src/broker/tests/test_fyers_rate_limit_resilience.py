"""Regression and resilience test suite for FYERS rate-limiting (HTTP 429).

Covers:
1. `parse_retry_after` bounded floor enforcement (Retry-After: 0 -> min_s).
2. `parse_retry_after` missing/invalid/negative header fallback.
3. `parse_retry_after` exponential backoff and jitter.
4. `auto_auth` immediate single-attempt abort on HTTP 429 (no OTP hammering).
5. `FyersSessionAdapter` profile caching to prevent duplicate REST calls.
6. `_job_health` non-fatal degradation to EXECUTION_NOT_READY on sub-read 429.
7. `_job_health` session-level 429 transition to BrokerStatus.ERROR with armed retry.
8. Safety: unverified orders access is never claimed healthy.
9. Single-flight reconciliation snapshot: the shared lock is never held across
   venue reads, joiners coalesce on one fetch and stay bounded, and an
   invalidation mid-read wins over the in-flight (possibly pre-op) read.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import broker.providers.fyers.session_adapter as session_adapter
from app.services.broker_manager import BrokerManager
from broker.error_taxonomy import parse_retry_after
from broker.providers.fyers.auto_auth import FyersAutoAuthEngine
from broker.providers.fyers.live_auth import AuthError
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.status import BrokerStatus
from broker.vocab import BrokerError

# ---------------------------------------------------------------------------
# 1. parse_retry_after Tests
# ---------------------------------------------------------------------------


def test_parse_retry_after_zero_header_enforces_minimum_floor() -> None:
    """Retry-After: 0 must not cause immediate retries; floor of 5s enforced."""
    delay = parse_retry_after({"Retry-After": "0"}, default_s=5.0, min_s=5.0)
    assert delay >= 5.0, f"Expected at least 5.0s, got {delay}"


def test_parse_retry_after_negative_header_enforces_minimum_floor() -> None:
    """Negative values in Retry-After must be clamped to min_s."""
    delay = parse_retry_after({"Retry-After": "-10"}, default_s=5.0, min_s=5.0)
    assert delay >= 5.0


def test_parse_retry_after_missing_header_uses_default() -> None:
    """Missing Retry-After header returns default_s bounded by min_s."""
    delay = parse_retry_after({}, default_s=8.0, min_s=5.0)
    assert delay == 8.0

    delay_low = parse_retry_after({}, default_s=2.0, min_s=5.0)
    assert delay_low >= 5.0


def test_parse_retry_after_valid_positive_seconds() -> None:
    """Valid positive seconds header is respected within bounds."""
    delay = parse_retry_after({"Retry-After": "15"}, default_s=5.0, min_s=5.0, max_s=60.0)
    assert delay == 15.0

    # Over max cap is clamped
    delay_capped = parse_retry_after({"Retry-After": "120"}, default_s=5.0, min_s=5.0, max_s=60.0)
    assert delay_capped == 60.0


def test_parse_retry_after_exponential_backoff() -> None:
    """Increasing attempts produce exponential backoff."""
    d0 = parse_retry_after({"Retry-After": "5"}, default_s=5.0, min_s=5.0, attempt=0)
    d1 = parse_retry_after({"Retry-After": "5"}, default_s=5.0, min_s=5.0, attempt=1)
    d2 = parse_retry_after({"Retry-After": "5"}, default_s=5.0, min_s=5.0, attempt=2)

    assert d0 == 5.0
    assert d1 == 10.0  # 5 * 2^1
    assert d2 == 20.0  # 5 * 2^2


def test_parse_retry_after_jitter() -> None:
    """With add_jitter=True, delay includes bounded jitter within [delay, delay * 1.25]."""
    d = parse_retry_after({"Retry-After": "10"}, default_s=5.0, min_s=5.0, add_jitter=True)
    assert 10.0 <= d <= 12.5


# ---------------------------------------------------------------------------
# 2. auto_auth Abort on HTTP 429
# ---------------------------------------------------------------------------


def test_auto_auth_aborts_immediately_on_429() -> None:
    """When FYERS returns HTTP 429 during auto-auth, do not hammer OTP endpoint with attempt 2."""
    creds = SimpleNamespace(
        client_id="XV29244",
        app_id="test_app-100",
        totp_secret="JBSWY3DPEHPK3PXP",
        pin="1234",
        redirect_uri="https://trade.fyers.in/api-login/redirect-uri/index.html",
        configured=True,
        auto_login_configured=True,
    )
    transport = MagicMock()
    # Step 1 request_otp_key raises HTTP 429
    transport.post_json.side_effect = AuthError(
        "HTTP 429 Too Many Requests (rate limited)", code="RATE_LIMITED"
    )

    engine = FyersAutoAuthEngine(credentials=creds, transport=transport)
    session_store = SimpleNamespace(load_token=lambda: None, save_token=MagicMock())

    ok, reason = engine._run_auto_auth(session_store, MagicMock())

    assert ok is False
    assert "rate limited" in reason.lower() or "429" in reason
    # Crucial: Must be called EXACTLY ONCE, not retried for attempt 2!
    assert transport.post_json.call_count == 1


# ---------------------------------------------------------------------------
# 3. FyersSessionAdapter Profile Caching Tests
# ---------------------------------------------------------------------------


def test_fyers_session_adapter_profile_caching() -> None:
    """health() and account() share cached profile without duplicate network calls."""
    mock_transport = MagicMock()
    mock_transport.get_json.return_value = {
        "s": "ok",
        "code": 200,
        "data": {"fy_id": "XV29244", "name": "Vayren Trader"},
    }

    adapter = FyersSessionAdapter(
        app_id="test_app-100",
        access_token="test_token",
        transport=mock_transport,
        enable_order_ws=False,
    )

    # Initial connect populates profile cache
    adapter.connect()
    assert mock_transport.get_json.call_count == 1

    # Immediate health() check uses cached profile
    ok, reason = adapter.health()
    assert ok is True
    assert reason == "ok"
    assert mock_transport.get_json.call_count == 1

    # Immediate account() check uses cached profile
    acc = adapter.account()
    assert acc["account_id"] == "XV29244"
    assert mock_transport.get_json.call_count == 1  # Still 1! Zero redundant network calls

    # Disconnect clears cache
    adapter.disconnect()
    assert adapter._profile_cache is None


def test_reconciliation_snapshot_coalesces_per_symbol_session_fanout() -> None:
    """22 per-symbol sessions must not issue 44 account reads in one cycle.

    Regression for the LIVE blocker: every LiveSession reconciles the same
    account, so an uncached adapter produced positions+orders per symbol
    inside a sub-second loop and FYERS answered HTTP 429.
    """
    calls: list[str] = []

    def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        calls.append(url)
        if url.endswith("/positions"):
            return {"s": "ok", "netPositions": [{"symbol": "NSE:NETWEB-EQ", "quantity": 1}]}
        if url.endswith("/orders"):
            return {"s": "ok", "orderBook": []}
        raise AssertionError(f"unexpected URL: {url}")

    transport = MagicMock()
    transport.get_json.side_effect = get_json
    adapter = FyersSessionAdapter(
        app_id="test_app-100",
        access_token="test_token",
        transport=transport,
        enable_order_ws=False,
        account_snapshot_cache_s=60.0,
    )

    snapshots = [adapter.reconciliation_snapshot() for _ in range(22)]

    expected_positions = [{"symbol": "NSE:NETWEB-EQ", "quantity": 1}]
    assert all(positions == expected_positions for positions, _ in snapshots)
    assert all(orders == [] for _, orders in snapshots)
    assert sum(url.endswith("/positions") for url in calls) == 1
    assert sum(url.endswith("/orders") for url in calls) == 1


def test_failed_reconciliation_snapshot_is_shared_and_still_raises() -> None:
    """A 429 is shared across sessions, never suppressed or retried 22 times."""
    calls: list[str] = []

    def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        calls.append(url)
        raise AuthError(
            "rate limited by venue (HTTP 429, retry in 5s)",
            code="RATE_LIMITED",
            http_status=429,
        )

    transport = MagicMock()
    transport.get_json.side_effect = get_json
    adapter = FyersSessionAdapter(
        app_id="test_app-100",
        access_token="test_token",
        transport=transport,
        enable_order_ws=False,
        account_snapshot_cache_s=60.0,
    )

    for _ in range(22):
        with pytest.raises(AuthError) as exc_info:
            adapter.reconciliation_snapshot()
        assert exc_info.value.code == "RATE_LIMITED"
        assert exc_info.value.http_status == 429

    assert len(calls) == 1


def test_reconciliation_snapshot_cache_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached snapshot is short-lived; the next window reads broker truth again."""
    calls: list[str] = []

    def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        calls.append(url)
        if url.endswith("/positions"):
            return {"s": "ok", "netPositions": []}
        if url.endswith("/orders"):
            return {"s": "ok", "orderBook": []}
        raise AssertionError(f"unexpected URL: {url}")

    transport = MagicMock()
    transport.get_json.side_effect = get_json
    adapter = FyersSessionAdapter(
        app_id="test_app-100",
        access_token="test_token",
        transport=transport,
        enable_order_ws=False,
        account_snapshot_cache_s=5.0,
    )

    clock = [1000.0]
    monkeypatch.setattr(session_adapter.time, "monotonic", lambda: clock[0])

    adapter.reconciliation_snapshot()
    adapter.reconciliation_snapshot()
    assert len(calls) == 2

    clock[0] += 5.1
    adapter.reconciliation_snapshot()
    assert len(calls) == 4


def test_generic_account_reads_stay_uncached() -> None:
    """Only reconciliation shares a snapshot; direct callers still read fresh."""
    calls: list[str] = []

    def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        calls.append(url)
        if url.endswith("/positions"):
            return {"s": "ok", "netPositions": []}
        if url.endswith("/orders"):
            return {"s": "ok", "orderBook": []}
        raise AssertionError(f"unexpected URL: {url}")

    transport = MagicMock()
    transport.get_json.side_effect = get_json
    adapter = FyersSessionAdapter(
        app_id="test_app-100",
        access_token="test_token",
        transport=transport,
        enable_order_ws=False,
        account_snapshot_cache_s=60.0,
    )

    adapter.positions()
    adapter.positions()
    adapter.open_orders()
    adapter.open_orders()

    assert sum(url.endswith("/positions") for url in calls) == 2
    assert sum(url.endswith("/orders") for url in calls) == 2


# ---------------------------------------------------------------------------
# 4. _job_health Sub-read Rate Limit Degradation (Requirement 5)
# ---------------------------------------------------------------------------


class _VerifiedSessionRateLimitedOrders:
    """Session is valid and identity verified, but orders sub-read gets HTTP 429."""

    connected = True

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def account(self) -> dict[str, Any]:
        return {"account_id": "XV29244"}

    def funds(self) -> dict[str, Any]:
        return {"available": 50000.0, "used": 0.0, "total": 50000.0}

    def positions(self) -> list[dict[str, Any]]:
        return []

    def open_orders(self) -> list[dict[str, Any]]:
        raise RuntimeError("HTTP 429 Too Many Requests: venue rate limited")

    def order_stream_health(self) -> tuple[bool, str]:
        return True, "connected"


def test_job_health_429_on_orders_preserves_session_as_execution_not_ready(tmp_path: Any) -> None:
    """When orders check gets HTTP 429, preserve authenticated session as EXECUTION_NOT_READY."""
    bm = BrokerManager(str(tmp_path), specs={})
    spec = SimpleNamespace(broker_id="fyers")
    adapter = _VerifiedSessionRateLimitedOrders()

    bm._states["fyers"] = {
        "adapter": adapter,
        "market_data": None,
        "details": bm._empty_details(),
        "checks": {},
        "status": BrokerStatus.CONNECTED,
        "reason": "authenticated as XV29244",
        "configured": True,
        "api_key_masked": "AB",
        "last_sync": "",
    }
    bm._desired["fyers"] = "CONNECTED"
    bm._unified["fyers"] = {"legs": {}, "version": 0}

    bm._job_health(spec)  # type: ignore[arg-type]

    state = bm.state("fyers")

    # 1. Never report LOGIN_REQUIRED or ERROR when token is valid!
    assert state["status"] is not BrokerStatus.LOGIN_REQUIRED
    assert state["status"] is not BrokerStatus.ERROR

    # 2. Status must be EXECUTION_NOT_READY (degraded capability)
    assert state["status"] is BrokerStatus.EXECUTION_NOT_READY

    # 3. Account identity preserved
    assert state["details"]["account_id"] == "XV29244"
    assert state["details"]["funds_available"] == 50000.0

    # 4. Check reflects honest rate limit status
    assert "orders" in state["checks"]
    assert "rate limited" in state["checks"]["orders"].lower()

    # 5. Retry timer is armed with cooldown
    assert bm._retry_armed("fyers") is True

    # 6. Adapter remains connected
    assert adapter.connected is True


class _SessionLevelRateLimited:
    """Connection/profile check itself gets HTTP 429."""

    connected = True

    def health(self) -> tuple[bool, str]:
        return False, "HTTP 429 Too Many Requests: venue rate limited"

    def account(self) -> dict[str, Any]:
        raise RuntimeError("HTTP 429 Too Many Requests")


def test_job_health_session_level_429_transitions_to_error(tmp_path: Any) -> None:
    """When session-level profile check gets HTTP 429, status is ERROR with armed retry."""
    bm = BrokerManager(str(tmp_path), specs={})
    spec = SimpleNamespace(broker_id="fyers")
    adapter = _SessionLevelRateLimited()

    bm._states["fyers"] = {
        "adapter": adapter,
        "market_data": None,
        "details": bm._empty_details(),
        "checks": {},
        "status": BrokerStatus.CONNECTED,
        "reason": "ok",
        "configured": True,
        "api_key_masked": "AB",
        "last_sync": "",
    }
    bm._desired["fyers"] = "CONNECTED"
    bm._unified["fyers"] = {"legs": {}, "version": 0}

    bm._job_health(spec)  # type: ignore[arg-type]

    state = bm.state("fyers")

    # Session could not be verified due to rate limit -> ERROR
    assert state["status"] is BrokerStatus.ERROR
    assert "rate limited" in state["reason"].lower()
    assert bm._retry_armed("fyers") is True


# ---------------------------------------------------------------------------
# 5. Single-flight account snapshot (no lock across venue reads)
# ---------------------------------------------------------------------------


def _blocking_account_transport(
    entered: threading.Event, release: threading.Event, calls: list[str]
) -> MagicMock:
    """Transport whose positions read blocks until ``release`` is set."""

    def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        calls.append(url)
        if url.endswith("/positions"):
            entered.set()
            assert release.wait(timeout=10.0)
            return {"s": "ok", "netPositions": [{"symbol": "NSE:INFY", "quantity": 1}]}
        if url.endswith("/orders"):
            return {"s": "ok", "orderBook": []}
        if url.endswith("/profile"):
            return {"s": "ok", "data": {"fy_id": "XV29244"}}
        raise AssertionError(f"unexpected URL: {url}")

    transport = MagicMock()
    transport.get_json.side_effect = get_json
    return transport


def _snapshot_adapter(transport: MagicMock, **kwargs: Any) -> FyersSessionAdapter:
    return FyersSessionAdapter(
        app_id="test_app-100",
        access_token="test_token",
        transport=transport,
        enable_order_ws=False,
        account_snapshot_cache_s=60.0,
        **kwargs,
    )


def test_slow_account_read_holds_no_lock_and_blocks_nothing() -> None:
    """The shared snapshot lock is never held across the venue read.

    Regression for the LIVE lock convoy: one session's positions read used to
    hold ``_account_snapshot_lock`` across both REST calls while every other
    caller (the 22-session fleet plus every invalidation path) queued behind
    the mutex until the network read finished.
    """
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    adapter = _snapshot_adapter(_blocking_account_transport(entered, release, calls))

    results: list[Any] = []
    reader = threading.Thread(target=lambda: results.append(adapter.reconciliation_snapshot()))
    reader.start()
    assert entered.wait(timeout=5.0), "venue read never started"

    # While the venue read is blocked:
    # - the shared lock is free (the exact wedge mechanism),
    # - invalidation completes (91 threads queued here on the EC2 wedge),
    # - an unrelated adapter call still works (gateway stays responsive).
    assert adapter._account_snapshot_lock.acquire(blocking=False) is True
    adapter._account_snapshot_lock.release()
    adapter._invalidate_account_snapshot()
    ok, reason = adapter.health()
    assert (ok, reason) == (True, "ok")

    release.set()
    reader.join(timeout=5.0)
    assert reader.is_alive() is False
    assert results == [([{"symbol": "NSE:INFY", "quantity": 1}], [])]
    assert sum(url.endswith("/positions") for url in calls) == 1
    assert sum(url.endswith("/orders") for url in calls) == 1


def test_concurrent_snapshot_callers_join_one_fetch() -> None:
    """Concurrent reconciles coalesce on ONE venue read pair (single-flight).

    Before the fix only the TTL cache coalesced callers; on a miss every
    caller serialized behind the lock while one thread did the reads. Now the
    first caller owns the read and the rest join its ticket — same result,
    one REST pair, no queue.
    """
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    adapter = _snapshot_adapter(_blocking_account_transport(entered, release, calls))

    results: list[Any] = []
    errors: list[BaseException] = []

    def call() -> None:
        try:
            results.append(adapter.reconciliation_snapshot())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(8)]
    threads[0].start()
    assert entered.wait(timeout=5.0), "owner read never started"
    for thread in threads[1:]:
        thread.start()
    time.sleep(0.05)  # let the other callers join the in-flight ticket

    # The joiners wait on the ticket event, not on a mutex: the lock is free
    # while the owner is still inside the venue read.
    assert adapter._account_snapshot_lock.acquire(blocking=False) is True
    adapter._account_snapshot_lock.release()

    release.set()
    for thread in threads:
        thread.join(timeout=5.0)
        assert thread.is_alive() is False

    assert errors == []
    expected = ([{"symbol": "NSE:INFY", "quantity": 1}], [])
    assert results == [expected] * 8
    assert sum(url.endswith("/positions") for url in calls) == 1
    assert sum(url.endswith("/orders") for url in calls) == 1


def test_snapshot_joiner_times_out_fail_closed_instead_of_hanging() -> None:
    """A joiner that outlives the wait bound fails closed, never hangs.

    The bound is the difference between "a slow read delays this caller" and
    "the gateway stops responding": on expiry the joiner raises a blocking
    transport error (RECONCILE_ERROR upstream) and the dead ticket is
    deregistered so the next caller can retry.
    """
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    adapter = _snapshot_adapter(
        _blocking_account_transport(entered, release, calls),
        account_snapshot_wait_s=0.1,
    )

    owner_result: list[Any] = []
    owner = threading.Thread(target=lambda: owner_result.append(adapter.reconciliation_snapshot()))
    owner.start()
    assert entered.wait(timeout=5.0), "owner read never started"

    started = time.monotonic()
    with pytest.raises(BrokerError) as exc_info:
        adapter.reconciliation_snapshot()
    assert time.monotonic() - started < 5.0  # bounded, not the 10s transport wait
    assert "did not complete" in str(exc_info.value)
    # The dead ticket is deregistered: a later caller owns a fresh fetch.
    assert adapter._account_snapshot_fetch is None

    release.set()
    owner.join(timeout=5.0)
    assert owner_result == [([{"symbol": "NSE:INFY", "quantity": 1}], [])]


def test_invalidation_during_inflight_read_discards_the_publish() -> None:
    """A fetch that began before an account-changing op must not cache.

    The operation invalidated the snapshot; the in-flight read may predate it,
    so the next caller must read fresh truth instead of the stale window.
    """
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    adapter = _snapshot_adapter(_blocking_account_transport(entered, release, calls))

    results: list[Any] = []
    reader = threading.Thread(target=lambda: results.append(adapter.reconciliation_snapshot()))
    reader.start()
    assert entered.wait(timeout=5.0), "venue read never started"

    adapter._invalidate_account_snapshot()  # an order op lands mid-read
    release.set()
    reader.join(timeout=5.0)
    assert reader.is_alive() is False
    assert results == [([{"symbol": "NSE:INFY", "quantity": 1}], [])]

    adapter.reconciliation_snapshot()  # must NOT be served by the stale window
    assert sum(url.endswith("/positions") for url in calls) == 2
    assert sum(url.endswith("/orders") for url in calls) == 2
