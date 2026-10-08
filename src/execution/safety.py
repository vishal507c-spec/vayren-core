"""Safety state model — structured snapshot for operational visibility.

Phase 1 — Safety Foundation: exposes the overall trading safety state in one
frozen snapshot. Consumed by LiveSession.state() and any remote observer
(APK/EXE remote dashboard, ops dashboard).

Design constraints (per Phase 1 spec):
- Never fail-open: every unknown / missing state → NOT_READY
- Secrets never appear in any field
- SL protection state tracked per-position (strategy-provided stop_price only)
- All gate names expose their current verdict and last reason
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ── SL Protection ─────────────────────────────────────────────────────────


class SlProtectionStatus(StrEnum):
    """SL protection status for a single filled position."""

    PENDING = "PENDING"  # fill received, SL placement in-flight
    PROTECTED = "PROTECTED"  # confirmed SL order live at broker
    SL_ATTENTION = "SL_ATTENTION"  # SL missing/failed — requires attention
    PROTECTION_FAILED = "PROTECTION_FAILED"  # SL placement confirmed failed
    NOT_REQUIRED = "NOT_REQUIRED"  # strategy did not supply a stop_price


@dataclass(frozen=True)
class SlProtectionRecord:
    """One stop-protection record for one filled intent.

    The execution layer NEVER invents stop prices. ``stop_price`` always comes
    from the strategy's own signal — this record only tracks that external
    stop's lifecycle, not any computed SL rule.
    """

    intent_id: str
    symbol: str
    side: str
    fill_price: float
    stop_price: float | None  # None = strategy provided no stop
    status: SlProtectionStatus
    sl_order_id: str | None = None  # broker SL order id once placed
    reason: str = ""
    updated_at: str = ""

    @classmethod
    def from_fill(
        cls,
        intent_id: str,
        symbol: str,
        side: str,
        fill_price: float,
        stop_price: float | None,
    ) -> SlProtectionRecord:
        """Create a PENDING record the moment a fill is confirmed."""
        status = (
            SlProtectionStatus.PENDING
            if stop_price is not None
            else SlProtectionStatus.NOT_REQUIRED
        )
        return cls(
            intent_id=intent_id,
            symbol=symbol,
            side=side,
            fill_price=fill_price,
            stop_price=stop_price,
            status=status,
            updated_at=_now_iso(),
        )

    def with_status(
        self, status: SlProtectionStatus, *, sl_order_id: str | None = None, reason: str = ""
    ) -> SlProtectionRecord:
        return SlProtectionRecord(
            intent_id=self.intent_id,
            symbol=self.symbol,
            side=self.side,
            fill_price=self.fill_price,
            stop_price=self.stop_price,
            status=status,
            sl_order_id=sl_order_id or self.sl_order_id,
            reason=reason,
            updated_at=_now_iso(),
        )

    @property
    def needs_attention(self) -> bool:
        return self.status in (
            SlProtectionStatus.SL_ATTENTION,
            SlProtectionStatus.PROTECTION_FAILED,
        )


# ── Overall Safety State ───────────────────────────────────────────────────


class TradingSafetyState(StrEnum):
    """Overall trading safety state — the single summary field for dashboards."""

    READY = "READY"  # all gates green, live possible
    DEGRADED = "DEGRADED"  # paper/sandbox only; not all live gates satisfied
    SL_ATTENTION = "SL_ATTENTION"  # live gate issues due to unprotected positions
    BLOCKED = "BLOCKED"  # reconciliation mismatch or kill switch — no new live orders
    KILL_SWITCH_ON = "KILL_SWITCH_ON"  # global kill switch engaged
    UNKNOWN = "UNKNOWN"  # crash/restart — reconcile before trading


# ── Safety Snapshot ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SafetySnapshot:
    """Immutable point-in-time safety snapshot.

    Produced by ``LiveSession.safety_snapshot()``; consumed by remote dashboards,
    APK/EXE state observers, and operational tooling. Every field is JSON-safe.
    Secrets never appear.
    """

    # summary
    overall: TradingSafetyState
    last_block_reason: str
    evaluated_at: str

    # kill switch
    kill_switch_engaged: bool
    kill_switch_reason: str
    kill_switch_level: str

    # live gates (vocabulary matches ModeGates field names)
    live_trading_enabled: bool
    broker_live_enabled: bool
    account_confirmed: bool
    risk_limits_valid: bool
    kill_switch_off: bool

    # broker health
    broker_connected: bool
    broker_name: str
    broker_environment: str
    broker_health_reason: str

    # reconciliation
    reconciliation_healthy: bool
    reconciliation_status: str  # SAFE | WARNING | BLOCKED
    reconciliation_reasons: tuple[str, ...]

    # risk readiness
    risk_ready: bool
    capital_valid: bool
    last_risk_denial_reason: str

    # SL protection
    sl_protection_summary: str  # CLEAN | SL_ATTENTION | PROTECTION_FAILED | N/A
    sl_attention_count: int
    sl_records: tuple[dict[str, Any], ...]

    # arming
    armed: str  # DISARMED | ARMING | ARMED | RUNNING | HALTED

    # lifecycle
    session_lifecycle: str

    # Phase 2 additive fields (§12, §13)
    execution_state: str = "READY"
    broker_connection: str = "CONNECTED"
    market_ws: str = "CONNECTED"
    order_ws: str = "CONNECTED"
    recovery_state: str = "IDLE"
    last_heartbeat: str = ""
    last_market_tick: str = ""
    last_broker_event: str = ""
    last_reconciliation: str = ""
    recovery_attempts: int = 0
    last_recovery_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict for serialisation (no frozen-tuple issues)."""
        return {
            "overall": self.overall.value,
            "last_block_reason": self.last_block_reason,
            "evaluated_at": self.evaluated_at,
            "kill_switch": {
                "engaged": self.kill_switch_engaged,
                "reason": self.kill_switch_reason,
                "level": self.kill_switch_level,
            },
            "live_gates": {
                "live_trading_enabled": self.live_trading_enabled,
                "broker_live_enabled": self.broker_live_enabled,
                "account_confirmed": self.account_confirmed,
                "risk_limits_valid": self.risk_limits_valid,
                "kill_switch_off": self.kill_switch_off,
            },
            "broker": {
                "connected": self.broker_connected,
                "name": self.broker_name,
                "environment": self.broker_environment,
                "health_reason": self.broker_health_reason,
            },
            "reconciliation": {
                "healthy": self.reconciliation_healthy,
                "status": self.reconciliation_status,
                "reasons": list(self.reconciliation_reasons),
            },
            "risk": {
                "ready": self.risk_ready,
                "capital_valid": self.capital_valid,
                "last_denial_reason": self.last_risk_denial_reason,
            },
            "sl_protection": {
                "summary": self.sl_protection_summary,
                "attention_count": self.sl_attention_count,
                "records": list(self.sl_records),
            },
            "armed": self.armed,
            "session_lifecycle": self.session_lifecycle,
            "execution_state": self.execution_state,
            "broker_connection": self.broker_connection,
            "market_ws": self.market_ws,
            "order_ws": self.order_ws,
            "recovery_state": self.recovery_state,
            "last_heartbeat": self.last_heartbeat,
            "last_market_tick": self.last_market_tick,
            "last_broker_event": self.last_broker_event,
            "last_reconciliation": self.last_reconciliation,
            "recovery_attempts": self.recovery_attempts,
            "last_recovery_error": self.last_recovery_error,
        }


# ── SL Protection Tracker ──────────────────────────────────────────────────


class SlProtectionTracker:
    """Tracks stop-loss protection records for filled positions.

    The tracker NEVER computes stop prices. It only records what the strategy
    provides (via stop_price on the signal) and whether the execution layer
    successfully placed the corresponding protective order at the broker.

    Per Phase 1 spec §5:
    - Strategy-provided stop_price is preserved through to the broker layer.
    - If SL placement fails, status → PROTECTION_FAILED; new trading may be
      blocked or flagged for operator attention.
    - Execution layer never invents or hardcodes SL rules.
    """

    def __init__(self) -> None:
        self._records: dict[str, SlProtectionRecord] = {}

    def on_fill(
        self,
        intent_id: str,
        symbol: str,
        side: str,
        fill_price: float,
        stop_price: float | None,
    ) -> SlProtectionRecord:
        """Create a protection record when a fill is confirmed."""
        record = SlProtectionRecord.from_fill(
            intent_id=intent_id,
            symbol=symbol,
            side=side,
            fill_price=fill_price,
            stop_price=stop_price,
        )
        self._records[intent_id] = record
        return record

    def confirm_sl(self, intent_id: str, sl_order_id: str) -> SlProtectionRecord | None:
        """Mark SL as PROTECTED once the broker confirms the SL order."""
        record = self._records.get(intent_id)
        if record is None:
            return None
        updated = record.with_status(
            SlProtectionStatus.PROTECTED, sl_order_id=sl_order_id, reason="broker confirmed SL"
        )
        self._records[intent_id] = updated
        return updated

    def mark_sl_attention(self, intent_id: str, reason: str) -> SlProtectionRecord | None:
        """Mark as SL_ATTENTION when strategy provided a stop but placement failed."""
        record = self._records.get(intent_id)
        if record is None:
            return None
        # Only escalate if a stop was expected
        status = (
            SlProtectionStatus.SL_ATTENTION
            if record.stop_price is not None
            else SlProtectionStatus.NOT_REQUIRED
        )
        updated = record.with_status(status, reason=reason)
        self._records[intent_id] = updated
        return updated

    def mark_protection_failed(self, intent_id: str, reason: str) -> SlProtectionRecord | None:
        """Mark as PROTECTION_FAILED when SL placement definitively failed."""
        record = self._records.get(intent_id)
        if record is None:
            return None
        if record.stop_price is None:
            return record  # no SL was expected, nothing to fail
        updated = record.with_status(SlProtectionStatus.PROTECTION_FAILED, reason=reason)
        self._records[intent_id] = updated
        return updated

    def close_position(self, intent_id: str) -> None:
        """Remove tracking when a position is fully closed."""
        self._records.pop(intent_id, None)

    @property
    def has_attention(self) -> bool:
        return any(r.needs_attention for r in self._records.values())

    @property
    def attention_count(self) -> int:
        return sum(1 for r in self._records.values() if r.needs_attention)

    def all_records(self) -> tuple[SlProtectionRecord, ...]:
        return tuple(self._records.values())

    def summary_status(self) -> str:
        """High-level summary for dashboard display."""
        if not self._records:
            return "N/A"
        failed = [
            r for r in self._records.values() if r.status == SlProtectionStatus.PROTECTION_FAILED
        ]
        attention = [
            r for r in self._records.values() if r.status == SlProtectionStatus.SL_ATTENTION
        ]
        if failed:
            return "PROTECTION_FAILED"
        if attention:
            return "SL_ATTENTION"
        return "CLEAN"


# ── Safety Snapshot Builder ────────────────────────────────────────────────


def build_safety_snapshot(
    *,
    kill_switch_engaged: bool,
    kill_switch_reason: str,
    kill_switch_level: str,
    gates_live_trading_enabled: bool,
    gates_broker_live_enabled: bool,
    gates_account_confirmed: bool,
    gates_risk_limits_valid: bool,
    gates_kill_switch_off: bool,
    broker_connected: bool,
    broker_name: str,
    broker_environment: str,
    broker_health_reason: str,
    reconciliation_healthy: bool,
    reconciliation_status: str,
    reconciliation_reasons: tuple[str, ...],
    risk_ready: bool,
    capital_valid: bool,
    last_risk_denial_reason: str,
    sl_tracker: SlProtectionTracker,
    armed: str,
    session_lifecycle: str,
    last_block_reason: str,
    execution_state: str = "READY",
    broker_connection: str = "CONNECTED",
    market_ws: str = "CONNECTED",
    order_ws: str = "CONNECTED",
    recovery_state: str = "IDLE",
    last_heartbeat: str = "",
    last_market_tick: str = "",
    last_broker_event: str = "",
    last_reconciliation: str = "",
    recovery_attempts: int = 0,
    last_recovery_error: str = "",
) -> SafetySnapshot:
    """Assemble a ``SafetySnapshot`` from individual safety state inputs.

    Computes ``overall`` deterministically from the inputs — any failure
    forces a non-READY state. The order of precedence:
    1. KILL_SWITCH_ON  (hardest stop)
    2. UNKNOWN         (restart/crash — unverified state)
    3. BLOCKED         (reconciliation mismatch)
    4. SL_ATTENTION    (unprotected live positions)
    5. DEGRADED        (not all live gates satisfied, but paper OK)
    6. READY           (all gates green, arming possible)
    """
    sl_summary = sl_tracker.summary_status()
    sl_records = tuple(
        {
            "intent_id": r.intent_id,
            "symbol": r.symbol,
            "side": r.side,
            "fill_price": r.fill_price,
            "stop_price": r.stop_price,
            "status": r.status.value,
            "sl_order_id": r.sl_order_id,
            "reason": r.reason,
        }
        for r in sl_tracker.all_records()
    )

    # Determine overall state
    if kill_switch_engaged:
        overall = TradingSafetyState.KILL_SWITCH_ON
    elif session_lifecycle in ("UNKNOWN", "RECOVERING") or execution_state in (
        "UNKNOWN",
        "RECOVERING",
    ):
        overall = TradingSafetyState.UNKNOWN
    elif not reconciliation_healthy:
        overall = TradingSafetyState.BLOCKED
    elif sl_tracker.has_attention:
        overall = TradingSafetyState.SL_ATTENTION
    elif not (
        gates_live_trading_enabled
        and gates_broker_live_enabled
        and gates_account_confirmed
        and gates_risk_limits_valid
        and gates_kill_switch_off
    ):
        overall = TradingSafetyState.DEGRADED
    else:
        overall = TradingSafetyState.READY

    return SafetySnapshot(
        overall=overall,
        last_block_reason=last_block_reason,
        evaluated_at=_now_iso(),
        kill_switch_engaged=kill_switch_engaged,
        kill_switch_reason=kill_switch_reason,
        kill_switch_level=kill_switch_level,
        live_trading_enabled=gates_live_trading_enabled,
        broker_live_enabled=gates_broker_live_enabled,
        account_confirmed=gates_account_confirmed,
        risk_limits_valid=gates_risk_limits_valid,
        kill_switch_off=gates_kill_switch_off,
        broker_connected=broker_connected,
        broker_name=broker_name,
        broker_environment=broker_environment,
        broker_health_reason=broker_health_reason,
        reconciliation_healthy=reconciliation_healthy,
        reconciliation_status=reconciliation_status,
        reconciliation_reasons=reconciliation_reasons,
        risk_ready=risk_ready,
        capital_valid=capital_valid,
        last_risk_denial_reason=last_risk_denial_reason,
        sl_protection_summary=sl_summary,
        sl_attention_count=sl_tracker.attention_count,
        sl_records=sl_records,
        armed=armed,
        session_lifecycle=session_lifecycle,
        execution_state=execution_state,
        broker_connection=broker_connection,
        market_ws=market_ws,
        order_ws=order_ws,
        recovery_state=recovery_state,
        last_heartbeat=last_heartbeat,
        last_market_tick=last_market_tick,
        last_broker_event=last_broker_event,
        last_reconciliation=last_reconciliation,
        recovery_attempts=recovery_attempts,
        last_recovery_error=last_recovery_error,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "SlProtectionStatus",
    "SlProtectionRecord",
    "SlProtectionTracker",
    "TradingSafetyState",
    "SafetySnapshot",
    "build_safety_snapshot",
]
