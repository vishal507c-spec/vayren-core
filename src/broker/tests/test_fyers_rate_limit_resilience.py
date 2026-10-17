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
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.services.broker_manager import BrokerManager
from broker.error_taxonomy import parse_retry_after
from broker.providers.fyers.auto_auth import FyersAutoAuthEngine
from broker.providers.fyers.live_auth import AuthError
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.status import BrokerStatus

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
