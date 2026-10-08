"""VAYREN Phase 2 — Self-Managing EC2 Execution Engine Tests.

Tests per Phase 2 specification (§14):
1.  Broker recovery: disconnect, reconnect success, reconnect failure, bounded retry,
    auth failure segregation
2.  Market WS recovery: disconnect, reconnect, subscription restore, stale data blocks,
    fresh data restores
3.  Order WS recovery: disconnect, reconnect, deduplication, missed event triggers
    reconciliation
4.  State machine: valid transitions, illegal transitions raise, live orders only in RUNNING
5.  Crash/restart recovery: checkpoint restore, UNKNOWN order recovery, partial fill recovery,
    SL verification, reconciliation before READY
6.  Duplicate safety: crash before ACK, crash after ACK, crash after partial fill,
    crash after full fill, recovery never creates duplicate orders
7.  APK disconnect safety: APK disconnect does not stop engine; reconnect receives
    authoritative fresh snapshot
8.  Heartbeat & watchdog: liveness/stale detection, bounded retries prevent infinite loop,
    threshold trips to BLOCKED
9.  State visibility: extended SafetySnapshot exposes all Phase 2 fields, zero secrets,
    backward compatible
"""

from __future__ import annotations

import time

import pytest

from execution.broker.resilience import BackoffPolicy
from execution.broker.sandbox import SandboxBroker
from execution.engine import ExecutionEngine, IllegalTransitionError
from execution.journal import ExecutionJournal
from execution.models.order import BrokerOrder, Fill, OrderState
from execution.portfolio.ledger import PositionLedger
from execution.recovery import (
    BrokerFailureKind,
    BrokerRecoveryManager,
    ExecutionState,
    ExecutionStateMachine,
    HeartbeatMonitor,
    MarketWsRecovery,
    OrderWsRecovery,
    RecoveryCoordinator,
)
from execution.runtime.session import LiveSession, SessionConfig
from execution.safety import (
    SlProtectionTracker,
    TradingSafetyState,
    build_safety_snapshot,
)

# ── 1. Broker Auto-Recovery Tests ──────────────────────────────────────────


def test_broker_disconnect_detected() -> None:
    """Disconnect is detected and connection status marked False."""
    manager = BrokerRecoveryManager()
    manager.on_reconnect_success()
    assert manager.connected is True
    manager.on_disconnect(reason="socket reset by peer", kind=BrokerFailureKind.NETWORK)
    assert manager.connected is False
    assert manager.last_error == "socket reset by peer"
    assert manager.last_failure_kind == BrokerFailureKind.NETWORK


def test_broker_reconnect_success() -> None:
    """Successful reconnect resets attempts and errors."""
    manager = BrokerRecoveryManager(backoff=BackoffPolicy(max_attempts=3))
    manager.on_disconnect("timeout", BrokerFailureKind.TIMEOUT)
    manager.record_attempt()
    assert manager.attempts == 1
    manager.on_reconnect_success()
    assert manager.connected is True
    assert manager.attempts == 0
    assert manager.last_error == ""


def test_broker_reconnect_failure_and_delay() -> None:
    """Failed reconnect increments attempts and computes bounded backoff delay."""
    manager = BrokerRecoveryManager(
        backoff=BackoffPolicy(base_seconds=1.0, factor=2.0, max_seconds=10.0, max_attempts=3)
    )
    manager.on_disconnect("connection refused", BrokerFailureKind.NETWORK)
    assert manager.can_retry() is True
    delay1 = manager.next_delay()
    assert delay1 == 1.0
    manager.record_attempt()

    delay2 = manager.next_delay()
    assert delay2 == 2.0
    manager.record_attempt()


def test_broker_bounded_retry_exhaustion() -> None:
    """After max_attempts, can_retry becomes False (anti-storm)."""
    manager = BrokerRecoveryManager(backoff=BackoffPolicy(max_attempts=2))
    manager.on_disconnect("net down", BrokerFailureKind.NETWORK)
    manager.record_attempt()
    assert manager.can_retry() is True
    manager.record_attempt()
    assert manager.can_retry() is False  # exhausted


def test_broker_auth_failure_handled_separately() -> None:
    """Authentication failure is segregated and immediately denies further retries."""
    manager = BrokerRecoveryManager(backoff=BackoffPolicy(max_attempts=5))
    manager.on_disconnect("invalid token / 401", kind=BrokerFailureKind.AUTHENTICATION)
    assert manager.last_failure_kind == BrokerFailureKind.AUTHENTICATION
    # Must NOT enter retry loops on auth failure
    assert manager.can_retry() is False


def test_broker_reconnect_alone_does_not_permit_live() -> None:
    """Broker reconnecting does NOT authorize LIVE orders without full verification."""
    coordinator = RecoveryCoordinator()
    coordinator.start_recovery("network glitch")
    coordinator.broker_recovery.on_reconnect_success()
    # State remains RECOVERING until full verification passes
    assert coordinator.state_machine.state == ExecutionState.RECOVERING
    assert coordinator.state_machine.allows_live_trading is False


# ── 2. Market WebSocket Recovery Tests ────────────────────────────────────


def test_market_ws_disconnect_detected() -> None:
    """Market WS disconnect is tracked accurately."""
    ws = MarketWsRecovery()
    ws.on_connect()
    assert ws.connected is True
    ws.on_disconnect()
    assert ws.connected is False


def test_market_ws_reconnect_restores_subscriptions() -> None:
    """Subscribed symbols are retained and restored upon reconnection."""
    ws = MarketWsRecovery()
    ws.subscribe(("RELIANCE", "TCS", "INFY"))
    assert set(ws.subscriptions) == {"RELIANCE", "TCS", "INFY"}
    ws.on_disconnect()
    restored = ws.restore_subscriptions()
    assert set(restored) == {"RELIANCE", "TCS", "INFY"}


def test_market_ws_stale_data_detected_and_blocks_live() -> None:
    """Ticks older than threshold are rejected and is_fresh returns False."""
    ws = MarketWsRecovery(max_age_seconds=10.0)
    now = time.time()
    # Old tick: 30 seconds ago
    valid = ws.observe_tick("RELIANCE", now - 30.0, tick_id="t1")
    assert valid is False
    assert ws.is_fresh(now) is False


def test_market_ws_fresh_data_restores_readiness() -> None:
    """Fresh ticks update timestamp and restore freshness."""
    ws = MarketWsRecovery(max_age_seconds=10.0)
    now = time.time()
    valid = ws.observe_tick("RELIANCE", now - 1.0, tick_id="t2")
    assert valid is True
    assert ws.is_fresh(now) is True


def test_market_ws_duplicate_ticks_deduplicated() -> None:
    """Duplicate ticks with the same identifier are filtered out."""
    ws = MarketWsRecovery()
    now = time.time()
    assert ws.observe_tick("TCS", now, tick_id="tick-unique-1") is True
    # Second arrival of same tick_id must be rejected
    assert ws.observe_tick("TCS", now, tick_id="tick-unique-1") is False


# ── 3. Order / Trade WebSocket Recovery Tests ─────────────────────────────


def test_order_ws_disconnect_detected_and_flags_missed_events() -> None:
    """Order WS disconnect flags missed_event_detected to trigger reconciliation."""
    ws = OrderWsRecovery()
    ws.on_connect()
    assert ws.connected is True
    assert ws.missed_event_detected is False
    ws.on_disconnect()
    assert ws.connected is False
    assert ws.missed_event_detected is True


def test_order_ws_reconnect_and_duplicate_deduplication() -> None:
    """Duplicate broker events are ignored; new events pass."""
    ws = OrderWsRecovery()
    ws.on_connect()
    assert ws.observe_event("fill-evt-100") is True
    # Duplicate event must be rejected
    assert ws.observe_event("fill-evt-100") is False


def test_order_ws_missed_event_cleared_after_reconciliation() -> None:
    """Clearing missed_event_flag resets the state post-reconciliation."""
    ws = OrderWsRecovery()
    ws.on_disconnect()
    assert ws.missed_event_detected is True
    ws.on_connect()
    ws.clear_missed_event_flag()
    assert ws.missed_event_detected is False


# ── 4. Execution State Machine Tests (§4, §10) ────────────────────────────


def test_execution_state_machine_valid_transitions() -> None:
    """Standard happy path walks through valid state transitions."""
    sm = ExecutionStateMachine(ExecutionState.STARTING)
    assert sm.transition(ExecutionState.RECOVERING, "boot") == ExecutionState.RECOVERING
    assert sm.transition(ExecutionState.READY, "verified") == ExecutionState.READY
    assert sm.transition(ExecutionState.RUNNING, "start") == ExecutionState.RUNNING
    assert sm.transition(ExecutionState.DEGRADED, "stale data") == ExecutionState.DEGRADED
    assert sm.transition(ExecutionState.RECOVERING, "re-check") == ExecutionState.RECOVERING
    assert sm.transition(ExecutionState.READY, "restored") == ExecutionState.READY


def test_execution_state_machine_invalid_transition_raises() -> None:
    """Invalid transitions raise IllegalTransitionError."""
    sm = ExecutionStateMachine(ExecutionState.STARTING)
    with pytest.raises(IllegalTransitionError, match="illegal execution state transition"):
        sm.transition(ExecutionState.RUNNING, "cannot jump to running directly")

    sm2 = ExecutionStateMachine(ExecutionState.BLOCKED)
    with pytest.raises(IllegalTransitionError, match="illegal execution state transition"):
        sm2.transition(ExecutionState.READY, "blocked cannot jump to ready")


def test_execution_state_machine_live_trading_only_in_running() -> None:
    """allows_live_trading is True ONLY when RUNNING."""
    sm = ExecutionStateMachine(ExecutionState.STARTING)
    assert sm.allows_live_trading is False

    sm.transition(ExecutionState.RECOVERING)
    assert sm.allows_live_trading is False

    sm.transition(ExecutionState.READY)
    assert sm.allows_live_trading is False

    sm.transition(ExecutionState.RUNNING)
    assert sm.allows_live_trading is True

    sm.transition(ExecutionState.DEGRADED)
    assert sm.allows_live_trading is False

    sm.transition(ExecutionState.BLOCKED)
    assert sm.allows_live_trading is False


# ── 5. Crash & Restart Recovery Flow Tests (§5, §6) ───────────────────────


def test_recovery_coordinator_full_flow() -> None:
    """Full recovery flow: START -> RECOVERING -> CHECKPOINT -> RECONCILE -> VERIFY -> READY."""
    journal = ExecutionJournal()
    coordinator = RecoveryCoordinator(journal=journal)
    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=1_000_000.0)

    coordinator.start_recovery("EC2 restart")
    assert coordinator.state_machine.state == ExecutionState.RECOVERING

    # Restore empty checkpoint
    coordinator.restore_checkpoint(engine, {"engine": {"orders": []}, "positions": []})

    # Broker reconnects
    coordinator.broker_recovery.on_reconnect_success()

    # Reconcile against empty broker snapshot (all matched)
    rec_state = coordinator.reconcile_with_broker(ledger, engine, [], [])
    assert not rec_state.blocks_live

    # Verify and complete
    ok = coordinator.verify_and_complete(rec_state, coordinator.sl_tracker, gates_satisfied=True)
    assert ok is True
    assert coordinator.state_machine.state == ExecutionState.READY

    kinds = [e.kind for e in journal.entries]
    assert "RECOVERY_STARTED" in kinds
    assert "RECONCILIATION_STARTED" in kinds
    assert "RECONCILIATION_COMPLETED" in kinds
    assert "RECOVERY_COMPLETED" in kinds


def test_recovery_coordinator_mismatch_blocks_live() -> None:
    """Reconciliation mismatch forces RECONCILIATION_REQUIRED state and blocks READY."""
    coordinator = RecoveryCoordinator()
    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=1_000_000.0)
    # Local position exists
    ledger.apply_fill(
        Fill(
            client_order_id="c1",
            broker_order_id="b1",
            symbol="TCS",
            side="BUY",
            fill_qty=10.0,
            fill_price=3500.0,
            commission=0.0,
            timestamp="t",
        )
    )

    coordinator.start_recovery("restart")
    # Broker reports no positions -> mismatch
    rec_state = coordinator.reconcile_with_broker(ledger, engine, [], [])
    assert rec_state.blocks_live is True
    assert coordinator.state_machine.state == ExecutionState.RECONCILIATION_REQUIRED

    # Verification fails closed
    ok = coordinator.verify_and_complete(rec_state, coordinator.sl_tracker, gates_satisfied=True)
    assert ok is False
    assert coordinator.state_machine.state != ExecutionState.READY


def test_recovery_coordinator_unprotected_sl_degrades() -> None:
    """Unprotected positions (SL_ATTENTION) degrade recovery and prevent READY."""
    coordinator = RecoveryCoordinator()
    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=1_000_000.0)
    coordinator.start_recovery("test")

    rec_state = coordinator.reconcile_with_broker(ledger, engine, [], [])
    # Add an unprotected SL record
    sl = coordinator.sl_tracker
    sl.on_fill("intent-1", "TCS", "BUY", 3500.0, stop_price=3400.0)
    sl.mark_sl_attention("intent-1", reason="SL order dropped")

    ok = coordinator.verify_and_complete(rec_state, sl, gates_satisfied=True)
    assert ok is False
    assert coordinator.state_machine.state == ExecutionState.DEGRADED


def test_recovery_restores_unknown_orders_reconcile_only() -> None:
    """Orders in UNKNOWN state from checkpoint exit ONLY via reconcile()."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="rec-unk-1", intent_id="ri-1", symbol="INFY", side="BUY", quantity=5.0
    )
    engine.create(order)
    engine.transition("rec-unk-1", OrderState.VALIDATED)
    engine.transition("rec-unk-1", OrderState.SUBMITTED)
    engine.transition("rec-unk-1", OrderState.UNKNOWN, reason="crash mid-flight")
    snap = engine.snapshot()

    fresh_engine = ExecutionEngine()
    coordinator = RecoveryCoordinator()
    coordinator.restore_checkpoint(fresh_engine, {"engine": snap})

    restored_order = fresh_engine.get("rec-unk-1")
    assert restored_order is not None
    assert restored_order.state == OrderState.UNKNOWN

    # Blind transition must raise
    with pytest.raises(IllegalTransitionError):
        fresh_engine.transition("rec-unk-1", OrderState.FILLED)

    # Only reconcile resolves UNKNOWN
    resolved = fresh_engine.reconcile("rec-unk-1", "FILLED")
    assert resolved.state == OrderState.FILLED


def test_recovery_restores_partial_fills_and_positions() -> None:
    """Restored engine from checkpoint correctly maintains partial fill quantities."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="part-1", intent_id="pi-1", symbol="INFY", side="BUY", quantity=10.0
    )
    engine.create(order)
    engine.transition("part-1", OrderState.VALIDATED)
    engine.transition("part-1", OrderState.SUBMITTED)
    engine.transition("part-1", OrderState.ACKNOWLEDGED)
    engine.apply_fill(
        order,
        Fill("part-1", "b1", "INFY", "BUY", 4.0, 1500.0, 0.0, "t", partial=True),
    )
    engine.transition("part-1", OrderState.PARTIALLY_FILLED)
    snap = engine.snapshot()

    restored_engine = ExecutionEngine()
    RecoveryCoordinator().restore_checkpoint(restored_engine, {"engine": snap})

    o = restored_engine.get("part-1")
    assert o is not None
    assert o.state == OrderState.PARTIALLY_FILLED
    assert o.filled_qty == 4.0
    assert o.avg_fill_price == 1500.0


# ── 6. Duplicate Safety Across Crash Scenarios (§7) ───────────────────────


def test_crash_before_ack_duplicate_prevention() -> None:
    """Order submitted, crash before ACK. Restored engine denies duplicate creation."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="cba-1", intent_id="intent-cba", symbol="SBIN", side="BUY", quantity=10.0
    )
    engine.create(order)
    engine.transition("cba-1", OrderState.VALIDATED)
    engine.transition("cba-1", OrderState.SUBMITTED)
    snap = engine.snapshot()

    fresh_engine = ExecutionEngine()
    RecoveryCoordinator().restore_checkpoint(fresh_engine, {"engine": snap})

    # Resubmitting the same intent or client_order_id must be blocked
    with pytest.raises(IllegalTransitionError, match="duplicate"):
        fresh_engine.create(order)


def test_crash_after_ack_duplicate_prevention() -> None:
    """Order ACK received, crash. Restored engine blocks duplicate placement."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="caa-1", intent_id="intent-caa", symbol="SBIN", side="BUY", quantity=10.0
    )
    engine.create(order)
    engine.transition("caa-1", OrderState.VALIDATED)
    engine.transition("caa-1", OrderState.SUBMITTED)
    engine.transition("caa-1", OrderState.ACKNOWLEDGED)
    snap = engine.snapshot()

    fresh_engine = ExecutionEngine()
    RecoveryCoordinator().restore_checkpoint(fresh_engine, {"engine": snap})

    with pytest.raises(IllegalTransitionError, match="duplicate"):
        fresh_engine.create(order)


def test_crash_after_partial_fill_duplicate_prevention() -> None:
    """Order partially filled, crash. Restored order blocks duplicate creation."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="capf-1", intent_id="intent-capf", symbol="SBIN", side="BUY", quantity=10.0
    )
    engine.create(order)
    engine.transition("capf-1", OrderState.VALIDATED)
    engine.transition("capf-1", OrderState.SUBMITTED)
    engine.transition("capf-1", OrderState.ACKNOWLEDGED)
    engine.apply_fill(
        order, Fill("capf-1", "b1", "SBIN", "BUY", 3.0, 500.0, 0.0, "t", partial=True)
    )
    engine.transition("capf-1", OrderState.PARTIALLY_FILLED)
    snap = engine.snapshot()

    fresh_engine = ExecutionEngine()
    RecoveryCoordinator().restore_checkpoint(fresh_engine, {"engine": snap})

    # Same intent_id with a different client_order_id must also raise
    dup_order = BrokerOrder(
        client_order_id="capf-2", intent_id="intent-capf", symbol="SBIN", side="BUY", quantity=10.0
    )
    with pytest.raises(IllegalTransitionError, match="duplicate intent"):
        fresh_engine.create(dup_order)


def test_crash_after_full_fill_duplicate_prevention() -> None:
    """Order fully filled, crash. Terminal order rejects further fills and duplicate intents."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="caff-1", intent_id="intent-caff", symbol="SBIN", side="BUY", quantity=10.0
    )
    engine.create(order)
    engine.transition("caff-1", OrderState.VALIDATED)
    engine.transition("caff-1", OrderState.SUBMITTED)
    engine.transition("caff-1", OrderState.ACKNOWLEDGED)
    engine.apply_fill(
        order, Fill("caff-1", "b1", "SBIN", "BUY", 10.0, 500.0, 0.0, "t", partial=False)
    )
    engine.transition("caff-1", OrderState.FILLED)
    snap = engine.snapshot()

    fresh_engine = ExecutionEngine()
    RecoveryCoordinator().restore_checkpoint(fresh_engine, {"engine": snap})

    restored = fresh_engine.get("caff-1")
    assert restored is not None and restored.state == OrderState.FILLED
    with pytest.raises(IllegalTransitionError, match="terminal"):
        fresh_engine.apply_fill(
            restored, Fill("caff-1", "b1", "SBIN", "BUY", 10.0, 500.0, 0.0, "t")
        )


def test_recovery_never_creates_duplicate_broker_order() -> None:
    """Multiple recovery cycles with the same checkpoint never double-count or duplicate orders."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="cyd-1", intent_id="intent-cyd", symbol="SBIN", side="BUY", quantity=10.0
    )
    engine.create(order)
    engine.transition("cyd-1", OrderState.VALIDATED)
    engine.transition("cyd-1", OrderState.SUBMITTED)
    snap = engine.snapshot()

    # Recovery run 1
    engine1 = ExecutionEngine()
    c1, _ = RecoveryCoordinator().restore_checkpoint(engine1, {"engine": snap})
    assert c1 == 1

    # Recovery run 2
    engine2 = ExecutionEngine()
    c2, _ = RecoveryCoordinator().restore_checkpoint(engine2, {"engine": snap})
    assert c2 == 1
    assert len(engine2._orders) == 1
    assert engine2.get("cyd-1") is not None


# ── 7. APK Disconnect Safety Tests (§8) ───────────────────────────────────


def test_apk_disconnect_does_not_stop_ec2_engine() -> None:
    """APK client disconnect is tracked but execution engine state remains unchanged."""
    coordinator = RecoveryCoordinator()
    coordinator.state_machine.transition(ExecutionState.READY)
    coordinator.state_machine.transition(ExecutionState.RUNNING)

    # Client connects then disconnects
    coordinator.client_tracker.on_client_connect()
    assert coordinator.client_tracker.connected is True
    coordinator.client_tracker.on_client_disconnect()
    assert coordinator.client_tracker.connected is False
    assert coordinator.client_tracker.disconnect_count == 1

    # Engine is STILL RUNNING!
    assert coordinator.state_machine.state == ExecutionState.RUNNING
    assert coordinator.state_machine.allows_live_trading is True


def test_apk_reconnect_receives_fresh_snapshot() -> None:
    """Client reconnecting receives authoritative enriched snapshot."""
    coordinator = RecoveryCoordinator()
    coordinator.broker_recovery.on_reconnect_success()
    coordinator.market_ws.on_connect()
    coordinator.order_ws.on_connect()

    tracker = SlProtectionTracker()
    base = build_safety_snapshot(
        kill_switch_engaged=False,
        kill_switch_reason="",
        kill_switch_level="global",
        gates_live_trading_enabled=True,
        gates_broker_live_enabled=True,
        gates_account_confirmed=True,
        gates_risk_limits_valid=True,
        gates_kill_switch_off=True,
        broker_connected=True,
        broker_name="fyers",
        broker_environment="live",
        broker_health_reason="",
        reconciliation_healthy=True,
        reconciliation_status="SAFE",
        reconciliation_reasons=(),
        risk_ready=True,
        capital_valid=True,
        last_risk_denial_reason="",
        sl_tracker=tracker,
        armed="DISARMED",
        session_lifecycle="RUNNING",
        last_block_reason="",
    )

    coordinator.client_tracker.on_client_connect()
    snapshot = coordinator.build_snapshot(base)

    assert snapshot.broker_connection == "CONNECTED"
    assert snapshot.market_ws == "CONNECTED"
    assert snapshot.order_ws == "CONNECTED"
    assert snapshot.overall == TradingSafetyState.READY


# ── 8. Heartbeat & Watchdog Tests (§9, §10) ───────────────────────────────


def test_heartbeat_monitor_liveness_and_stale_detection() -> None:
    """Heartbeat monitor accurately reflects freshness and marks timeout."""
    hb = HeartbeatMonitor(stale_threshold_seconds=10.0)
    now = time.time()
    assert hb.is_alive(now) is True
    # Check with epoch far past threshold
    assert hb.is_alive(now + 25.0) is False


def test_watchdog_prevents_infinite_retry_loop() -> None:
    """Watchdog records bounded failures and trips to BLOCKED at max attempts."""
    journal = ExecutionJournal()
    coordinator = RecoveryCoordinator(journal=journal, max_recovery_attempts=3)
    coordinator.start_recovery("attempt 1")

    # Failure 1
    coordinator.fail_recovery("network timeout 1")
    assert coordinator.state_machine.state == ExecutionState.DEGRADED
    assert coordinator.watchdog.tripped is False

    # Failure 2
    coordinator.fail_recovery("network timeout 2")
    assert coordinator.state_machine.state == ExecutionState.DEGRADED
    assert coordinator.watchdog.tripped is False

    # Failure 3 -> Trips!
    coordinator.fail_recovery("network timeout 3")
    assert coordinator.watchdog.tripped is True
    assert coordinator.state_machine.state == ExecutionState.BLOCKED
    assert "watchdog threshold" in coordinator.watchdog.tripped_reason

    # Journal recorded SYSTEM_BLOCKED
    blocked_events = journal.of_kind("SYSTEM_BLOCKED")
    assert len(blocked_events) == 1


def test_watchdog_cannot_bypass_safety_gates() -> None:
    """Tripped watchdog prevents state from jumping to READY."""
    coordinator = RecoveryCoordinator(max_recovery_attempts=2)
    coordinator.fail_recovery("error 1")
    coordinator.fail_recovery("error 2")  # trips
    assert coordinator.state_machine.state == ExecutionState.BLOCKED

    # BLOCKED state must NOT transition to READY
    with pytest.raises(IllegalTransitionError):
        coordinator.state_machine.transition(ExecutionState.READY)


# ── 9. State Visibility & Remote Compatibility (§12, §13) ─────────────────


def test_extended_safety_snapshot_exposes_phase2_fields() -> None:
    """SafetySnapshot includes all Phase 2 fields while maintaining Phase 1 compatibility."""
    tracker = SlProtectionTracker()
    snap = build_safety_snapshot(
        kill_switch_engaged=False,
        kill_switch_reason="",
        kill_switch_level="global",
        gates_live_trading_enabled=True,
        gates_broker_live_enabled=True,
        gates_account_confirmed=True,
        gates_risk_limits_valid=True,
        gates_kill_switch_off=True,
        broker_connected=True,
        broker_name="fyers",
        broker_environment="live",
        broker_health_reason="",
        reconciliation_healthy=True,
        reconciliation_status="SAFE",
        reconciliation_reasons=(),
        risk_ready=True,
        capital_valid=True,
        last_risk_denial_reason="",
        sl_tracker=tracker,
        armed="DISARMED",
        session_lifecycle="RUNNING",
        last_block_reason="",
        execution_state="RUNNING",
        broker_connection="CONNECTED",
        market_ws="CONNECTED",
        order_ws="CONNECTED",
        recovery_state="IDLE",
        recovery_attempts=0,
        last_recovery_error="",
    )

    # Phase 1 fields present
    assert snap.overall == TradingSafetyState.READY
    assert snap.live_trading_enabled is True
    assert snap.broker_name == "fyers"

    # Phase 2 fields present
    assert snap.execution_state == "RUNNING"
    assert snap.broker_connection == "CONNECTED"
    assert snap.market_ws == "CONNECTED"
    assert snap.order_ws == "CONNECTED"
    assert snap.recovery_state == "IDLE"
    assert snap.recovery_attempts == 0

    # to_dict serialization includes all Phase 2 keys
    d = snap.to_dict()
    assert d["execution_state"] == "RUNNING"
    assert d["broker_connection"] == "CONNECTED"
    assert d["market_ws"] == "CONNECTED"
    assert d["order_ws"] == "CONNECTED"
    assert d["recovery_state"] == "IDLE"
    assert d["recovery_attempts"] == 0


def test_extended_safety_snapshot_to_dict_json_safe_no_secrets() -> None:
    """to_dict contains no secret words and is safely serializable."""
    tracker = SlProtectionTracker()
    snap = build_safety_snapshot(
        kill_switch_engaged=False,
        kill_switch_reason="",
        kill_switch_level="global",
        gates_live_trading_enabled=False,
        gates_broker_live_enabled=False,
        gates_account_confirmed=False,
        gates_risk_limits_valid=False,
        gates_kill_switch_off=True,
        broker_connected=False,
        broker_name="paper",
        broker_environment="paper",
        broker_health_reason="",
        reconciliation_healthy=True,
        reconciliation_status="SAFE",
        reconciliation_reasons=(),
        risk_ready=False,
        capital_valid=False,
        last_risk_denial_reason="",
        sl_tracker=tracker,
        armed="DISARMED",
        session_lifecycle="CREATED",
        last_block_reason="",
    )
    raw = str(snap.to_dict()).lower()
    for secret in ("secret", "password", "token", "api_key", "apikey"):
        assert secret not in raw


def test_livesession_exposes_recovery_and_enriched_snapshot() -> None:
    """LiveSession has .recovery coordinator and produces enriched safety snapshots."""
    session = LiveSession(SessionConfig(), SandboxBroker(), None)  # type: ignore[arg-type]
    assert hasattr(session, "recovery")
    assert isinstance(session.recovery, RecoveryCoordinator)

    snap = session.safety_snapshot()
    assert hasattr(snap, "execution_state")
    assert hasattr(snap, "broker_connection")
    assert hasattr(snap, "market_ws")
    assert hasattr(snap, "order_ws")
    assert hasattr(snap, "recovery_attempts")
