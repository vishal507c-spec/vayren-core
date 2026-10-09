"""Unified eligibility models — ONE authoritative readiness vocabulary (Phase 6).

``EligibilityVerdict`` is the single answer to "can this instrument generate
a NEW order right now?" — composed from subsystem truth, never invented.
Every check carries a stable machine-readable code, an explicit severity
(BLOCKING vs WARNING), and the subsystem that produced it, so diagnostics
can explain the verdict instead of paraphrasing free text.

Deliberately NOT here (future phases): per-order risk math, broker
routing, market-data freshness calculation, any audit product.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

# ── severities (explicit, never implicit) ──────────────────────────
BLOCKING = "BLOCKING"
WARNING = "WARNING"

# ── instrument readiness levels (verdict.level) ────────────────────
READY = "READY"
WAITING = "WAITING"
BLOCKED = "BLOCKED"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"
RISK_BLOCKED = "RISK_BLOCKED"
EXECUTION_BLOCKED = "EXECUTION_BLOCKED"
DISABLED = "DISABLED"
ERROR = "ERROR"
DEGRADED = "DEGRADED"  # allowed, with warnings

# ── evaluation modes ───────────────────────────────────────────────
FAIL_FAST = "FAIL_FAST"
DIAGNOSTIC = "DIAGNOSTIC"

# ── system summary levels ──────────────────────────────────────────
SYSTEM_READY = "READY"
SYSTEM_NOT_READY = "NOT READY"
SYSTEM_DEGRADED = "DEGRADED"
TRADING_ALLOWED = "TRADING_ALLOWED"
TRADING_BLOCKED = "TRADING_BLOCKED"

# ── stable reason codes (machine-readable, UI translates) ─────────
STRATEGY_NOT_SELECTED = "STRATEGY_NOT_SELECTED"
STRATEGY_DISABLED = "STRATEGY_DISABLED"
STRATEGY_NOT_CONFIGURED = "STRATEGY_NOT_CONFIGURED"
UNIVERSE_EMPTY = "UNIVERSE_EMPTY"
STRATEGY_MEMBERSHIP_MISSING = "STRATEGY_MEMBERSHIP_MISSING"
INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"
INSTRUMENT_INACTIVE = "INSTRUMENT_INACTIVE"
MARKET_DATA_WAITING = "MARKET_DATA_WAITING"
MARKET_DATA_STALE = "MARKET_DATA_STALE"
MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
PROVIDER_MAPPING_MISSING = "PROVIDER_MAPPING_MISSING"
BROKER_MAPPING_MISSING = "BROKER_MAPPING_MISSING"
BROKER_MAPPING_DISABLED = "BROKER_MAPPING_DISABLED"
BROKER_MAPPING_CONFLICT = "BROKER_MAPPING_CONFLICT"
BROKER_NOT_READY = "BROKER_NOT_READY"
BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
RISK_NOT_READY = "RISK_NOT_READY"
RISK_DENIED = "RISK_DENIED"
DUPLICATE_ORDER = "DUPLICATE_ORDER"
RECONCILIATION_UNHEALTHY = "RECONCILIATION_UNHEALTHY"
SESSION_NOT_RUNNING = "SESSION_NOT_RUNNING"
LIVE_NOT_ARMED = "LIVE_NOT_ARMED"
ORDER_STREAM_UNHEALTHY = "ORDER_STREAM_UNHEALTHY"
STOP_PROTECTION_ATTENTION = "STOP_PROTECTION_ATTENTION"
SAFETY_BLOCKER = "SAFETY_BLOCKER"
EXECUTION_UNAVAILABLE = "EXECUTION_UNAVAILABLE"
EXECUTION_FALLBACK_ACTIVE = "EXECUTION_FALLBACK_ACTIVE"
SYSTEM_MODE_INVALID = "SYSTEM_MODE_INVALID"
UNKNOWN_STATE = "UNKNOWN_STATE"

# ── transition events (bounded journal) ────────────────────────────
EV_SYSTEM_READY = "SYSTEM_READY"
EV_SYSTEM_BLOCKED = "SYSTEM_BLOCKED"
EV_STRATEGY_READY = "STRATEGY_READY"
EV_STRATEGY_BLOCKED = "STRATEGY_BLOCKED"
EV_INSTRUMENT_READY = "INSTRUMENT_READY"
EV_INSTRUMENT_BLOCKED = "INSTRUMENT_BLOCKED"
EV_INSTRUMENT_STALE = "INSTRUMENT_STALE"
EV_INSTRUMENT_RECOVERED = "INSTRUMENT_RECOVERED"
EV_TRADING_ALLOWED = "TRADING_ALLOWED"
EV_TRADING_BLOCKED = "TRADING_BLOCKED"


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


@dataclass(frozen=True)
class EligibilityCheck:
    """One gate result: code + explicit severity + producing subsystem.

    Attributes:
        code: Stable machine-readable reason code (empty when passing).
        status: ``READY`` | ``BLOCKED`` | ``WARNING``.
        severity: ``BLOCKING`` prevents new orders; ``WARNING`` does not.
        message: Human explanation (diagnostics only).
        source: Subsystem that produced the check (SYSTEM/SESSION/
            STRATEGY/UNIVERSE/INSTRUMENT/MARKET_DATA/RISK/EXECUTION/
            SAFETY).
    """

    code: str
    status: str
    severity: str
    message: str
    source: str

    @property
    def passed(self) -> bool:
        """True when the check does not block new orders."""
        return self.severity == WARNING or self.status == READY


@dataclass(frozen=True)
class EligibilityVerdict:
    """The single authoritative answer for one instrument + strategy.

    Attributes:
        allowed: True only for READY/DEGRADED — a mandatory blocker can
            never leave this True.
        level: Instrument readiness level (READY/WAITING/BLOCKED/STALE/
            UNAVAILABLE/RISK_BLOCKED/EXECUTION_BLOCKED/DISABLED/ERROR/
            DEGRADED).
        instrument_id: Canonical ``NSE:EQUITY:*`` identity (stable across
            provider and broker changes).
        strategy_id: Owning strategy (isolation key).
        checks: All evaluated checks in deterministic gate order.
        blocking_reasons: Blocking codes in evaluation order.
        warnings: Warning codes (never block).
        evaluated_at: ISO-8601 UTC evaluation stamp.
    """

    allowed: bool
    level: str
    instrument_id: str
    strategy_id: str
    checks: tuple[EligibilityCheck, ...]
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    evaluated_at: str = ""

    def __post_init__(self) -> None:
        """Fill the evaluation stamp (frozen, once)."""
        if not self.evaluated_at:
            object.__setattr__(self, "evaluated_at", _utcnow_iso())


__all__ = [
    "EligibilityCheck",
    "EligibilityVerdict",
    "BLOCKING",
    "WARNING",
    "READY",
    "WAITING",
    "BLOCKED",
    "STALE",
    "UNAVAILABLE",
    "RISK_BLOCKED",
    "EXECUTION_BLOCKED",
    "DISABLED",
    "ERROR",
    "DEGRADED",
    "FAIL_FAST",
    "DIAGNOSTIC",
    "SYSTEM_READY",
    "SYSTEM_NOT_READY",
    "SYSTEM_DEGRADED",
    "TRADING_ALLOWED",
    "TRADING_BLOCKED",
    "STRATEGY_NOT_SELECTED",
    "STRATEGY_DISABLED",
    "STRATEGY_NOT_CONFIGURED",
    "UNIVERSE_EMPTY",
    "STRATEGY_MEMBERSHIP_MISSING",
    "INSTRUMENT_NOT_FOUND",
    "INSTRUMENT_INACTIVE",
    "MARKET_DATA_WAITING",
    "MARKET_DATA_STALE",
    "MARKET_DATA_UNAVAILABLE",
    "PROVIDER_MAPPING_MISSING",
    "BROKER_MAPPING_MISSING",
    "BROKER_MAPPING_DISABLED",
    "BROKER_MAPPING_CONFLICT",
    "BROKER_NOT_READY",
    "BROKER_UNAVAILABLE",
    "RISK_NOT_READY",
    "RISK_DENIED",
    "DUPLICATE_ORDER",
    "RECONCILIATION_UNHEALTHY",
    "SESSION_NOT_RUNNING",
    "LIVE_NOT_ARMED",
    "ORDER_STREAM_UNHEALTHY",
    "STOP_PROTECTION_ATTENTION",
    "SAFETY_BLOCKER",
    "EXECUTION_UNAVAILABLE",
    "SYSTEM_MODE_INVALID",
    "UNKNOWN_STATE",
    "EV_SYSTEM_READY",
    "EV_SYSTEM_BLOCKED",
    "EV_STRATEGY_READY",
    "EV_STRATEGY_BLOCKED",
    "EV_INSTRUMENT_READY",
    "EV_INSTRUMENT_BLOCKED",
    "EV_INSTRUMENT_STALE",
    "EV_INSTRUMENT_RECOVERED",
    "EV_TRADING_ALLOWED",
    "EV_TRADING_BLOCKED",
]
