"""Focused FYERS integration tests for the shared API-governance layer.

These tests prove the existing runtime surfaces -- reconciliation, historical
backfill, order mutation and duplicate-order guard -- actually pass through the
adapter-owned governor rather than only importing classes with the same names.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pytest

from broker.common.api_governance import CircuitOpenError
from broker.providers.fyers.live_auth import AuthError
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.vocab import BrokerError


@dataclass
class _Plan:
    intent_id: str = "strat:1:i1"
    symbol: str = "SBIN"
    side: str = "BUY"
    quantity: float = 1.0
    order_type: str = "MARKET"
    limit_price: float | None = None
    trigger_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "DAY"
    product: str = "INTRADAY"
    bracket: tuple[dict[str, object], ...] = ()


def _protective_plan(symbol: str = "NSE:SBIN-EQ") -> _Plan:
    """The shape the execution engine uses for its stop-protection order."""
    return _Plan(
        intent_id=f"sl:{symbol}:1",
        symbol=symbol,
        side="SELL",
        quantity=1.0,
        order_type="STOP_MARKET",
        stop_price=100.0,
    )


def _protective_client_order_id(symbol: str = "NSE:SBIN-EQ") -> str:
    return f"sl:{symbol}:1:o1"


class _AccountTransport:
    def __init__(
        self,
        position_response: dict[str, Any] | None = None,
        order_response: dict[str, Any] | None = None,
    ) -> None:
        self.position_response = position_response or {"s": "ok", "netPositions": []}
        self.order_response = order_response or {"s": "ok", "orderBook": []}
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def _record(self, method: str, url: str) -> None:
        with self._lock:
            self.calls.append((method, url))

    def count(self, method: str, suffix: str) -> int:
        with self._lock:
            return sum(
                1
                for called_method, url in self.calls
                if called_method == method and url.endswith(suffix)
            )

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        self._record("GET", url)
        if url.endswith("/profile"):
            return {"s": "ok", "data": {"fy_id": "XV29244"}}
        if url.endswith("/positions"):
            return self.position_response
        if url.endswith("/orders"):
            return self.order_response
        raise AssertionError(f"unexpected GET: {url}")

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (payload, headers)
        self._record("POST", url)
        return {"s": "ok", "id": "ORDER-1"}

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (payload, headers)
        self._record("PUT", url)
        return {"s": "ok"}

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (payload, headers)
        self._record("DELETE", url)
        return {"s": "ok"}


class _HistoryTransport:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.started = threading.Event()
        self.release = threading.Event()
        self.history_calls = 0
        self._lock = threading.Lock()

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        if "/data/history" not in url:
            raise AssertionError(f"unexpected GET: {url}")
        with self._lock:
            self.history_calls += 1
        self.started.set()
        if not self.release.wait(5.0):
            raise TimeoutError("test did not release the history fetch")
        if self.error is not None:
            raise self.error
        return {
            "s": "ok",
            "candles": [[1_700_000_000, 100.0, 101.0, 99.0, 100.5, 1234]],
        }

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        raise AssertionError("history transport does not place orders")

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        raise AssertionError("history transport does not modify orders")

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        raise AssertionError("history transport does not cancel orders")


def test_reconciliation_snapshot_is_one_governed_read_for_all_sessions() -> None:
    transport = _AccountTransport()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
        account_snapshot_cache_s=60.0,
    )
    adapter.connect()
    transport.calls.clear()

    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = list(pool.map(lambda _: adapter.reconciliation_snapshot(), range(22)))

    assert all(positions == [] for positions, _ in snapshots)
    assert all(orders == [] for _, orders in snapshots)
    assert transport.count("GET", "/positions") == 1
    assert transport.count("GET", "/orders") == 1

    metrics = adapter.api_metrics()
    assert metrics["calls"]["GET positions"] == 1
    assert metrics["calls"]["GET orders"] == 1


def test_auth_error_envelope_never_becomes_an_empty_reconciliation() -> None:
    transport = _AccountTransport(
        position_response={"s": "error", "code": -8, "message": "token expired"},
    )
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )

    with pytest.raises(AuthError) as excinfo:
        adapter.reconciliation_snapshot()

    assert excinfo.value.code == "AUTH_EXPIRED"
    assert transport.count("GET", "/positions") == 1
    assert transport.count("GET", "/orders") == 0


def test_concurrent_history_requests_coalesce_to_one_fetch() -> None:
    transport = _HistoryTransport()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(adapter.history, "NSE:SBIN", "1", 100, 200) for _ in range(8)]
        assert transport.started.wait(2.0)
        time.sleep(0.1)
        transport.release.set()
        results = [future.result(timeout=5.0) for future in futures]

    expected = [(1_700_000_000, 100.0, 101.0, 99.0, 100.5, 1234)]
    assert all(result == expected for result in results)
    assert transport.history_calls == 1
    assert adapter.governor.hist_cache.size() == 1

    assert adapter.history("NSE:SBIN", "1", 100, 200) == expected
    assert transport.history_calls == 1
    assert adapter.api_metrics()["calls"]["historical_data"] == 1
    assert adapter.api_metrics()["cache_hits"] >= 1


def test_history_rate_limit_is_raised_and_not_cached() -> None:
    rate_limited = AuthError(
        "rate limited by venue (HTTP 429, retry in 5s)",
        code="RATE_LIMITED",
        http_status=429,
    )
    transport = _HistoryTransport(error=rate_limited)
    transport.release.set()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )

    with pytest.raises(AuthError) as excinfo:
        adapter.history("NSE:SBIN", "1", 100, 200)
    assert excinfo.value.code == "RATE_LIMITED"
    assert excinfo.value.http_status == 429
    assert adapter.governor.hist_cache.size() == 0

    with pytest.raises(AuthError):
        adapter.history("NSE:SBIN", "1", 100, 200)
    assert transport.history_calls == 2


def test_order_mutation_and_ws_event_invalidate_shared_snapshot() -> None:
    transport = _AccountTransport()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
        account_snapshot_cache_s=60.0,
    )
    adapter._connected = True

    adapter.reconciliation_snapshot()
    assert transport.count("GET", "/positions") == 1
    assert transport.count("GET", "/orders") == 1

    assert adapter.cancel_order("ORDER-1") is True
    adapter.reconciliation_snapshot()
    assert transport.count("GET", "/positions") == 2
    assert transport.count("GET", "/orders") == 2

    adapter._on_ws_orders(
        {
            "orders": {
                "orderTag": "TAG-1",
                "id": "ORDER-1",
                "status": 6,
                "symbol": "NSE:SBIN-EQ",
                "filledQty": 0,
                "side": 1,
            }
        }
    )
    adapter.reconciliation_snapshot()
    assert transport.count("GET", "/positions") == 3
    assert transport.count("GET", "/orders") == 3
    assert adapter.api_metrics()["calls"]["DELETE orders/sync"] == 1


def test_unreadable_order_book_blocks_duplicate_order_check() -> None:
    transport = _AccountTransport(
        order_response={"s": "error", "code": -8, "message": "token expired"},
    )
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )
    adapter._connected = True

    with pytest.raises(AuthError):
        adapter.place_order(_Plan(), client_order_id="TAG-1")

    assert transport.count("GET", "/orders") == 1
    assert transport.count("POST", "/orders/sync") == 0


def test_malformed_account_payload_never_becomes_a_flat_book() -> None:
    transport = _AccountTransport(position_response={"s": "ok"})
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )

    with pytest.raises(BrokerError):
        adapter.reconciliation_snapshot()

    assert transport.count("GET", "/positions") == 1
    assert transport.count("GET", "/orders") == 0


def test_open_circuit_blocks_reads_without_relabelling_them_as_auth() -> None:
    class _AlwaysRateLimited:
        def __init__(self) -> None:
            self.calls = 0

        def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
            _ = (url, headers)
            self.calls += 1
            raise AuthError(
                "rate limited by venue (HTTP 429, retry in 5s)",
                code="RATE_LIMITED",
                http_status=429,
            )

        def post_json(
            self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
        ) -> dict[str, Any]:
            _ = (url, payload, headers)
            raise AssertionError("no order may be sent while the circuit is open")

        def put_json(
            self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
        ) -> dict[str, Any]:
            _ = (url, payload, headers)
            raise AssertionError("no order may be sent while the circuit is open")

        def delete_json(
            self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
        ) -> dict[str, Any]:
            _ = (url, payload, headers)
            raise AssertionError("no order may be sent while the circuit is open")

    transport = _AlwaysRateLimited()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )

    for _ in range(5):
        with pytest.raises(AuthError):
            adapter.connect()

    with pytest.raises(CircuitOpenError):
        adapter.connect()

    assert transport.calls == 5
    assert adapter.governor.read_circuit.state == "OPEN"
    assert adapter.governor.order_circuit.state == "CLOSED"


class _ReadFailsTransport:
    """The venue answers reads with 429 but still accepts order writes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.reads = 0
        self.posts = 0
        self.deletes = 0
        self._lock = threading.Lock()

    def _record(self, method: str, url: str) -> None:
        with self._lock:
            self.calls.append((method, url))

    def count(self, method: str) -> int:
        with self._lock:
            return sum(1 for called, _ in self.calls if called == method)

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        self._record("GET", url)
        with self._lock:
            self.reads += 1
        if url.endswith("/funds"):
            raise AuthError(
                "rate limited by venue (HTTP 429, retry in 5s)",
                code="RATE_LIMITED",
                http_status=429,
            )
        if url.endswith("/orders"):
            return {"s": "ok", "code": 200, "orderBook": []}
        if url.endswith("/positions"):
            return {"s": "ok", "code": 200, "netPositions": []}
        return {"s": "ok", "code": 200, "data": {"fy_id": "XV29244"}}

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        self._record("POST", url)
        with self._lock:
            self.posts += 1
        return {"s": "ok", "code": 1101, "message": "Order submitted successfully", "id": "SL-1"}

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        self._record("PUT", url)
        return {"s": "ok", "code": 1102, "message": "Order modified successfully"}

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        self._record("DELETE", url)
        with self._lock:
            self.deletes += 1
        return {"s": "ok", "code": 1103, "message": "Order cancelled successfully"}


def _adapter_with_open_read_circuit() -> tuple[FyersSessionAdapter, _ReadFailsTransport]:
    transport = _ReadFailsTransport()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )
    adapter._connected = True
    # Five failing reads open the READ circuit; the order circuit stays closed.
    for _ in range(5):
        with pytest.raises(AuthError):
            adapter.funds()
    assert adapter.governor.read_circuit.state == "OPEN"
    assert adapter.governor.order_circuit.state == "CLOSED"
    return adapter, transport


def test_read_circuit_blocks_reads_and_new_entries_but_not_protective_exits() -> None:
    adapter, transport = _adapter_with_open_read_circuit()

    # 1. Reads are refused while the read circuit is open.
    with pytest.raises(CircuitOpenError):
        adapter.funds()

    # 2. A NEW ENTRY cannot bypass the safety gates: its duplicate-order
    #    check is a read, so it is refused before anything is sent.
    with pytest.raises(CircuitOpenError):
        adapter.place_order(_Plan(), client_order_id="strat:1:i1")
    assert transport.count("POST") == 0

    # 3. A PROTECTIVE EXIT is attempted even with the read circuit open:
    #    its duplicate check still runs (and is still rate limited).
    broker_id = adapter.place_order(
        _protective_plan(), client_order_id=_protective_client_order_id()
    )
    assert broker_id == "SL-1"
    assert transport.count("POST") == 1

    # 4. Cancelling that KNOWN order is risk-reducing and is attempted too.
    assert adapter.cancel_order(broker_id) is True
    assert transport.count("DELETE") == 1

    # 5. A cancel of an order this adapter never placed is ambiguous, so it
    #    stays gated — it is not silently treated as risk-reducing.
    with pytest.raises(CircuitOpenError):
        adapter.cancel_order("UNKNOWN-BROKER-ID")
    assert transport.count("DELETE") == 1

    # 6. The protective attempts were still rate limited.
    assert adapter.governor.bucket.tokens < adapter.governor.bucket.capacity


def test_order_state_reports_the_venues_own_view_of_one_order() -> None:
    transport = _AccountTransport(
        order_response={
            "s": "ok",
            "orderBook": [
                {
                    "id": "VENUE-1",
                    "orderTag": "sl:NSE:SBIN-EQ:1:o1",
                    "status": 6,
                    "symbol": "NSE:SBIN-EQ",
                    "filledQty": 0,
                }
            ],
        }
    )
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )

    listed = adapter.order_state("VENUE-1")
    assert listed["listed"] is True
    assert listed["client_order_id"] == "sl:NSE:SBIN-EQ:1:o1"

    gone = adapter.order_state("VENUE-UNKNOWN")
    assert gone["listed"] is False
    assert gone["client_order_id"] == ""


def test_order_state_is_attempted_while_the_read_circuit_is_open() -> None:
    """The check settles a cancel decision for a protective order.

    It is rate limited like every other call, but it must not be refused by
    an open read circuit — otherwise a stop could never be verified.
    """
    transport = _ReadFailsTransport()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )
    adapter._connected = True
    for _ in range(5):
        with pytest.raises(AuthError):
            adapter.funds()
    assert adapter.governor.read_circuit.state == "OPEN"

    state = adapter.order_state("VENUE-1")
    assert state["listed"] is False
    assert transport.count("GET") >= 6  # five failing funds + the order book read


def test_protective_intent_is_derived_from_the_execution_convention() -> None:
    transport = _AccountTransport()
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )

    # The engine's own protective shape is recognised.
    assert adapter._is_protective_exit(_protective_plan(), _protective_client_order_id()) is True

    # A strategy entry is never protective.
    assert adapter._is_protective_exit(_Plan(), "strat:1:i1") is False

    # An "sl:" intent with a non-stop order type is not protective.
    assert (
        adapter._is_protective_exit(
            _Plan(intent_id="sl:NSE:SBIN-EQ:1", order_type="MARKET"),
            _protective_client_order_id(),
        )
        is False
    )

    # A stop order without the engine's stop price is not protective.
    assert (
        adapter._is_protective_exit(
            _Plan(intent_id="sl:NSE:SBIN-EQ:1", order_type="STOP_MARKET"),
            _protective_client_order_id(),
        )
        is False
    )

    # A client order id that does not match the engine's shape is not
    # protective (ambiguous -> fail safe -> treated as an entry).
    assert adapter._is_protective_exit(_protective_plan(), "some-other-client-id") is False

    # An "sl:" intent for a DIFFERENT symbol than the order itself is not
    # protective either.
    assert (
        adapter._is_protective_exit(
            _Plan(
                intent_id="sl:NSE:OTHER-EQ:1",
                symbol="NSE:SBIN-EQ",
                order_type="STOP_MARKET",
                stop_price=100.0,
            ),
            "sl:NSE:OTHER-EQ:1:o1",
        )
        is False
    )
