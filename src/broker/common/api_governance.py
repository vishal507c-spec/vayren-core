"""Shared broker API governance: rate limit, metrics, circuit, cache, routing.

Ported from the reference institutional API-management layer. The reference
wrapped a Kite client with ``BrokerGateway``; VAYREN already owns the FYERS
transport inside :class:`~broker.providers.fyers.session_adapter.FyersSessionAdapter`.
This module therefore provides the provider-neutral control plane only: the
adapter remains the single authenticated broker boundary, and every eligible
transport call is routed through one ``APIGovernor`` instance owned by that
adapter. No second broker layer is introduced.

Behavior kept from the reference:

* token bucket rate ``2.5``/s with capacity ``3``;
* call/error/latency/throttle/cache metrics;
* circuit opens after ``5`` consecutive failures, probes after ``30`` s and
  closes after ``2`` consecutive successes;
* historical cache TTL ``60`` s, at most ``512`` entries, one in-flight fetch
  per parameter key (concurrent callers wait for the owner);
* order placement/cancellation/modification is never cached.

Circuit separation (the one deliberate change to the reference's single
global circuit): a read/API-health circuit and an order circuit are tracked
separately, and each call is classified as ``READ``, ``ENTRY`` or
``RISK_REDUCING``. An open read circuit refuses further reads and new
entries, but never refuses a protective exit or an exposure-reducing
cancellation — blocking a risk-reducing order is more dangerous than one
extra rate-limited attempt that fails honestly. New entries stay gated by
both circuits, so an unhealthy venue still cannot be handed more exposure.

Deliberate fail-closed deviations:

* the circuit is created eagerly (the reference required ``wire_log`` first,
  which made ``call`` unusable until then);
* a failed historical fetch is propagated to every waiter instead of being
  reported as an empty list, and a waiter timeout raises instead of
  pretending the venue returned no data.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class CallClass(StrEnum):
    """What a governed call is allowed to do while a circuit is open.

    ``READ``
        Account/market reads. Blocked by the read circuit: when the venue's
        read path is unhealthy, more reads are exactly what must not happen.
    ``ENTRY``
        New exposure. Blocked by BOTH circuits: an entry may only be sent
        when the venue is healthy *and* orders are working, because the
        account truth cannot be trusted otherwise.
    ``RISK_REDUCING``
        Protective exits and exposure-reducing cancellations. Never blocked
        by a circuit: refusing a stop that reduces risk is more dangerous
        than one extra rate-limited attempt that fails honestly.
    """

    READ = "read"
    ENTRY = "entry"
    RISK_REDUCING = "risk_reducing"


class CircuitOpenError(RuntimeError):
    """The governor refused a call while its circuit is open."""

    def __init__(self, method_name: str, call_class: CallClass = CallClass.READ) -> None:
        super().__init__(
            f"venue circuit open — {method_name} blocked "
            f"({call_class.value} circuit OPEN, request not sent)"
        )
        self.method_name = method_name
        self.call_class = call_class
        # Lets the shared error taxonomy classify this as a venue failure
        # instead of an unknown request error. The message deliberately
        # carries no timeout/network wording: nothing was sent, so the
        # failure is a deterministic refusal, not a sent-but-unknown order.
        self.http_status = 503


class TokenBucket:
    """Thread-safe token bucket used to smooth broker API bursts."""

    def __init__(self, rate: float, capacity: int) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens: float = float(capacity)
        self.last_refill = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> None:
        """Block until ``tokens`` are available, then consume them."""
        while True:
            with self._lock:
                now_ts = time.time()
                elapsed = now_ts - self.last_refill
                self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
                self.last_refill = now_ts
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
            time.sleep(0.01)

    def try_acquire(self, tokens: int = 1) -> bool:
        """Consume ``tokens`` without waiting; False when they are unavailable."""
        with self._lock:
            now_ts = time.time()
            elapsed = now_ts - self.last_refill
            self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
            self.last_refill = now_ts
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
        return False


class APIMetricsCollector:
    """Thread-safe counters for broker API calls, failures and cache use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)
        self._total_latency: dict[str, float] = defaultdict(float)
        self._circuit_trips = 0
        self._throttle_waits = 0
        self._cache_hits = 0
        self._cache_misses = 0

    def record_call(self, method: str, latency_s: float) -> None:
        with self._lock:
            self._calls[method] += 1
            self._total_latency[method] += latency_s

    def record_error(self, method: str) -> None:
        with self._lock:
            self._errors[method] += 1

    def record_circuit_trip(self) -> None:
        with self._lock:
            self._circuit_trips += 1

    def record_throttle_wait(self) -> None:
        with self._lock:
            self._throttle_waits += 1

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a stable copy of the current counters."""
        with self._lock:
            return {
                "calls": dict(self._calls),
                "errors": dict(self._errors),
                "avg_latency_ms": {
                    method: round(
                        self._total_latency[method] / max(self._calls[method], 1) * 1000,
                        1,
                    )
                    for method in self._calls
                },
                "circuit_trips": self._circuit_trips,
                "throttle_waits": self._throttle_waits,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
            }

    def log_summary(self, log: logging.Logger | None = None) -> None:
        """Log the same counters the reference reported through its LogChannel."""
        target = log or logger
        snap = self.snapshot()
        target.info("[APIMetricsCollector] API METRICS SNAPSHOT")
        for method, count in sorted(snap["calls"].items()):
            errors = snap["errors"].get(method, 0)
            latency = snap["avg_latency_ms"].get(method, 0.0)
            target.info(
                "[APIMetricsCollector] %-30s calls=%d errors=%d avg=%.1fms",
                method,
                count,
                errors,
                latency,
            )
        target.info("[APIMetricsCollector] circuit_trips=%d", snap["circuit_trips"])
        target.info("[APIMetricsCollector] throttle_waits=%d", snap["throttle_waits"])
        target.info("[APIMetricsCollector] cache_hits=%d", snap["cache_hits"])
        target.info("[APIMetricsCollector] cache_misses=%d", snap["cache_misses"])


class CircuitBreakerState:
    """Circuit states used by the broker API governor."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Stop hammering an unhealthy broker API after repeated failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        metrics: APIMetricsCollector | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold
        self._metrics = metrics
        self._log = log
        self._state = CircuitBreakerState.CLOSED
        self._consecutive_fails = 0
        self._consecutive_ok = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def is_open(self) -> bool:
        """True while calls must be blocked; transitions OPEN -> HALF_OPEN."""
        with self._lock:
            if self._state == CircuitBreakerState.OPEN:
                if time.time() - self._opened_at >= self._recovery_timeout:
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._consecutive_ok = 0
                    self._log_message("CircuitBreaker -> HALF_OPEN (probing)")
                    return False
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_fails = 0
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._consecutive_ok += 1
                if self._consecutive_ok >= self._success_threshold:
                    self._state = CircuitBreakerState.CLOSED
                    self._log_message("CircuitBreaker -> CLOSED (recovered)")

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_fails += 1
            self._consecutive_ok = 0
            if (
                self._state in (CircuitBreakerState.CLOSED, CircuitBreakerState.HALF_OPEN)
                and self._consecutive_fails >= self._failure_threshold
            ):
                self._state = CircuitBreakerState.OPEN
                self._opened_at = time.time()
                if self._metrics is not None:
                    self._metrics.record_circuit_trip()
                self._log_message(
                    "CircuitBreaker -> OPEN "
                    f"(after {self._consecutive_fails} failures, "
                    f"recovery in {self._recovery_timeout}s)"
                )

    def _log_message(self, message: str) -> None:
        if self._log is not None:
            self._log.warning(message)


class _HistCacheKey:
    """Parameter identity for one historical candle request."""

    __slots__ = ("token", "from_date", "to_date", "interval", "_hash")

    def __init__(self, token: object, from_date: object, to_date: object, interval: str) -> None:
        self.token = str(token)
        self.from_date = str(from_date)[:16]
        self.to_date = str(to_date)[:16]
        self.interval = str(interval)
        self._hash = hash((self.token, self.from_date, self.to_date, self.interval))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _HistCacheKey):
            return NotImplemented
        return (
            self.token == other.token
            and self.from_date == other.from_date
            and self.to_date == other.to_date
            and self.interval == other.interval
        )

    def __hash__(self) -> int:
        return self._hash


class _InflightFetch:
    """One owner fetch plus the result/error every waiter must observe."""

    __slots__ = ("event", "result", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: list[Any] | None = None
        self.error: Exception | None = None


class HistoricalDataCache:
    """TTL cache with per-key in-flight coordination for historical reads."""

    def __init__(
        self,
        ttl_seconds: float = 60.0,
        max_entries: int = 512,
        metrics: APIMetricsCollector | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._metrics = metrics
        self._store: dict[_HistCacheKey, tuple[list[Any], float]] = {}
        self._inflight: dict[_HistCacheKey, _InflightFetch] = {}
        self._lock = threading.Lock()

    def get(
        self, token: object, from_date: object, to_date: object, interval: str
    ) -> list[Any] | None:
        key = _HistCacheKey(token, from_date, to_date, interval)
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and time.time() < entry[1]:
                if self._metrics is not None:
                    self._metrics.record_cache_hit()
                return entry[0]
        if self._metrics is not None:
            self._metrics.record_cache_miss()
        return None

    def begin_fetch(
        self, token: object, from_date: object, to_date: object, interval: str
    ) -> tuple[bool, _InflightFetch | None]:
        """Return (owner?, ticket). A non-owner waits on the ticket event."""
        key = _HistCacheKey(token, from_date, to_date, interval)
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and time.time() < entry[1]:
                if self._metrics is not None:
                    self._metrics.record_cache_hit()
                return False, None
            ticket = self._inflight.get(key)
            if ticket is not None:
                return False, ticket
            ticket = _InflightFetch()
            self._inflight[key] = ticket
            if self._metrics is not None:
                self._metrics.record_cache_miss()
            return True, ticket

    def put(
        self,
        token: object,
        from_date: object,
        to_date: object,
        interval: str,
        data: list[Any],
        fetch_event: threading.Event,
        error: Exception | None = None,
    ) -> None:
        """Publish the owner's result (or error) to waiters and the TTL store."""
        key = _HistCacheKey(token, from_date, to_date, interval)
        with self._lock:
            ticket = self._inflight.pop(key, None)
            if ticket is not None:
                ticket.result = list(data)
                ticket.error = error
            if error is None and data:
                self._store[key] = (list(data), time.time() + self._ttl)
                self._evict_if_needed()
        fetch_event.set()

    def _evict_if_needed(self) -> None:
        now_ts = time.time()
        expired = [key for key, (_, expiry) in self._store.items() if now_ts >= expiry]
        for key in expired:
            del self._store[key]
        while len(self._store) > self._max_entries:
            oldest = next(iter(self._store))
            del self._store[oldest]

    def invalidate(self, token: object) -> None:
        """Drop cached candle pages for one instrument."""
        token_text = str(token)
        with self._lock:
            keys = [key for key in self._store if key.token == token_text]
            for key in keys:
                del self._store[key]

    def size(self) -> int:
        with self._lock:
            return len(self._store)


class APIGovernor:
    """Common control layer for every eligible broker API call.

    Two circuits, because "the venue is unhealthy for reads" and "the venue
    is unhealthy for orders" have different safe reactions:

    ``read_circuit``
        Opened by failing READ calls. While open, reads are refused (the
        venue already said no) and NEW ENTRIES are refused (the account truth
        cannot be trusted). Risk-reducing work is still attempted.
    ``order_circuit``
        Opened by failing order calls (entries and risk-reducing alike).
        While open, NEW ENTRIES are refused — a venue that just rejected
        orders must not be handed more exposure. Risk-reducing work is still
        attempted, because a stop that cannot be sent is the worse outcome.

    Every call — including risk-reducing ones — still consumes a token from
    the shared bucket, so nothing here can be used to hammer the venue.
    """

    def __init__(self, log: logging.Logger | None = None) -> None:
        self.metrics = APIMetricsCollector()
        self.bucket = TokenBucket(rate=2.5, capacity=3)
        self._log = log
        self.read_circuit = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            metrics=self.metrics,
            log=log,
        )
        self.order_circuit = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            metrics=self.metrics,
            log=log,
        )
        self.hist_cache = HistoricalDataCache(
            ttl_seconds=60.0,
            max_entries=512,
            metrics=self.metrics,
        )

    def is_blocked(self, call_class: CallClass) -> bool:
        """True when a call of this class must not reach the venue."""
        if call_class is CallClass.RISK_REDUCING:
            return False
        if self.read_circuit.is_open():
            return True
        return call_class is CallClass.ENTRY and self.order_circuit.is_open()

    def call(
        self,
        method_name: str,
        func: Callable[..., Any],
        *args: Any,
        call_class: CallClass = CallClass.READ,
        bypass_circuit: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Run one eligible call through the circuit, bucket and metrics.

        ``bypass_circuit`` is reserved for a read performed ON BEHALF OF a
        risk-reducing operation (the duplicate-order check of a protective
        stop). The read is still rate-limited and still fails loudly; it is
        only not refused by an already-open read circuit, so the protective
        order it guards is not blocked by the health of the read path.
        """
        if not bypass_circuit and self.is_blocked(call_class):
            self.metrics.record_error(method_name)
            raise CircuitOpenError(method_name, call_class)
        self.bucket.acquire()
        # Kept from the reference: every governed call counts as a throttle
        # wait, not only calls that actually slept.
        self.metrics.record_throttle_wait()
        started = time.time()
        try:
            result = func(*args, **kwargs)
            self._record_success(call_class)
            return result
        except Exception:
            self._record_failure(call_class)
            self.metrics.record_error(method_name)
            raise
        finally:
            self.metrics.record_call(method_name, time.time() - started)

    def _record_success(self, call_class: CallClass) -> None:
        if call_class is CallClass.READ:
            self.read_circuit.record_success()
        else:
            self.order_circuit.record_success()

    def _record_failure(self, call_class: CallClass) -> None:
        if call_class is CallClass.READ:
            self.read_circuit.record_failure()
        else:
            self.order_circuit.record_failure()

    def historical(
        self,
        fetch_func: Callable[..., list[_T]],
        token: object,
        from_date: object,
        to_date: object,
        interval: str,
    ) -> list[_T]:
        """Return cached candles or let exactly one caller fetch them."""
        cached = self.hist_cache.get(token, from_date, to_date, interval)
        if cached is not None:
            return list(cached)

        should_fetch, ticket = self.hist_cache.begin_fetch(token, from_date, to_date, interval)
        if not should_fetch:
            if ticket is None:
                # begin_fetch only returns no ticket when the cache entry was
                # valid, so a miss here means it was invalidated mid-flight.
                raise RuntimeError("historical cache entry disappeared during fetch coordination")
            ticket.event.wait(timeout=30.0)
            if not ticket.event.is_set():
                raise TimeoutError("historical fetch did not complete within 30s")
            if ticket.error is not None:
                raise ticket.error
            return list(ticket.result or [])

        data: list[Any] = []
        assert ticket is not None  # owner path always gets a ticket
        try:
            data = self.call(
                "historical_data",
                fetch_func,
                token,
                from_date,
                to_date,
                interval,
            )
        except Exception as exc:
            self.hist_cache.put(
                token,
                from_date,
                to_date,
                interval,
                [],
                ticket.event,
                error=exc,
            )
            raise
        self.hist_cache.put(
            token,
            from_date,
            to_date,
            interval,
            data,
            ticket.event,
        )
        return list(data)


class BrokerGateway:
    """Route eligible broker calls through the shared governor.

    The reference held the Kite client here. VAYREN's authenticated adapter
    already owns transport, auth and broker mapping, so this facade only keeps
    the control-flow property that made the reference safe: callers cannot
    bypass the governor, and order mutations never enter the historical cache.
    """

    def __init__(self, governor: APIGovernor) -> None:
        self._governor = governor

    @property
    def governor(self) -> APIGovernor:
        return self._governor

    def call(
        self,
        method_name: str,
        func: Callable[..., Any],
        *args: Any,
        call_class: CallClass = CallClass.READ,
        bypass_circuit: bool = False,
        **kwargs: Any,
    ) -> Any:
        return self._governor.call(
            method_name,
            func,
            *args,
            call_class=call_class,
            bypass_circuit=bypass_circuit,
            **kwargs,
        )

    def historical(
        self,
        fetch_func: Callable[..., list[_T]],
        token: object,
        from_date: object,
        to_date: object,
        interval: str,
    ) -> list[_T]:
        return self._governor.historical(fetch_func, token, from_date, to_date, interval)


__all__ = [
    "APIGovernor",
    "APIMetricsCollector",
    "BrokerGateway",
    "CallClass",
    "CircuitBreaker",
    "CircuitBreakerState",
    "CircuitOpenError",
    "HistoricalDataCache",
    "TokenBucket",
]
