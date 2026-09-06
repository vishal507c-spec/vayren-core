"""Venue-interaction safety: retry classification, rate limiting, clock drift.

No broker-specific values live here. Rate limits are caller-configured
(client-side only); clock checks compare caller-supplied epochs. All
classifications fail closed: unknown operations are NOT safe to retry.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum


class RetryKind(Enum):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    NOT_SAFE_TO_RETRY = "NOT_SAFE_TO_RETRY"
    REQUIRES_RECONCILIATION = "REQUIRES_RECONCILIATION"


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
