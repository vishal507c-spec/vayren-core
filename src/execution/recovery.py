"""Self-managing EC2 execution engine — recovery, resilience, state machine & watchdog.

VAYREN Phase 2 — Self-Managing EC2 Execution Engine:
- Central execution state machine (STARTING, RECOVERING, READY, RUNNING, DEGRADED,
  RECONCILIATION_REQUIRED, BLOCKED, STOPPED)
- Broker auto-recovery with bounded exponential backoff & auth failure segregation
- Market WebSocket disconnect/stale/duplicate detection and subscription restoration
- Order WebSocket disconnect/duplicate detection and REST reconciliation triggers
- Process crash/restart recovery with UNKNOWN order protection & zero duplicate submissions
- Heartbeat monitor and watchdog with bounded retry loop protection (anti-storm)
- APK/EXE client disconnect safety (EC2 continues running; fresh snapshot on reconnect)
- Recovery audit events (RECOVERY_STARTED, BROKER_DISCONNECTED, etc.)
- Zero secrets/tokens/credentials logged or exposed.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from execution.broker.resilience import BackoffPolicy
from execution.engine import ExecutionEngine, IllegalTransitionError
from execution.journal import ExecutionJournal
from execution.models.order import OrderState
from execution.portfolio.ledger import PositionLedger
from execution.portfolio.reconcile import (
    ReconciliationReport,
    ReconciliationState,
    reconcile_funds,
    reconcile_orders,
    reconcile_positions,
)
from execution.safety import (
    SafetySnapshot,
    SlProtectionTracker,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Execution State Machine (§4, §10) ──────────────────────────────────────


class ExecutionState(StrEnum):
    """Authoritative central execution lifecycle states.

    STARTING: Initial process boot.
    RECOVERING: Post-restart state restoration in-flight.
    READY: All checks/verifications green; ready for orders.
    RUNNING: Actively processing market data and strategy intents.
    DEGRADED: Component failure/stale data; LIVE orders strictly BLOCKED.
    RECONCILIATION_REQUIRED: Position/order/fund mismatch; orders BLOCKED.
    BLOCKED: Hard halt (watchdog threshold reached / kill switch / auth failed).
    STOPPED: Orderly shutdown completed.
    """

    STARTING = "STARTING"
    RECOVERING = "RECOVERING"
    READY = "READY"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILING = "RECONCILING"
    BLOCKED = "BLOCKED"
    STOPPED = "STOPPED"


_VALID_EXECUTION_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.STARTING: frozenset(
        {
            ExecutionState.RECOVERING,
            ExecutionState.RECONCILING,
            ExecutionState.READY,
            ExecutionState.DEGRADED,
            ExecutionState.STOPPED,
            ExecutionState.BLOCKED,
        }
    ),
    ExecutionState.RECOVERING: frozenset(
        {
            ExecutionState.READY,
            ExecutionState.RECONCILIATION_REQUIRED,
            ExecutionState.RECONCILING,
            ExecutionState.DEGRADED,
            ExecutionState.BLOCKED,
            ExecutionState.STOPPED,
        }
    ),
    ExecutionState.READY: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.DEGRADED,
            ExecutionState.RECONCILIATION_REQUIRED,
            ExecutionState.RECONCILING,
            ExecutionState.BLOCKED,
            ExecutionState.STOPPED,
        }
    ),
    ExecutionState.RUNNING: frozenset(
        {
            ExecutionState.DEGRADED,
            ExecutionState.RECONCILIATION_REQUIRED,
            ExecutionState.RECONCILING,
            ExecutionState.BLOCKED,
            ExecutionState.STOPPED,
        }
    ),
    ExecutionState.DEGRADED: frozenset(
        {
            ExecutionState.RECOVERING,
            ExecutionState.RECONCILING,
            ExecutionState.READY,
            ExecutionState.RECONCILIATION_REQUIRED,
            ExecutionState.BLOCKED,
            ExecutionState.STOPPED,
        }
    ),
    ExecutionState.RECONCILIATION_REQUIRED: frozenset(
        {
            ExecutionState.RECONCILING,
            ExecutionState.RECOVERING,
            ExecutionState.READY,
            ExecutionState.BLOCKED,
            ExecutionState.STOPPED,
        }
    ),
    ExecutionState.RECONCILING: frozenset(
        {
            ExecutionState.READY,
            ExecutionState.RECONCILIATION_REQUIRED,
            ExecutionState.DEGRADED,
            ExecutionState.BLOCKED,
            ExecutionState.STOPPED,
        }
    ),
    ExecutionState.BLOCKED: frozenset(
        {
            ExecutionState.RECOVERING,
            ExecutionState.RECONCILING,
            ExecutionState.STOPPED,
        }
    ),
    ExecutionState.STOPPED: frozenset(
        {
            ExecutionState.STARTING,
            ExecutionState.RECOVERING,
            ExecutionState.RECONCILING,
        }
    ),
}


class ExecutionStateMachine:
    """Enforces strict execution lifecycle transitions. Never fails open."""

    def __init__(self, initial: ExecutionState = ExecutionState.STARTING) -> None:
        self._state = initial
        self._reason = ""
        self._updated_at = _now_iso()

    @property
    def state(self) -> ExecutionState:
        return self._state

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def updated_at(self) -> str:
        return self._updated_at

    def transition(self, target: ExecutionState, reason: str = "") -> ExecutionState:
        if target == self._state:
            self._reason = reason or self._reason
            self._updated_at = _now_iso()
            return self._state
        allowed = _VALID_EXECUTION_TRANSITIONS.get(self._state, frozenset())
        if target not in allowed:
            msg = (
                f"illegal execution state transition: "
                f"{self._state.value} -> {target.value} ({reason})"
            )
            raise IllegalTransitionError(msg)
        self._state = target
        self._reason = reason
        self._updated_at = _now_iso()
        return self._state

    @property
    def allows_live_trading(self) -> bool:
        """Only RUNNING state permits submitting live orders."""
        return self._state == ExecutionState.RUNNING


# ── Broker Connection Recovery (§1) ────────────────────────────────────────


class BrokerFailureKind(StrEnum):
    NETWORK = "NETWORK"
    AUTHENTICATION = "AUTHENTICATION"
    VENUE_ERROR = "VENUE_ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


@dataclass
class BrokerRecoveryManager:
    """Monitors broker connection, detects disconnects, and manages bounded reconnects.

    Discipline:
    - Distinguishes authentication failure (BLOCKED, no retry storm) vs network.
    - Uses BackoffPolicy for deterministic bounded exponential backoff.
    - Reconnect success alone does NOT authorize LIVE trading — full post-reconnect
      refresh (funds, orders, positions, reconciliation, SL protection) is mandatory.
    """

    backoff: BackoffPolicy = field(
        default_factory=lambda: BackoffPolicy(
            base_seconds=1.0, factor=2.0, max_seconds=15.0, max_attempts=4
        )
    )
    _connected: bool = False
    _attempts: int = 0
    _last_error: str = ""
    _last_failure_kind: BrokerFailureKind = BrokerFailureKind.UNKNOWN
    _last_reconnect_time: str = ""

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def last_failure_kind(self) -> BrokerFailureKind:
        return self._last_failure_kind

    def on_disconnect(
        self, reason: str, kind: BrokerFailureKind = BrokerFailureKind.NETWORK
    ) -> None:
        self._connected = False
        self._last_error = reason
        self._last_failure_kind = kind

    def can_retry(self) -> bool:
        # Authentication failures MUST NOT enter retry storms
        if self._last_failure_kind == BrokerFailureKind.AUTHENTICATION:
            return False
        return not self.backoff.exhausted(self._attempts)

    def next_delay(self) -> float:
        return self.backoff.delay(self._attempts + 1)

    def record_attempt(self) -> int:
        self._attempts += 1
        return self._attempts

    def on_reconnect_success(self) -> None:
        self._connected = True
        self._attempts = 0
        self._last_error = ""
        self._last_failure_kind = BrokerFailureKind.UNKNOWN
        self._last_reconnect_time = _now_iso()

    def reset(self) -> None:
        self._attempts = 0
        self._last_error = ""


# ── Market WebSocket Recovery (§2) ─────────────────────────────────────────


@dataclass
class MarketWsRecovery:
    """Monitors market data WebSocket connection and detects stale/duplicate ticks.

    Restores subscriptions on reconnect. Any tick age > max_age_seconds blocks LIVE.
    """

    max_age_seconds: float = 15.0
    _connected: bool = False
    _subscriptions: set[str] = field(default_factory=set)
    _seen_tick_ids: deque[str] = field(default_factory=lambda: deque(maxlen=2000))
    _last_tick_epoch: float | None = None
    _last_tick_iso: str = ""
    _duplicates_filtered: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_tick_epoch(self) -> float | None:
        return self._last_tick_epoch

    @property
    def last_tick_iso(self) -> str:
        return self._last_tick_iso

    @property
    def subscriptions(self) -> tuple[str, ...]:
        return tuple(sorted(self._subscriptions))

    def on_connect(self) -> None:
        self._connected = True

    def on_disconnect(self) -> None:
        self._connected = False

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        self._subscriptions.update(symbols)

    def restore_subscriptions(self) -> tuple[str, ...]:
        return tuple(sorted(self._subscriptions))

    def observe_tick(self, symbol: str, timestamp_epoch: float, tick_id: str = "") -> bool:
        """Observe tick. Returns True if valid/fresh, False if duplicate or stale."""
        now = time.time()
        # Stale check
        if timestamp_epoch > 0 and (now - timestamp_epoch) > self.max_age_seconds:
            return False
        # Deduplication check
        effective_id = tick_id or f"{symbol}:{timestamp_epoch}"
        if effective_id in self._seen_tick_ids:
            self._duplicates_filtered += 1
            return False
        self._seen_tick_ids.append(effective_id)
        self._last_tick_epoch = timestamp_epoch
        self._last_tick_iso = _now_iso()
        return True

    def is_fresh(self, current_epoch: float | None = None) -> bool:
        if self._last_tick_epoch is None:
            return False
        now = current_epoch if current_epoch is not None else time.time()
        return (now - self._last_tick_epoch) <= self.max_age_seconds


# ── Order / Trade WebSocket Recovery (§3) ──────────────────────────────────


@dataclass
class OrderWsRecovery:
    """Monitors order/trade WebSocket, deduplicates events, and triggers REST reconciliation."""

    _connected: bool = False
    _seen_event_ids: deque[str] = field(default_factory=lambda: deque(maxlen=2000))
    _duplicates_filtered: int = 0
    _last_event_iso: str = ""
    _missed_event_detected: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_event_iso(self) -> str:
        return self._last_event_iso

    @property
    def missed_event_detected(self) -> bool:
        return self._missed_event_detected

    def on_connect(self) -> None:
        self._connected = True
        self._missed_event_detected = False

    def on_disconnect(self) -> None:
        self._connected = False
        # Stream disconnect means events might have occurred during the outage
        self._missed_event_detected = True

    def observe_event(self, event_id: str) -> bool:
        """Returns True if event is new, False if duplicate."""
        if not event_id:
            return True
        if event_id in self._seen_event_ids:
            self._duplicates_filtered += 1
            return False
        self._seen_event_ids.append(event_id)
        self._last_event_iso = _now_iso()
        return True

    def clear_missed_event_flag(self) -> None:
        self._missed_event_detected = False


# ── Heartbeat Monitor (§9) ─────────────────────────────────────────────────


@dataclass
class HeartbeatMonitor:
    """Monitors components to ensure stale connections never masquerade as HEALTHY."""

    stale_threshold_seconds: float = 30.0
    _last_heartbeat_epoch: float = field(default_factory=time.time)
    _last_heartbeat_iso: str = field(default_factory=_now_iso)

    def ping(self) -> None:
        self._last_heartbeat_epoch = time.time()
        self._last_heartbeat_iso = _now_iso()

    @property
    def last_heartbeat_iso(self) -> str:
        return self._last_heartbeat_iso

    def is_alive(self, current_epoch: float | None = None) -> bool:
        now = current_epoch if current_epoch is not None else time.time()
        return (now - self._last_heartbeat_epoch) <= self.stale_threshold_seconds


# ── Watchdog (§10) ─────────────────────────────────────────────────────────


@dataclass
class ExecutionWatchdog:
    """Prevents infinite recovery retry storms. Bounded attempts trip to BLOCKED."""

    max_recovery_attempts: int = 3
    _attempts: int = 0
    _tripped: bool = False
    _tripped_reason: str = ""

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def tripped_reason(self) -> str:
        return self._tripped_reason

    def record_failure(self, reason: str) -> bool:
        """Record a failure. Returns True if watchdog tripped (max attempts reached)."""
        self._attempts += 1
        if self._attempts >= self.max_recovery_attempts:
            self._tripped = True
            self._tripped_reason = (
                f"watchdog threshold ({self.max_recovery_attempts}) exceeded: {reason}"
            )
            return True
        return False

    def reset(self) -> None:
        self._attempts = 0
        self._tripped = False
        self._tripped_reason = ""


# ── Remote Client Tracker (§8) ─────────────────────────────────────────────


@dataclass
class RemoteClientTracker:
    """Tracks APK/EXE connection status.

    Crucial safety guarantee: APK disconnect never stops EC2 trading engine
    nor bypasses safety. Upon reconnect, the fresh authoritative state is served.
    """

    _connected: bool = False
    _last_seen_iso: str = ""
    _disconnect_count: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def disconnect_count(self) -> int:
        return self._disconnect_count

    def on_client_connect(self) -> None:
        self._connected = True
        self._last_seen_iso = _now_iso()

    def on_client_disconnect(self) -> None:
        self._connected = False
        self._disconnect_count += 1
        self._last_seen_iso = _now_iso()


# ── Full Recovery Coordinator (§5, §6, §7) ─────────────────────────────────


class RecoveryCoordinator:
    """Coordinates automatic state recovery, crash safety, and duplicate prevention.

    Flow:
    START/RESTART
      ↓
    Mark state RECOVERING (journal RECOVERY_STARTED)
      ↓
    Restore engine from checkpoint (preserve intent_ids, client_order_ids, fills)
      ↓
    Verify/reconnect broker
      ↓
    Refresh broker truth (funds, open orders, positions)
      ↓
    Restore market and order WS subscriptions
      ↓
    Run reconciliation against broker snapshot
      ↓
    If mismatch -> RECONCILIATION_REQUIRED (LIVE blocked)
      ↓
    Verify stop-loss protection on all open positions
      ↓
    Verify risk and safety gates
      ↓
    Only then -> READY (journal RECOVERY_COMPLETED)
    """

    def __init__(
        self,
        journal: ExecutionJournal | None = None,
        max_recovery_attempts: int = 3,
    ) -> None:
        self.state_machine = ExecutionStateMachine(ExecutionState.STARTING)
        self.broker_recovery = BrokerRecoveryManager()
        self.market_ws = MarketWsRecovery()
        self.order_ws = OrderWsRecovery()
        self.heartbeat = HeartbeatMonitor()
        self.watchdog = ExecutionWatchdog(max_recovery_attempts=max_recovery_attempts)
        self.client_tracker = RemoteClientTracker()
        self.journal = journal or ExecutionJournal()
        self.sl_tracker = SlProtectionTracker()
        self._last_reconciliation_iso: str = ""
        self._last_recovery_error: str = ""

    def start_recovery(self, reason: str = "process start/recovery") -> None:
        """Begin recovery flow. Transitions to RECOVERING."""
        if self.state_machine.state in (
            ExecutionState.STARTING,
            ExecutionState.DEGRADED,
            ExecutionState.RECONCILIATION_REQUIRED,
            ExecutionState.STOPPED,
        ):
            self.state_machine.transition(ExecutionState.RECOVERING, reason)
        self.journal.record("RECOVERY_STARTED", reason=reason)

    def restore_checkpoint(
        self,
        engine: ExecutionEngine,
        checkpoint: dict[str, Any],
    ) -> tuple[int, int]:
        """Restore previous order intents and orders from checkpoint.

        Returns (restored_orders_count, restored_positions_count).
        UNKNOWN orders remain UNKNOWN and can only exit via reconcile.
        """
        engine_snap = checkpoint.get("engine", {})
        if engine_snap:
            engine.restore(engine_snap)
        orders_count = len(engine._orders)
        self.journal.record(
            "CHECKPOINT_RESTORED",
            orders=orders_count,
            unknown_orders=len(
                [o for o in engine._orders.values() if o.state == OrderState.UNKNOWN]
            ),
        )
        return orders_count, len(checkpoint.get("positions", []))

    def reconcile_with_broker(
        self,
        ledger: PositionLedger,
        engine: ExecutionEngine,
        broker_positions: list[dict[str, Any]],
        broker_open_orders: list[str],
        broker_funds: dict[str, Any] | None = None,
    ) -> ReconciliationState:
        """Full reconciliation against broker truth.

        Mismatch transitions state to RECONCILIATION_REQUIRED.
        """
        self.journal.record("RECONCILIATION_STARTED")
        local_positions = tuple(ledger.all_positions())
        local_orders = tuple(o.client_order_id for o in engine.open_orders())

        positions_report = reconcile_positions(local_positions, broker_positions)
        orders_report = reconcile_orders(local_orders, tuple(broker_open_orders))
        funds_report = ReconciliationReport(matched=True)
        if broker_funds:
            snapshot = ledger.snapshot()
            funds_report = reconcile_funds(snapshot.equity, broker_funds)

        state = ReconciliationState(
            positions=positions_report,
            orders=orders_report,
            funds=funds_report,
        )
        self._last_reconciliation_iso = _now_iso()
        verdict = state.verdict()

        self.journal.record(
            "RECONCILIATION_COMPLETED",
            matched=not state.blocks_live,
            status=verdict.status.value,
            reasons=list(verdict.reasons),
        )

        if state.blocks_live:
            self.state_machine.transition(
                ExecutionState.RECONCILIATION_REQUIRED,
                reason="; ".join(verdict.reasons) or "reconciliation mismatch",
            )
        return state

    def verify_and_complete(
        self,
        reconciliation_state: ReconciliationState,
        sl_tracker: SlProtectionTracker,
        gates_satisfied: bool = True,
    ) -> bool:
        """Final verification step before marking READY.

        Requires:
        1. Reconciliation is SAFE
        2. SL protection has no attention/failures
        3. Safety gates are satisfied
        """
        if reconciliation_state.blocks_live:
            self._last_recovery_error = "reconciliation blocks live"
            self.fail_recovery("reconciliation mismatch")
            return False

        if sl_tracker.has_attention:
            self._last_recovery_error = "stop loss protection needs attention"
            self.state_machine.transition(
                ExecutionState.DEGRADED, reason="unprotected positions detected"
            )
            self.journal.record("RECOVERY_FAILED", reason=self._last_recovery_error)
            return False

        if not gates_satisfied:
            self._last_recovery_error = "safety gates not fully satisfied"
            self.state_machine.transition(
                ExecutionState.DEGRADED, reason="live gates not satisfied"
            )
            self.journal.record("RECOVERY_FAILED", reason=self._last_recovery_error)
            return False

        # All checks passed -> READY
        self.state_machine.transition(ExecutionState.READY, reason="recovery complete and verified")
        self.journal.record("RECOVERY_COMPLETED")
        self.watchdog.reset()
        self._last_recovery_error = ""
        return True

    def fail_recovery(self, reason: str) -> None:
        """Record recovery failure and check watchdog threshold."""
        self._last_recovery_error = reason
        self.journal.record("RECOVERY_FAILED", reason=reason)
        tripped = self.watchdog.record_failure(reason)
        if tripped:
            self.state_machine.transition(
                ExecutionState.BLOCKED, reason=self.watchdog.tripped_reason
            )
            self.journal.record("SYSTEM_BLOCKED", reason=self.watchdog.tripped_reason)
        elif self.state_machine.state not in (
            ExecutionState.RECONCILIATION_REQUIRED,
            ExecutionState.BLOCKED,
        ):
            self.state_machine.transition(ExecutionState.DEGRADED, reason=reason)

    def build_snapshot(
        self,
        base_snapshot: SafetySnapshot,
    ) -> SafetySnapshot:
        """Enrich a base SafetySnapshot with Phase 2 self-managing fields (additive)."""
        broker_conn = "CONNECTED" if self.broker_recovery.connected else "DISCONNECTED"
        market_conn = "CONNECTED" if self.market_ws.connected else "DISCONNECTED"
        order_conn = "CONNECTED" if self.order_ws.connected else "DISCONNECTED"
        rec_state = self.state_machine.state.value

        return SafetySnapshot(
            overall=base_snapshot.overall,
            last_block_reason=base_snapshot.last_block_reason,
            evaluated_at=base_snapshot.evaluated_at,
            kill_switch_engaged=base_snapshot.kill_switch_engaged,
            kill_switch_reason=base_snapshot.kill_switch_reason,
            kill_switch_level=base_snapshot.kill_switch_level,
            live_trading_enabled=base_snapshot.live_trading_enabled,
            broker_live_enabled=base_snapshot.broker_live_enabled,
            account_confirmed=base_snapshot.account_confirmed,
            risk_limits_valid=base_snapshot.risk_limits_valid,
            kill_switch_off=base_snapshot.kill_switch_off,
            broker_connected=base_snapshot.broker_connected,
            broker_name=base_snapshot.broker_name,
            broker_environment=base_snapshot.broker_environment,
            broker_health_reason=base_snapshot.broker_health_reason,
            reconciliation_healthy=base_snapshot.reconciliation_healthy,
            reconciliation_status=base_snapshot.reconciliation_status,
            reconciliation_reasons=base_snapshot.reconciliation_reasons,
            risk_ready=base_snapshot.risk_ready,
            capital_valid=base_snapshot.capital_valid,
            last_risk_denial_reason=base_snapshot.last_risk_denial_reason,
            sl_protection_summary=base_snapshot.sl_protection_summary,
            sl_attention_count=base_snapshot.sl_attention_count,
            sl_records=base_snapshot.sl_records,
            armed=base_snapshot.armed,
            session_lifecycle=base_snapshot.session_lifecycle,
            # Phase 2 additive fields
            execution_state=self.state_machine.state.value,
            broker_connection=broker_conn,
            market_ws=market_conn,
            order_ws=order_conn,
            recovery_state=rec_state,
            last_heartbeat=self.heartbeat.last_heartbeat_iso,
            last_market_tick=self.market_ws.last_tick_iso,
            last_broker_event=self.order_ws.last_event_iso,
            last_reconciliation=self._last_reconciliation_iso,
            recovery_attempts=self.watchdog.attempts,
            last_recovery_error=self._last_recovery_error,
        )


__all__ = [
    "ExecutionState",
    "ExecutionStateMachine",
    "BrokerFailureKind",
    "BrokerRecoveryManager",
    "MarketWsRecovery",
    "OrderWsRecovery",
    "HeartbeatMonitor",
    "ExecutionWatchdog",
    "RemoteClientTracker",
    "RecoveryCoordinator",
]
