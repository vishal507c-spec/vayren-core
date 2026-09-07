"""UBL broker health model — generic connection health (M8 §12).

Health is CONNECTION health only. It never implies LIVE readiness —
LIVE readiness stays exclusively with the five live gates
(``execution.broker.gates.GATE_NAMES``) plus explicit arming.

States are generic; adapter-specific health strings must be translated
to :class:`HealthState` at the adapter edge and must never enter core.
Legacy ``(bool, reason)`` health tuples are preserved through
:meth:`BrokerHealth.to_legacy` / :func:`health_from_legacy` so existing
callers keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HealthState(StrEnum):
    """Generic broker connection states (no adapter-specific values)."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BrokerHealth:
    """Immutable connection-health snapshot."""

    state: HealthState
    reason: str = ""
    timestamp: str | None = None
    latency_ms: float | None = None
    last_heartbeat: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, HealthState):
            raise ValueError(f"health state must be a HealthState, got {self.state!r}")
        if not isinstance(self.reason, str):
            raise ValueError(f"health reason must be a string, got {self.reason!r}")
        if self.latency_ms is not None and (
            not isinstance(self.latency_ms, (int, float)) or self.latency_ms < 0
        ):
            raise ValueError(f"health latency_ms must be non-negative, got {self.latency_ms!r}")

    @property
    def connected(self) -> bool:
        """True only for CONNECTED (DEGRADED is usable-but-impaired)."""
        return self.state is HealthState.CONNECTED

    def to_legacy(self) -> tuple[bool, str]:
        """Compatibility view: the historical ``(healthy, reason)`` tuple."""
        return self.connected, self.reason or self.state.value


def health_from_legacy(healthy: bool, reason: str = "") -> BrokerHealth:
    """Translate a legacy ``(healthy, reason)`` tuple into the generic model.

    Mapping is honest: ``True`` → CONNECTED, ``False`` → DISCONNECTED.
    Finer states (DEGRADED, AUTH_*, RATE_LIMITED) are only produced by
    adapters that genuinely observe them — never inferred here.
    """
    state = HealthState.CONNECTED if healthy else HealthState.DISCONNECTED
    return BrokerHealth(state=state, reason=reason or state.value)


__all__ = [
    "BrokerHealth",
    "HealthState",
    "health_from_legacy",
]
