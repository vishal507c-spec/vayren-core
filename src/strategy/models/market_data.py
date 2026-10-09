"""Normalized market-data contract + router configuration (Phase 4).

``NormalizedMarketData`` is the ONLY tick shape the strategy layer ever
sees: canonical identity plus price/volume/quote facts plus timing. It
carries no provider raw payloads, no SDK objects, no vendor ids — the
provider name rides along for diagnostics only and never as identity.

``RouterConfig`` makes provider priority explicit and testable (primary +
ordered secondaries, freshness threshold, recovery window). Nothing here
is hard-wired into strategy code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Provider health states (provider level and subscription level).
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
STALE = "STALE"
DISCONNECTED = "DISCONNECTED"
ERROR = "ERROR"
DISABLED = "DISABLED"

#: Per-instrument data readiness (what strategy may ask about).
READY = "READY"
WAITING_FOR_DATA = "WAITING_FOR_DATA"
FAILOVER = "FAILOVER"
UNAVAILABLE = "UNAVAILABLE"
READINESS_ERROR = "ERROR"

#: Tick-level data status (readiness lives one level up, per instrument).
TICK_OK = "OK"

#: Structured routing event names (bounded in-router journal).
PROVIDER_CONNECTED = "PROVIDER_CONNECTED"
PROVIDER_DISCONNECTED = "PROVIDER_DISCONNECTED"
SUBSCRIPTION_STARTED = "SUBSCRIPTION_STARTED"
SUBSCRIPTION_CONFIRMED = "SUBSCRIPTION_CONFIRMED"
DATA_RECEIVED = "DATA_RECEIVED"
DATA_STALE = "DATA_STALE"
FAILOVER_STARTED = "FAILOVER_STARTED"
FAILOVER_COMPLETED = "FAILOVER_COMPLETED"
PRIMARY_RECOVERED = "PRIMARY_RECOVERED"
FAILBACK_COMPLETED = "FAILBACK_COMPLETED"
NO_DATA_AVAILABLE = "NO_DATA_AVAILABLE"


def _require_iso(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"NormalizedMarketData {label} must be ISO-8601, got {value!r}")
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"NormalizedMarketData {label} must be ISO-8601, got {value!r}") from exc
    return value


@dataclass(frozen=True)
class NormalizedMarketData:
    """One provider-independent market-data tick for a canonical instrument.

    Attributes:
        instrument_id: Stable ``NSE:EQUITY:*`` identity (never changes on
            failover — data continuity is the point).
        exchange/segment/symbol: Canonical identity facts (display-safe).
        event_time: Provider-stamped event time (ISO-8601; future stamps
            are clamped to ``received_time`` by the router, never trusted).
        received_time: Router receipt time (ISO-8601).
        last_price: Last traded (or mid/quote-derived) price, must be > 0.
        volume: Traded volume when the provider reports it, else 0.0.
        bid/ask: Best quote legs when reported, else None (absent, never
            invented as zero).
        provider: Source provider name (diagnostics only, not identity).
        provider_seq: Provider sequence when supplied, else None (never
            invented — dedup only applies when present).
        router_seq: Router-local monotonic sequence per instrument.
        data_status: Tick status (``OK`` in Phase 4 scope).
    """

    instrument_id: str
    exchange: str
    segment: str
    symbol: str
    event_time: str
    received_time: str
    last_price: float
    volume: float = 0.0
    bid: float | None = None
    ask: float | None = None
    provider: str = ""
    provider_seq: int | None = None
    router_seq: int = 0
    data_status: str = TICK_OK

    def __post_init__(self) -> None:
        """Reject empty identity, non-positive prices, bad timestamps."""
        for field_name in ("instrument_id", "exchange", "segment", "symbol", "provider"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"NormalizedMarketData {field_name} must be non-empty")
        _require_iso("event_time", self.event_time)
        _require_iso("received_time", self.received_time)
        price = self.last_price
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            raise ValueError(f"NormalizedMarketData last_price must be > 0, got {price!r}")
        if self.provider_seq is not None and (
            isinstance(self.provider_seq, bool)
            or not isinstance(self.provider_seq, int)
            or self.provider_seq < 0
        ):
            raise ValueError(
                f"NormalizedMarketData provider_seq must be >= 0 or None, got {self.provider_seq!r}"
            )

    @property
    def display(self) -> str:
        """User-facing identity (``NSE:RELIANCE``) — never a provider id."""
        return f"{self.exchange}:{self.symbol}"


@dataclass(frozen=True)
class RouterConfig:
    """Explicit, testable routing policy (no hard-wired priority).

    Attributes:
        primary: First-choice provider name (``FYERS``).
        secondaries: Ordered fallback providers (``("ZERODHA",)``).
        freshness_threshold_s: Tick age beyond which data is STALE and a
            failover candidate (must be > 0; strategy-agnostic transport
            bound, not a strategy threshold).
        recovery_window_s: How long the primary must stay continuously
            healthy before failback (anti-flap hysteresis, must be > 0).
    """

    primary: str = "FYERS"
    secondaries: tuple[str, ...] = ("ZERODHA",)
    freshness_threshold_s: float = 5.0
    recovery_window_s: float = 10.0

    def __post_init__(self) -> None:
        """Reject empty/duplicate providers and non-positive windows."""
        primary = str(self.primary or "").strip().upper()
        if not primary:
            raise ValueError("RouterConfig primary must be non-empty")
        object.__setattr__(self, "primary", primary)
        secondaries = tuple(str(s or "").strip().upper() for s in self.secondaries)
        if any(not s for s in secondaries):
            raise ValueError("RouterConfig secondaries must be non-empty names")
        if primary in secondaries or len(set(secondaries)) != len(secondaries):
            raise ValueError("RouterConfig providers must be distinct")
        object.__setattr__(self, "secondaries", secondaries)
        for field_name in ("freshness_threshold_s", "recovery_window_s"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"RouterConfig {field_name} must be > 0, got {value!r}")

    @property
    def ordered_providers(self) -> tuple[str, ...]:
        """Primary first, then secondaries — the only priority order."""
        return (self.primary, *self.secondaries)


__all__ = [
    "NormalizedMarketData",
    "RouterConfig",
    "HEALTHY",
    "DEGRADED",
    "STALE",
    "DISCONNECTED",
    "ERROR",
    "DISABLED",
    "READY",
    "WAITING_FOR_DATA",
    "FAILOVER",
    "UNAVAILABLE",
    "READINESS_ERROR",
    "TICK_OK",
    "PROVIDER_CONNECTED",
    "PROVIDER_DISCONNECTED",
    "SUBSCRIPTION_STARTED",
    "SUBSCRIPTION_CONFIRMED",
    "DATA_RECEIVED",
    "DATA_STALE",
    "FAILOVER_STARTED",
    "FAILOVER_COMPLETED",
    "PRIMARY_RECOVERED",
    "FAILBACK_COMPLETED",
    "NO_DATA_AVAILABLE",
]
