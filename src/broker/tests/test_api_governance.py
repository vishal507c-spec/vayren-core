"""Focused tests for the shared broker API-governance control plane.

Covers the reference behavior the runtime depends on: token-bucket limiting,
metrics, circuit-breaker transitions, historical cache reuse/invalidation and
one in-flight fetch per parameter key. Adapter integration is covered in
``test_fyers_api_governance.py``.
"""

from __future__ import annotations

import threading

import pytest

import broker.common.api_governance as governance
from broker.common.api_governance import (
    APIGovernor,
    BrokerGateway,
    CallClass,
    CircuitBreakerState,
    CircuitOpenError,
    HistoricalDataCache,
    TokenBucket,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_token_bucket_blocks_then_refills(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(governance.time, "time", clock.time)
    monkeypatch.setattr(governance.time, "sleep", clock.sleep)

    bucket = TokenBucket(rate=2.0, capacity=3)
    assert bucket.try_acquire()
    assert bucket.try_acquire()
    assert bucket.try_acquire()
    assert not bucket.try_acquire()

    bucket.acquire()
    assert clock.now == pytest.approx(1000.5, abs=0.02)


def test_read_failures_open_the_read_circuit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(governance.time, "time", clock.time)
    monkeypatch.setattr(governance.time, "sleep", clock.sleep)

    governor = APIGovernor()

    def fail() -> None:
        raise RuntimeError("venue failure")

    for _ in range(5):
        with pytest.raises(RuntimeError, match="venue failure"):
            governor.call("read", fail, call_class=CallClass.READ)

    assert governor.read_circuit.state == CircuitBreakerState.OPEN
    # The order circuit is untouched: a broken read path is not evidence that
    # the venue rejects orders.
    assert governor.order_circuit.state == CircuitBreakerState.CLOSED

    with pytest.raises(CircuitOpenError):
        governor.call("read", lambda: "never", call_class=CallClass.READ)

    clock.now += 31.0
    assert not governor.read_circuit.is_open()
    assert governor.read_circuit.state == CircuitBreakerState.HALF_OPEN

    assert governor.call("probe", lambda: "ok", call_class=CallClass.READ) == "ok"
    assert governor.read_circuit.state == CircuitBreakerState.HALF_OPEN
    assert governor.call("probe", lambda: "ok", call_class=CallClass.READ) == "ok"
    assert governor.read_circuit.state == CircuitBreakerState.CLOSED

    snap = governor.metrics.snapshot()
    assert snap["circuit_trips"] == 1
    assert snap["calls"]["read"] == 5
    assert snap["errors"]["read"] == 6  # five failures + one blocked call


def test_entry_failures_open_the_order_circuit_and_block_new_entries() -> None:
    governor = APIGovernor()

    def fail() -> None:
        raise RuntimeError("venue rejected the order")

    for _ in range(5):
        with pytest.raises(RuntimeError, match="venue rejected the order"):
            governor.call("entry", fail, call_class=CallClass.ENTRY)

    assert governor.order_circuit.state == CircuitBreakerState.OPEN
    assert governor.read_circuit.state == CircuitBreakerState.CLOSED

    # A new entry is refused: the venue is not taking orders.
    with pytest.raises(CircuitOpenError):
        governor.call("entry", lambda: "never", call_class=CallClass.ENTRY)
    # Reads still work — the order path and the read path fail separately.
    assert governor.call("read", lambda: "ok", call_class=CallClass.READ) == "ok"


def test_risk_reducing_work_is_not_blocked_by_an_open_circuit() -> None:
    governor = APIGovernor()

    def fail() -> None:
        raise RuntimeError("venue failure")

    # The order circuit opens first (the read path is still healthy), then
    # the read circuit opens. Both are open for the checks below.
    for _ in range(5):
        with pytest.raises(RuntimeError, match="venue failure"):
            governor.call("entry", fail, call_class=CallClass.ENTRY)
    for _ in range(5):
        with pytest.raises(RuntimeError, match="venue failure"):
            governor.call("read", fail, call_class=CallClass.READ)

    assert governor.read_circuit.state == CircuitBreakerState.OPEN
    assert governor.order_circuit.state == CircuitBreakerState.OPEN

    # A protective exit is still ATTEMPTED with both circuits open.
    assert governor.call("protective", lambda: "sent", call_class=CallClass.RISK_REDUCING) == "sent"

    # Its real error is not swallowed: a failed risk-reducing call raises.
    with pytest.raises(RuntimeError, match="venue failure"):
        governor.call("protective", fail, call_class=CallClass.RISK_REDUCING)

    # It is still rate limited: a token is consumed by every attempt.
    assert governor.bucket.tokens < governor.bucket.capacity
    snap = governor.metrics.snapshot()
    assert snap["calls"]["protective"] == 2
    assert snap["errors"]["protective"] == 1
    assert snap["throttle_waits"] >= 2


def test_bypass_circuit_allows_one_read_on_behalf_of_a_risk_reducing_call() -> None:
    governor = APIGovernor()

    def fail() -> None:
        raise RuntimeError("venue failure")

    for _ in range(5):
        with pytest.raises(RuntimeError, match="venue failure"):
            governor.call("read", fail, call_class=CallClass.READ)
    assert governor.read_circuit.state == CircuitBreakerState.OPEN

    # The duplicate-order check of a protective stop still runs.
    assert (
        governor.call(
            "GET orders",
            lambda: "order book",
            call_class=CallClass.READ,
            bypass_circuit=True,
        )
        == "order book"
    )

    # Without the bypass the very same read is refused.
    with pytest.raises(CircuitOpenError):
        governor.call("GET orders", lambda: "order book", call_class=CallClass.READ)


def test_historical_cache_reuses_until_ttl_and_invalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(governance.time, "time", clock.time)
    monkeypatch.setattr(governance.time, "sleep", clock.sleep)

    cache = HistoricalDataCache(ttl_seconds=60.0)
    owner, ticket = cache.begin_fetch("TOKEN", 100, 200, "5")
    assert owner is True
    assert ticket is not None
    cache.put("TOKEN", 100, 200, "5", [(1, 2.0)], ticket.event)

    assert cache.get("TOKEN", 100, 200, "5") == [(1, 2.0)]
    again, no_ticket = cache.begin_fetch("TOKEN", 100, 200, "5")
    assert again is False
    assert no_ticket is None

    clock.now += 61.0
    assert cache.get("TOKEN", 100, 200, "5") is None

    owner, ticket = cache.begin_fetch("TOKEN", 100, 200, "5")
    assert owner is True
    assert ticket is not None
    cache.put("TOKEN", 100, 200, "5", [(2, 3.0)], ticket.event)
    assert cache.get("TOKEN", 100, 200, "5") == [(2, 3.0)]

    cache.invalidate("TOKEN")
    assert cache.get("TOKEN", 100, 200, "5") is None


def test_historical_cache_publishes_failure_to_waiters_without_storing() -> None:
    cache = HistoricalDataCache()
    owner, ticket = cache.begin_fetch("TOKEN", 1, 2, "1")
    assert owner is True
    assert ticket is not None

    waited, waiter_ticket = cache.begin_fetch("TOKEN", 1, 2, "1")
    assert waited is False
    assert waiter_ticket is ticket
    assert waiter_ticket is not None

    failure = RuntimeError("rate limited")
    cache.put("TOKEN", 1, 2, "1", [], ticket.event, error=failure)

    assert ticket.error is failure
    assert waiter_ticket.error is failure
    assert cache.get("TOKEN", 1, 2, "1") is None


def test_governor_historical_failure_is_not_cached_as_empty_data() -> None:
    governor = APIGovernor()
    calls = 0

    def fail(*_args: object) -> list[int]:
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP 429")

    with pytest.raises(RuntimeError, match="429"):
        governor.historical(fail, "TOKEN", 1, 2, "1")
    with pytest.raises(RuntimeError, match="429"):
        governor.historical(fail, "TOKEN", 1, 2, "1")

    assert calls == 2
    assert governor.hist_cache.size() == 0


def test_governor_historical_coalesces_one_real_fetch() -> None:
    governor = APIGovernor()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fetch(token: object, from_date: object, to_date: object, interval: str) -> list[int]:
        nonlocal calls
        _ = (token, from_date, to_date, interval)
        calls += 1
        started.set()
        assert release.wait(5.0)
        return [7]

    results: list[list[int]] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(governor.historical(fetch, "TOKEN", 1, 2, "1"))
        except BaseException as exc:  # noqa: BLE001 - test collects thread failures
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert started.wait(2.0)
    release.set()
    for thread in threads:
        thread.join(timeout=5.0)

    assert not errors
    assert results == [[7], [7], [7], [7]]
    assert calls == 1


def test_broker_gateway_routes_through_the_governor() -> None:
    governor = APIGovernor()
    gateway = BrokerGateway(governor)
    assert gateway.governor is governor
    assert gateway.call("profile", lambda: {"s": "ok"}) == {"s": "ok"}
    assert governor.metrics.snapshot()["calls"]["profile"] == 1
