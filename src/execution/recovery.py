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

import logging
import time
from collections import deque
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from execution.broker.resilience import BackoffPolicy
from execution.engine import ExecutionEngine, IllegalTransitionError
from execution.events import CandleEvent
from execution.journal import ExecutionJournal
from execution.models.intent import StrategySignal
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
from market.models.bar import Bar
from market.native_timeframe import seconds_of

logger = logging.getLogger(__name__)


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


#: Venue wall clock (IST: UTC + 5:30).
VENUE_TZ = timezone(timedelta(hours=5, minutes=30))

RESOLUTION_BY_TIMEFRAME: dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "20m": "20",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "D",
    "D": "D",
}


def _format_stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def is_trading_day(dt: date | datetime, holidays: Collection[str] = ()) -> bool:
    """Check if the given date is an active trading day (Mon-Fri, non-holiday)."""
    d = dt.date() if isinstance(dt, datetime) else dt
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    if holidays:
        iso = d.isoformat()
        return iso not in holidays
    return True


def evaluate_recovered_signal(
    signal: StrategySignal,
    *,
    now_dt: datetime,
    current_quote: float | None = None,
    subsequent_bars: Sequence[Bar] = (),
    existing_positions: Sequence[Any] = (),
    existing_orders: Sequence[Any] = (),
    exit_time_cutoff: tuple[int, int] = (15, 15),
    max_slippage_pct: float = 2.0,
) -> tuple[bool, str]:
    """Check if a signal that occurred while the engine was offline is still valid.

    Checks:
    1. Existing Position / Duplicate:
       - If position already open or order already submitted today -> DUPLICATE_PREVENTED.
    2. Session Time Cutoff:
       - If current time >= 15:15 IST (exit time) -> SESSION_TIME_EXIT_PASSED.
    3. Stop Loss Breached:
       - If subsequent bars touched SL, or current market price breached SL -> STOP_LOSS_BREACHED.
    4. Adverse Price Drift:
       - If current price has moved excessively far against entry -> ADVERSE_PRICE_DRIFT.

    Returns (is_valid, reason).
    """
    now = now_dt.replace(tzinfo=VENUE_TZ) if now_dt.tzinfo is None else now_dt.astimezone(VENUE_TZ)

    # 1. Existing position check
    for pos in existing_positions:
        sym = getattr(pos, "symbol", "") or (pos.get("symbol") if isinstance(pos, dict) else "")
        qty = getattr(pos, "quantity", 0) or (
            pos.get("quantity", 0) if isinstance(pos, dict) else 0
        )
        if sym == signal.symbol and qty != 0:
            return False, f"POSITION_ALREADY_EXISTS: position of {qty} exists for {signal.symbol}"

    # 2. Existing order check (idempotency)
    for ord_item in existing_orders:
        ord_sym = getattr(ord_item, "symbol", "") or (
            ord_item.get("symbol") if isinstance(ord_item, dict) else ""
        )
        ord_side = str(
            getattr(ord_item, "side", "")
            or (ord_item.get("side", "") if isinstance(ord_item, dict) else "")
        ).upper()
        if ord_sym == signal.symbol and ord_side == str(signal.side).upper():
            return (
                False,
                f"ORDER_ALREADY_SUBMITTED: {signal.side} order already placed for {signal.symbol}",
            )

    # 3. Session time cutoff
    cutoff_h, cutoff_m = exit_time_cutoff
    if now.hour > cutoff_h or (now.hour == cutoff_h and now.minute >= cutoff_m):
        return (
            False,
            f"SESSION_TIME_EXIT_PASSED: {now.strftime('%H:%M')} >= {cutoff_h:02d}:{cutoff_m:02d}",
        )

    side = str(signal.side).upper()
    entry_price = float(signal.price)
    stop_loss = float(signal.stop_loss) if signal.stop_loss is not None else None

    # 4. Stop Loss breached check
    if stop_loss is not None and stop_loss > 0:
        if side == "SELL":
            # Short position: stop loss is above entry price.
            for bar in subsequent_bars:
                if bar.high >= stop_loss:
                    return (
                        False,
                        f"STOP_LOSS_BREACHED: bar high {bar.high:.2f} >= SL {stop_loss:.2f}",
                    )
            if current_quote is not None and current_quote >= stop_loss:
                return (
                    False,
                    f"STOP_LOSS_BREACHED: quote {current_quote:.2f} >= SL {stop_loss:.2f}",
                )
        elif side == "BUY":
            # Long position: stop loss is below entry price.
            for bar in subsequent_bars:
                if bar.low <= stop_loss:
                    return (
                        False,
                        f"STOP_LOSS_BREACHED: bar low {bar.low:.2f} <= SL {stop_loss:.2f}",
                    )
            if current_quote is not None and current_quote <= stop_loss:
                return (
                    False,
                    f"STOP_LOSS_BREACHED: quote {current_quote:.2f} <= SL {stop_loss:.2f}",
                )

    # 5. Adverse price drift check
    if current_quote is not None and entry_price > 0:
        if side == "SELL":
            drift_pct = (current_quote - entry_price) / entry_price * 100.0
            if drift_pct > max_slippage_pct:
                return (
                    False,
                    f"ADVERSE_PRICE_DRIFT: rose {drift_pct:.2f}% "
                    f"({current_quote:.2f} vs {entry_price:.2f})",
                )
        elif side == "BUY":
            drift_pct = (entry_price - current_quote) / entry_price * 100.0
            if drift_pct > max_slippage_pct:
                return (
                    False,
                    f"ADVERSE_PRICE_DRIFT: fell {drift_pct:.2f}% "
                    f"({current_quote:.2f} vs {entry_price:.2f})",
                )

    return True, "VALID"


def backfill_candle_gap(
    adapter: Any,
    symbol: str,
    timeframe: str,
    last_seen_stamp: str,
    now_dt: datetime | None = None,
) -> tuple[CandleEvent, ...]:
    """Backfill missing completed candles between last_seen_stamp and current time.

    Used when WebSocket reconnects after a disconnect interval during market hours.
    """
    raw_now = now_dt if now_dt is not None else datetime.now(VENUE_TZ)
    now = (
        raw_now.replace(tzinfo=VENUE_TZ) if raw_now.tzinfo is None else raw_now.astimezone(VENUE_TZ)
    )

    if not is_trading_day(now):
        return ()

    try:
        last_dt = datetime.strptime(last_seen_stamp[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=VENUE_TZ
        )
    except Exception:
        return ()

    tf_sec = int(seconds_of(timeframe) or 1800)
    step = timedelta(seconds=float(tf_sec))
    expected_start = last_dt + step

    expected_intervals: list[tuple[datetime, datetime]] = []
    curr = expected_start
    while curr < now:
        end_val = curr + step
        if end_val <= now:
            expected_intervals.append((curr, end_val))
        else:
            break
        curr = end_val

    if not expected_intervals:
        return ()

    resolution = RESOLUTION_BY_TIMEFRAME.get(str(timeframe).lower(), str(timeframe))
    from_epoch = int(expected_intervals[0][0].timestamp())
    to_epoch = int(now.timestamp())

    try:
        raw_rows = adapter.history(symbol, resolution, from_epoch, to_epoch)
    except Exception as exc:
        logger.warning("Failed to fetch backfill candles for %s: %s", symbol, exc)
        return ()

    events: list[CandleEvent] = []
    for row in raw_rows if isinstance(raw_rows, (list, tuple)) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            epoch = int(row[0])
            o, h, low, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            v = int(float(row[5]))
        except (TypeError, ValueError):
            continue

        candle_dt = datetime.fromtimestamp(epoch, VENUE_TZ)
        if candle_dt <= last_dt or (epoch + tf_sec) > to_epoch:
            continue

        events.append(
            CandleEvent(
                symbol=symbol,
                timestamp=_format_stamp(candle_dt),
                seq=0,
                source="backfill_history",
                open=o,
                high=h,
                low=low,
                close=c,
                volume=v,
                timeframe=timeframe,
                is_closed=True,
            )
        )

    events.sort(key=lambda e: (e.timestamp, e.symbol))
    return tuple(events)


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
    "VENUE_TZ",
    "RESOLUTION_BY_TIMEFRAME",
    "backfill_candle_gap",
    "evaluate_recovered_signal",
    "is_trading_day",
]
