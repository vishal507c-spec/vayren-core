"""Venue-interaction safety: retry classification, rate limiting, clock drift.

The rules live in Rust (`rust/vayren-core/src/resilience.rs`); this module is
the vocabulary + handle facade over :mod:`execution.broker.native_policy`. It
holds no table, no window, no comparison of its own.

No broker-specific values live here. Rate limits are caller-configured
(client-side only); clock checks compare caller-supplied epochs. All
classifications fail closed: unknown operations are NOT safe to retry.

Production-adapter policy (M8 §13) — conceptual binding, no auto-retries:

- timeout / disconnect / reconnect → surface as UNKNOWN order state, then
  reconcile (``MUST_RECONCILE_FIRST``); correctness > speed.
- rate limiting → ``RateLimiter`` client-side throttle + ``RATE_LIMITED``
  health; callers back off, never spin.
- stale market data / broker outage → ``StreamNormalizer`` staleness +
  ``BrokerHealth`` DEGRADED/DISCONNECTED; orders stay blocked until fresh.
- unknown submission → reconcile first, never blind-resubmit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from execution.broker.native_policy import (
    NativeLimiter,
    native_backoff_delay,
    native_backoff_problem,
    native_clock_ok,
    native_exhausted,
    native_limiter_problem,
    native_reconnect_problem,
    native_retry_kind,
    native_timeout_problem,
)


class RetryKind(Enum):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    NOT_SAFE_TO_RETRY = "NOT_SAFE_TO_RETRY"
    REQUIRES_RECONCILIATION = "REQUIRES_RECONCILIATION"
    # M8 production-adapter vocabulary alias: unknown/timeout submissions
    # must reconcile first — never blindly resubmit (same member, same
    # value; ``is`` comparison with REQUIRES_RECONCILIATION holds).
    MUST_RECONCILE_FIRST = "REQUIRES_RECONCILIATION"


_RETRY_BY_CODE = (
    RetryKind.SAFE_TO_RETRY,
    RetryKind.NOT_SAFE_TO_RETRY,
    RetryKind.REQUIRES_RECONCILIATION,
)


def classify_retry(operation: str) -> RetryKind:
    """Classify one venue operation. Unknown operations are NOT safe."""
    return _RETRY_BY_CODE[native_retry_kind(operation)]


@dataclass
class RateLimiter:
    """Sliding-window client-side throttle. No broker values invented.

    ``max_requests``/``window_seconds`` come from venue documentation or
    explicit configuration. Exhaustion returns False (caller backs off);
    it never raises and never retries by itself. The window itself is the
    kernel's — this object only carries the configuration and the handle.
    """

    max_requests: int = 100
    window_seconds: float = 60.0

    def __post_init__(self) -> None:
        problem = native_limiter_problem(self.max_requests, self.window_seconds)
        if problem:
            raise ValueError(problem)
        self._limiter = NativeLimiter(self.max_requests, self.window_seconds)

    def allow(self, now_epoch: float) -> bool:
        """Consume one quota unit if available within the window."""
        return self._limiter.allow(now_epoch)

    def record_429(self) -> None:
        """Record a venue rate-limit response (diagnostic counter only)."""
        self._limiter.record_429()

    @property
    def used(self) -> int:
        return self._limiter.used

    @property
    def rejections_429(self) -> int:
        return self._limiter.rejections


def clock_drift_ok(
    local_epoch: object, reference_epoch: object, max_drift_seconds: float = 5.0
) -> bool:
    """True when local clock is within threshold of the reference.

    Both epochs must share a basis (caller constructs both). Unusable
    inputs fail closed. Never fabricates or adjusts timestamps — the only
    thing decided here is whether the boundary could read the numbers at all.
    """
    try:
        local = float(local_epoch)  # type: ignore[arg-type]
        reference = float(reference_epoch)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return native_clock_ok(0.0, 0.0, 0.0, False)
    return native_clock_ok(local, reference, max_drift_seconds, True)


@dataclass
class ResilienceState:
    """Observable throttle/retry counters for UI and journal."""

    allowed: int = 0
    denied: int = 0
    reconciliations_required: int = 0
    rate_429s: int = 0


@dataclass(frozen=True)
class TimeoutPolicy:
    """Per-operation timeout budgets (FINAL §M). Policy only — no timers,
    no threads. Adapters enforce these around transport calls; expiry of
    an ORDER-timeout submission means UNKNOWN → reconcile first."""

    connect_seconds: float = 10.0
    read_seconds: float = 5.0
    submit_seconds: float = 10.0
    reconcile_seconds: float = 30.0

    def __post_init__(self) -> None:
        problem = native_timeout_problem(
            self.connect_seconds, self.read_seconds, self.submit_seconds, self.reconcile_seconds
        )
        if problem:
            raise ValueError(problem)


@dataclass(frozen=True)
class BackoffPolicy:
    """Deterministic backoff between retries (FINAL §M). No retry storm:
    bounded attempts, capped delay, no jitter randomness (reproducible).
    Only SAFE_TO_RETRY operations may use this; order submissions never do.
    """

    base_seconds: float = 1.0
    factor: float = 2.0
    max_seconds: float = 30.0
    max_attempts: int = 5

    def __post_init__(self) -> None:
        problem = native_backoff_problem(
            self.base_seconds, self.factor, self.max_seconds, self.max_attempts
        )
        if problem:
            raise ValueError(problem)

    def delay(self, attempt: int) -> float:
        """Delay before attempt N (1-indexed); attempt 0 → base. Capped."""
        return native_backoff_delay(self.base_seconds, self.factor, self.max_seconds, attempt)

    def exhausted(self, attempts_made: int) -> bool:
        """True when no further attempts are allowed."""
        return native_exhausted(attempts_made, self.max_attempts)


@dataclass(frozen=True)
class ReconnectPolicy:
    """Bounded reconnect discipline (FINAL §M). No blind reconnect storm:
    attempts are capped and spaced by :class:`BackoffPolicy`; after
    exhaustion the venue stays DISCONNECTED until an operator acts, and
    every UNKNOWN order reconciles before new submissions."""

    backoff: BackoffPolicy = BackoffPolicy()
    max_attempts: int = 5

    def __post_init__(self) -> None:
        problem = native_reconnect_problem(self.max_attempts)
        if problem:
            raise ValueError(problem)

    def exhausted(self, attempts_made: int) -> bool:
        return native_exhausted(attempts_made, self.max_attempts)
