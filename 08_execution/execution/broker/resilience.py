"""Venue-interaction safety: retry classification, rate limiting, clock drift.

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

from collections import deque
from dataclasses import dataclass
from enum import Enum


class RetryKind(Enum):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    NOT_SAFE_TO_RETRY = "NOT_SAFE_TO_RETRY"
    REQUIRES_RECONCILIATION = "REQUIRES_RECONCILIATION"
    # M8 production-adapter vocabulary alias: unknown/timeout submissions
    # must reconcile first — never blindly resubmit (same member, same
    # value; ``is`` comparison with REQUIRES_RECONCILIATION holds).
    MUST_RECONCILE_FIRST = "REQUIRES_RECONCILIATION"


# Operation kinds with a known-safe classification. Anything absent is
# NOT_SAFE_TO_RETRY by default (fail-closed classification).
_RETRY_TABLE: dict[str, RetryKind] = {
    "health": RetryKind.SAFE_TO_RETRY,
    "account": RetryKind.SAFE_TO_RETRY,
    "positions": RetryKind.SAFE_TO_RETRY,
    "open_orders": RetryKind.SAFE_TO_RETRY,
    "stream_poll": RetryKind.SAFE_TO_RETRY,
    "place_order": RetryKind.NOT_SAFE_TO_RETRY,
    "cancel_order": RetryKind.REQUIRES_RECONCILIATION,
    "modify_order": RetryKind.REQUIRES_RECONCILIATION,
    "settle": RetryKind.REQUIRES_RECONCILIATION,
}


def classify_retry(operation: str) -> RetryKind:
    """Classify one venue operation. Unknown operations are NOT safe."""
    return _RETRY_TABLE.get(operation, RetryKind.NOT_SAFE_TO_RETRY)


@dataclass
class RateLimiter:
    """Sliding-window client-side throttle. No broker values invented.

    ``max_requests``/``window_seconds`` come from venue documentation or
    explicit configuration. Exhaustion returns False (caller backs off);
    it never raises and never retries by itself.
    """

    max_requests: int = 100
    window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._hits: deque[float] = deque()
        self.rejections_429 = 0

    def allow(self, now_epoch: float) -> bool:
        """Consume one quota unit if available within the window."""
        cutoff = now_epoch - self.window_seconds
        while self._hits and self._hits[0] <= cutoff:
            self._hits.popleft()
        if len(self._hits) >= self.max_requests:
            return False
        self._hits.append(now_epoch)
        return True

    def record_429(self) -> None:
        """Record a venue rate-limit response (diagnostic counter only)."""
        self.rejections_429 += 1

    @property
    def used(self) -> int:
        return len(self._hits)


def clock_drift_ok(
    local_epoch: float, reference_epoch: float, max_drift_seconds: float = 5.0
) -> bool:
    """True when local clock is within threshold of the reference.

    Both epochs must share a basis (caller constructs both). Unusable
    inputs fail closed. Never fabricates or adjusts timestamps.
    """
    try:
        local = float(local_epoch)
        reference = float(reference_epoch)
    except (TypeError, ValueError):
        return False
    if max_drift_seconds < 0:
        return False
    return abs(local - reference) <= max_drift_seconds


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
        for name in ("connect_seconds", "read_seconds", "submit_seconds", "reconcile_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


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
        if self.base_seconds <= 0 or self.factor < 1.0 or self.max_seconds <= 0:
            raise ValueError("backoff requires positive base/max and factor >= 1")
        if self.max_attempts < 1:
            raise ValueError("backoff max_attempts must be >= 1")

    def delay(self, attempt: int) -> float:
        """Delay before attempt N (1-indexed); attempt 0 → base. Capped."""
        if attempt < 1:
            return self.base_seconds
        return min(self.max_seconds, self.base_seconds * (self.factor ** (attempt - 1)))

    def exhausted(self, attempts_made: int) -> bool:
        """True when no further attempts are allowed."""
        return attempts_made >= self.max_attempts


@dataclass(frozen=True)
class ReconnectPolicy:
    """Bounded reconnect discipline (FINAL §M). No blind reconnect storm:
    attempts are capped and spaced by :class:`BackoffPolicy`; after
    exhaustion the venue stays DISCONNECTED until an operator acts, and
    every UNKNOWN order reconciles before new submissions."""

    backoff: BackoffPolicy = BackoffPolicy()
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("reconnect max_attempts must be >= 1")

    def exhausted(self, attempts_made: int) -> bool:
        return attempts_made >= self.max_attempts
