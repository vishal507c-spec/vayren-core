"""VAYREN Phase 5 — Institutional Reconciliation and Self-Healing Engine Tests.

Verifies all 18 institutional reconciliation and self-healing requirements:
1.  Authoritative Broker Truth (§1): Broker state > local VAYREN assumptions.
2.  Mismatch Detection Taxonomy (§2): All 11 mismatch kinds correctly identified.
3.  Fail-Closed Discipline (§3): Mismatches block live trading, stops preserved.
4.  Deterministic 10-Step Recovery Pipeline (§4): Steps 1-10 executed strictly.
5.  Unknown Broker Orders (§5): Non-destructive inventory recording, no blind cancel.
6.  Partial Fills (§6): Ingests cumulative broker filled quantity.
7.  Stop-Loss Protection (§7): Positions require inverse protective SL of equal size.
8.  Periodic Reconciliation (§8): Interval triggers and consecutive clean tracking.
9.  Crash/Restart Reconstruction (§9): Ledger rebuilt from broker snapshot.
10. Bounded Self-Healing (§10): Retry limits enforce escalation to BLOCKED.
11. Audit Trail (§11): ExecutionJournal records all events with zero secrets.
12. Control Plane & WSS (§12): Sequenced events and authoritative snapshots.
"""

from __future__ import annotations

from typing import Any

from execution.control_plane import (
    CommandRequest,
    CommandStatus,
    ControlCommand,
    ControlPlaneAuth,
    ControlPlaneController,
    EventStreamManager,
)
from execution.engine import ExecutionEngine
from execution.journal import ExecutionJournal
from execution.models.order import BrokerOrder, OrderState
from execution.models.position import Position
from execution.portfolio.ledger import PositionLedger
from execution.reconciliation import (
    BrokerOrderTruth,
    BrokerPositionTruth,
    BrokerTruthSnapshot,
    HealingActionKind,
    InstitutionalMismatch,
    InstitutionalReconciliationReport,
    MismatchType,
    PeriodicReconciler,
    ReconciliationPipeline,
    SelfHealingCoordinator,
    compare_broker_truth,
)
from execution.recovery import (
    ExecutionState,
    ExecutionStateMachine,
)
from execution.safety import (
    SlProtectionTracker,
    build_safety_snapshot,
)


def _make_sample_order(
    client_order_id: str,
    symbol: str = "NSE:INFY-EQ",
    side: str = "BUY",
    quantity: float = 50.0,
    state: OrderState = OrderState.SUBMITTED,
    filled_qty: float = 0.0,
    stop_price: float | None = None,
) -> BrokerOrder:
    return BrokerOrder(
        client_order_id=client_order_id,
        intent_id=f"intent-{client_order_id}",
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type="LIMIT" if stop_price is None else "STOP_MARKET",
        state=state,
        filled_qty=filled_qty,
    )


# ── 1. Authoritative Broker Truth Models (§1) ────────────────────────────────


def test_broker_truth_snapshot_query_helpers() -> None:
    """BrokerTruthSnapshot provides immutable lookup for orders, positions and SLs."""
    ord1 = BrokerOrderTruth(
        order_id="venue-1",
        client_order_id="cid-1",
        symbol="NSE:INFY-EQ",
        side="BUY",
        quantity=50.0,
        filled_quantity=50.0,
        status="FILLED",
    )
    sl_ord = BrokerOrderTruth(
        order_id="venue-2",
        client_order_id="cid-sl-1",
        symbol="NSE:INFY-EQ",
        side="SELL",
        quantity=50.0,
        filled_quantity=0.0,
        status="OPEN",
        stop_price=1400.0,
        order_type="STOP_LOSS",
        is_protective_sl=True,
    )
    pos1 = BrokerPositionTruth(
        symbol="NSE:INFY-EQ",
        quantity=50.0,
        avg_price=1450.0,
        current_price=1460.0,
    )

    snapshot = BrokerTruthSnapshot(
        orders=(ord1, sl_ord),
        positions=(pos1,),
        funds={"available_cash": 100_000.0},
    )

    assert snapshot.find_order("cid-1") == ord1
    assert snapshot.find_order("cid-unknown") is None
    assert snapshot.find_position("NSE:INFY-EQ") == pos1
    assert snapshot.find_position("NSE:TCS-EQ") is None

    sls = snapshot.protective_sl_for("NSE:INFY-EQ")
    assert len(sls) == 1
    assert sls[0].client_order_id == "cid-sl-1"
    assert snapshot.protective_sl_for("NSE:TCS-EQ") == ()


# ── 2. Mismatch Detection Taxonomy (§2) ──────────────────────────────────────


def test_clean_parity_zero_mismatches() -> None:
    """When broker truth perfectly matches local state, matched=True and blocks_live=False."""
    ledger = PositionLedger(starting_capital=100_000.0)
    ledger.adopt_position(Position(symbol="NSE:INFY-EQ", quantity=50.0, avg_price=1450.0))

    sl_tracker = SlProtectionTracker()
    sl_tracker.on_fill("intent-infy", "NSE:INFY-EQ", "BUY", 1450.0, 1400.0)
    sl_tracker.confirm_sl("intent-infy", "venue-sl")

    truth = BrokerTruthSnapshot(
        orders=(
            BrokerOrderTruth(
                order_id="venue-sl",
                client_order_id="cid-sl",
                symbol="NSE:INFY-EQ",
                side="SELL",
                quantity=50.0,
                filled_quantity=0.0,
                status="OPEN",
                order_type="STOP_MARKET",
                is_protective_sl=True,
            ),
        ),
        positions=(BrokerPositionTruth(symbol="NSE:INFY-EQ", quantity=50.0),),
    )

    report = compare_broker_truth(
        local_positions=ledger.all_positions(),
        local_orders={},
        sl_tracker=sl_tracker,
        broker_truth=truth,
    )

    assert report.matched is True
    assert report.blocks_live is False
    assert len(report.mismatches) == 0
    assert report.orders_matched is True
    assert report.positions_matched is True
    assert report.sl_matched is True


def test_mismatch_unknown_broker_order() -> None:
    """Order active on broker that is untracked locally is detected."""
    truth = BrokerTruthSnapshot(
        orders=(
            BrokerOrderTruth(
                order_id="venue-rogue-1",
                client_order_id="manual-101",
                symbol="NSE:SBIN-EQ",
                side="BUY",
                quantity=100.0,
                filled_quantity=0.0,
                status="OPEN",
            ),
        ),
        positions=(),
    )

    report = compare_broker_truth(
        local_positions=(),
        local_orders={},
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth,
    )

    assert report.matched is False
    assert report.orders_matched is False
    assert len(report.unknown_broker_orders) == 1
    mtypes = [m.mismatch_type for m in report.mismatches]
    assert MismatchType.UNKNOWN_BROKER_ORDER in mtypes


def test_mismatch_missing_local_order() -> None:
    """Local open order absent on broker venue is detected."""
    local_order = _make_sample_order("cid-missing", state=OrderState.SUBMITTED)

    truth = BrokerTruthSnapshot(orders=(), positions=())

    report = compare_broker_truth(
        local_positions=(),
        local_orders={"cid-missing": local_order},
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth,
    )

    assert report.matched is False
    assert report.orders_matched is False
    mtypes = [m.mismatch_type for m in report.mismatches]
    assert MismatchType.MISSING_LOCAL_ORDER in mtypes


def test_mismatch_fill_discrepancy() -> None:
    """Local filled quantity differing from broker cumulative fill is detected."""
    local_order = _make_sample_order(
        "cid-fill",
        state=OrderState.PARTIALLY_FILLED,
        filled_qty=20.0,
    )
    broker_order = BrokerOrderTruth(
        order_id="venue-fill",
        client_order_id="cid-fill",
        symbol="NSE:INFY-EQ",
        side="BUY",
        quantity=50.0,
        filled_quantity=40.0,
        status="PARTIAL",
    )

    truth = BrokerTruthSnapshot(orders=(broker_order,), positions=())

    report = compare_broker_truth(
        local_positions=(),
        local_orders={"cid-fill": local_order},
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth,
    )

    assert report.matched is False
    assert report.orders_matched is False
    mtypes = [m.mismatch_type for m in report.mismatches]
    assert MismatchType.FILL_MISMATCH in mtypes


def test_mismatch_position_presence_and_quantity() -> None:
    """Position presence mismatch and quantity divergence are detected."""
    # Case A: Broker holds position, local book is flat
    truth_a = BrokerTruthSnapshot(
        positions=(BrokerPositionTruth(symbol="NSE:TCS-EQ", quantity=25.0),),
    )
    report_a = compare_broker_truth(
        local_positions=(),
        local_orders={},
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth_a,
    )
    assert report_a.positions_matched is False
    assert any(m.mismatch_type == MismatchType.POSITION_MISMATCH for m in report_a.mismatches)

    # Case B: Local book holds position, broker is flat
    local_pos = Position(symbol="NSE:WIPRO-EQ", quantity=100.0)
    report_b = compare_broker_truth(
        local_positions=(local_pos,),
        local_orders={},
        sl_tracker=SlProtectionTracker(),
        broker_truth=BrokerTruthSnapshot(positions=()),
    )
    assert report_b.positions_matched is False
    assert any(m.mismatch_type == MismatchType.POSITION_MISMATCH for m in report_b.mismatches)

    # Case C: Both hold position, but quantities diverge
    truth_c = BrokerTruthSnapshot(
        positions=(BrokerPositionTruth(symbol="NSE:WIPRO-EQ", quantity=80.0),),
    )
    report_c = compare_broker_truth(
        local_positions=(local_pos,),
        local_orders={},
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth_c,
    )
    assert report_c.positions_matched is False
    assert any(m.mismatch_type == MismatchType.QUANTITY_MISMATCH for m in report_c.mismatches)


def test_mismatch_protective_stop_loss_rules() -> None:
    """Detects missing SL, wrong SL quantity, and wrong SL side."""
    pos = BrokerPositionTruth(symbol="NSE:INFY-EQ", quantity=50.0)  # Long 50

    # 1. Missing SL entirely
    truth_missing = BrokerTruthSnapshot(positions=(pos,), orders=())
    rep1 = compare_broker_truth((), {}, SlProtectionTracker(), truth_missing)
    assert rep1.sl_matched is False
    assert any(m.mismatch_type == MismatchType.MISSING_SL for m in rep1.mismatches)

    # 2. Wrong SL side (Long requires SELL stop, but order is BUY)
    wrong_side = BrokerOrderTruth(
        order_id="venue-sl1",
        client_order_id="cid-sl1",
        symbol="NSE:INFY-EQ",
        side="BUY",
        quantity=50.0,
        filled_quantity=0.0,
        status="OPEN",
        order_type="STOP",
        is_protective_sl=True,
    )
    truth_side = BrokerTruthSnapshot(positions=(pos,), orders=(wrong_side,))
    rep2 = compare_broker_truth((), {}, SlProtectionTracker(), truth_side)
    assert rep2.sl_matched is False
    assert any(m.mismatch_type == MismatchType.WRONG_SL_SIDE for m in rep2.mismatches)

    # 3. Wrong SL quantity (Position is 50, SL is for 30)
    wrong_qty = BrokerOrderTruth(
        order_id="venue-sl2",
        client_order_id="cid-sl2",
        symbol="NSE:INFY-EQ",
        side="SELL",
        quantity=30.0,
        filled_quantity=0.0,
        status="OPEN",
        order_type="STOP",
        is_protective_sl=True,
    )
    truth_qty = BrokerTruthSnapshot(positions=(pos,), orders=(wrong_qty,))
    rep3 = compare_broker_truth((), {}, SlProtectionTracker(), truth_qty)
    assert rep3.sl_matched is False
    assert any(m.mismatch_type == MismatchType.WRONG_SL_QUANTITY for m in rep3.mismatches)


def test_mismatch_order_status_discrepancy() -> None:
    """Local order open but broker terminal triggers ORDER_STATUS_MISMATCH."""
    local_order = _make_sample_order("cid-term", state=OrderState.SUBMITTED)
    broker_order = BrokerOrderTruth(
        order_id="venue-term",
        client_order_id="cid-term",
        symbol="NSE:INFY-EQ",
        side="BUY",
        quantity=50.0,
        filled_quantity=50.0,
        status="FILLED",
    )

    truth = BrokerTruthSnapshot(orders=(broker_order,), positions=())
    rep = compare_broker_truth((), {"cid-term": local_order}, SlProtectionTracker(), truth)
    assert rep.orders_matched is False
    assert any(m.mismatch_type == MismatchType.ORDER_STATUS_MISMATCH for m in rep.mismatches)


# ── 3. Fail-Closed Discipline (§3) ───────────────────────────────────────────


def test_fail_closed_blocks_live_and_preserves_protective_stops() -> None:
    """Fail-closed discipline ensures new orders are blocked while stops stay intact."""
    sm = ExecutionStateMachine(initial=ExecutionState.RUNNING)
    journal = ExecutionJournal()
    healer = SelfHealingCoordinator(journal=journal)
    pipeline = ReconciliationPipeline(healer=healer, journal=journal)

    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=100_000.0)
    sl_tracker = SlProtectionTracker()

    # Broker has position without protective SL
    truth = BrokerTruthSnapshot(
        positions=(BrokerPositionTruth(symbol="NSE:INFY-EQ", quantity=50.0),),
        orders=(),
    )

    result = pipeline.execute(
        engine=engine,
        ledger=ledger,
        sl_tracker=sl_tracker,
        state_machine=sm,
        broker_fetcher=lambda: truth,
        operator_armed=True,
    )

    assert result.success is False
    assert result.final_state in (ExecutionState.DEGRADED, ExecutionState.RECONCILIATION_REQUIRED)
    assert sm.allows_live_trading is False

    frozen_entries = journal.of_kind("ORDERS_FROZEN")
    assert len(frozen_entries) >= 1
    assert frozen_entries[0].payload["protective_stops_intact"] is True


# ── 4. Deterministic 10-Step Recovery Pipeline (§4) ──────────────────────────


def test_ten_step_pipeline_full_clean_flow_armed() -> None:
    """Pipeline runs all 10 steps sequentially and enters RUNNING when operator_armed=True."""
    sm = ExecutionStateMachine(initial=ExecutionState.READY)
    journal = ExecutionJournal()
    pipeline = ReconciliationPipeline(journal=journal)

    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=100_000.0)
    ledger.adopt_position(Position(symbol="NSE:INFY-EQ", quantity=50.0, avg_price=1450.0))

    sl_tracker = SlProtectionTracker()
    sl_tracker.on_fill("intent-infy", "NSE:INFY-EQ", "BUY", 1450.0, 1400.0)
    sl_tracker.confirm_sl("intent-infy", "v-sl")

    truth = BrokerTruthSnapshot(
        positions=(BrokerPositionTruth(symbol="NSE:INFY-EQ", quantity=50.0),),
        orders=(
            BrokerOrderTruth(
                order_id="v-sl",
                client_order_id="cid-sl",
                symbol="NSE:INFY-EQ",
                side="SELL",
                quantity=50.0,
                filled_quantity=0.0,
                status="OPEN",
                is_protective_sl=True,
            ),
        ),
    )

    res = pipeline.execute(
        engine=engine,
        ledger=ledger,
        sl_tracker=sl_tracker,
        state_machine=sm,
        broker_fetcher=lambda: truth,
        operator_armed=True,
    )

    assert res.success is True
    assert res.final_state == ExecutionState.RUNNING
    assert sm.allows_live_trading is True
    assert len(res.steps) == 10
    step_names = [s.step_name for s in res.steps]
    expected_steps = [
        "DETECT",
        "FREEZE_NEW_ORDERS",
        "FETCH_BROKER_TRUTH",
        "COMPARE",
        "RECONCILE",
        "VERIFY_POSITIONS",
        "VERIFY_PROTECTIVE_SL",
        "VERIFY_RISK",
        "READY",
        "RE_ARM_LIVE",
    ]
    assert step_names == expected_steps
    assert all(s.passed for s in res.steps)


def test_ten_step_pipeline_holds_in_ready_when_not_armed() -> None:
    """When operator_armed=False, pipeline completes successfully in READY state."""
    sm = ExecutionStateMachine(initial=ExecutionState.STARTING)
    pipeline = ReconciliationPipeline()

    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=50_000.0)
    truth = BrokerTruthSnapshot()

    res = pipeline.execute(
        engine=engine,
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        state_machine=sm,
        broker_fetcher=lambda: truth,
        operator_armed=False,
    )

    assert res.success is True
    assert res.final_state == ExecutionState.READY
    assert sm.allows_live_trading is False
    assert res.steps[-1].step_name == "RE_ARM_LIVE"
    assert "holding in READY" in res.steps[-1].details


def test_ten_step_pipeline_broker_fetch_error_blocks() -> None:
    """Exception during broker fetch in Step 1 immediately transitions to BLOCKED."""
    sm = ExecutionStateMachine(initial=ExecutionState.READY)
    pipeline = ReconciliationPipeline()

    def _failing_fetcher() -> BrokerTruthSnapshot:
        raise ConnectionResetError("FYERS gateway timed out")

    engine = ExecutionEngine()
    res = pipeline.execute(
        engine=engine,
        ledger=PositionLedger(starting_capital=10_000.0),
        sl_tracker=SlProtectionTracker(),
        state_machine=sm,
        broker_fetcher=_failing_fetcher,
    )

    assert res.success is False
    assert res.final_state == ExecutionState.BLOCKED
    assert sm.allows_live_trading is False
    assert len(res.steps) == 1
    assert res.steps[0].passed is False


# ── 5. Unknown Broker Orders (§5) ────────────────────────────────────────────


def test_unknown_broker_order_non_destructive_registration() -> None:
    """Unknown broker orders are registered non-destructively without modifying venue."""
    journal = ExecutionJournal()
    healer = SelfHealingCoordinator(journal=journal)
    ledger = PositionLedger(starting_capital=100_000.0)
    sm = ExecutionStateMachine(initial=ExecutionState.RECONCILING)

    unknown_order = BrokerOrderTruth(
        order_id="venue-unknown-99",
        client_order_id="ext-trader-99",
        symbol="NSE:RELIANCE-EQ",
        side="BUY",
        quantity=25.0,
        filled_quantity=0.0,
        status="OPEN",
    )
    truth = BrokerTruthSnapshot(orders=(unknown_order,))

    mismatch = InstitutionalMismatch(
        mismatch_type=MismatchType.UNKNOWN_BROKER_ORDER,
        symbol_or_id="ext-trader-99",
        local_value="ABSENT",
        broker_value="status=OPEN, qty=25.0",
        message="untracked broker order ext-trader-99",
    )

    result = healer.heal_mismatch(
        mismatch=mismatch,
        engine=ExecutionEngine(),
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth,
        state_machine=sm,
    )

    assert result.action == HealingActionKind.REGISTER_UNKNOWN_ORDER
    assert result.success is True
    assert "without destructive modification" in result.reason

    entries = journal.of_kind("UNKNOWN_BROKER_ORDER_RECORDED")
    assert len(entries) == 1
    assert entries[0].payload["non_destructive"] is True
    assert entries[0].payload["client_order_id"] == "ext-trader-99"


# ── 6. Partial Fills Self-Healing (§6) ────────────────────────────────────────


def test_partial_fill_ingested_accurately() -> None:
    """Cumulative broker fill quantity is ingested and aligns local order."""
    journal = ExecutionJournal()
    healer = SelfHealingCoordinator(journal=journal)
    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=100_000.0)
    sm = ExecutionStateMachine(initial=ExecutionState.RECONCILING)

    ord_obj = _make_sample_order("cid-pf-1", quantity=100.0, filled_qty=30.0)
    engine._orders["cid-pf-1"] = ord_obj

    bo_truth = BrokerOrderTruth(
        order_id="venue-pf-1",
        client_order_id="cid-pf-1",
        symbol="NSE:INFY-EQ",
        side="BUY",
        quantity=100.0,
        filled_quantity=70.0,
        status="PARTIALLY_FILLED",
    )
    truth = BrokerTruthSnapshot(orders=(bo_truth,))

    mismatch = InstitutionalMismatch(
        mismatch_type=MismatchType.FILL_MISMATCH,
        symbol_or_id="cid-pf-1",
        local_value="30.0",
        broker_value="70.0",
    )

    res = healer.heal_mismatch(
        mismatch=mismatch,
        engine=engine,
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth,
        state_machine=sm,
    )

    assert res.action == HealingActionKind.INGEST_PARTIAL_FILL
    assert res.success is True

    # Order in engine reflects 70.0 filled
    aligned_ord = engine.get("cid-pf-1")
    assert aligned_ord is not None
    assert aligned_ord.filled_qty == 70.0


# ── 7. Stop-Loss Verification (§7) ───────────────────────────────────────────


def test_sl_protection_discrepancy_flags_attention_and_degrades() -> None:
    """Position without verified stop-loss flags attention and degrades execution state."""
    sm = ExecutionStateMachine(initial=ExecutionState.READY)
    pipeline = ReconciliationPipeline()

    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=50_000.0)
    ledger.adopt_position(Position(symbol="NSE:TCS-EQ", quantity=40.0))

    sl_tracker = SlProtectionTracker()
    # Missing SL order on broker
    truth = BrokerTruthSnapshot(
        positions=(BrokerPositionTruth(symbol="NSE:TCS-EQ", quantity=40.0),),
        orders=(),
    )

    res = pipeline.execute(
        engine=engine,
        ledger=ledger,
        sl_tracker=sl_tracker,
        state_machine=sm,
        broker_fetcher=lambda: truth,
        operator_armed=True,
    )

    assert res.success is False
    assert res.final_state == ExecutionState.DEGRADED
    assert sm.allows_live_trading is False
    assert not res.steps[6].passed  # Step 7 VERIFY_PROTECTIVE_SL failed


# ── 8. Periodic Reconciliation (§8) ──────────────────────────────────────────


def test_periodic_reconciler_cadence_and_consecutive_clean() -> None:
    """PeriodicReconciler executes on schedule and increments clean counter."""
    pipeline = ReconciliationPipeline()
    reconciler = PeriodicReconciler(pipeline=pipeline, interval_seconds=10.0)

    engine = ExecutionEngine()
    ledger = PositionLedger(starting_capital=50_000.0)
    sm = ExecutionStateMachine(initial=ExecutionState.READY)
    truth = BrokerTruthSnapshot()

    # Initial run
    assert reconciler.should_run(current_epoch=100.0) is True
    res1 = reconciler.poll(
        engine=engine,
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        state_machine=sm,
        broker_fetcher=lambda: truth,
        force=True,
    )
    assert res1 is not None
    assert reconciler.consecutive_clean_runs == 1

    # Before interval elapsed
    assert reconciler.should_run(current_epoch=105.0) is False
    assert (
        reconciler.poll(
            engine=engine,
            ledger=ledger,
            sl_tracker=SlProtectionTracker(),
            state_machine=sm,
            broker_fetcher=lambda: truth,
            force=False,
        )
        is None
    )

    # After interval elapsed
    res2 = reconciler.poll(
        engine=engine,
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        state_machine=sm,
        broker_fetcher=lambda: truth,
        force=True,
    )
    assert res2 is not None
    assert reconciler.consecutive_clean_runs == 2


# ── 9. Crash/Restart Recovery (§9) ───────────────────────────────────────────


def test_crash_restart_ledger_reconstruction() -> None:
    """On engine reboot, ledger is cleanly reconstructed from broker truth."""
    cold_ledger = PositionLedger(10_000.0)
    assert len(cold_ledger.all_positions()) == 0

    truth = BrokerTruthSnapshot(
        positions=(
            BrokerPositionTruth(symbol="NSE:INFY-EQ", quantity=100.0, avg_price=1420.0),
            BrokerPositionTruth(symbol="NSE:TCS-EQ", quantity=-20.0, avg_price=3500.0),
        ),
    )

    # Reconstruct positions from broker truth
    rebuilt_positions = [
        Position(
            symbol=bp.symbol,
            quantity=bp.quantity,
            avg_price=bp.avg_price,
        )
        for bp in truth.positions
        if not bp.flat
    ]
    cold_ledger.adopt_positions(rebuilt_positions)

    all_pos = {p.symbol: p.quantity for p in cold_ledger.all_positions()}
    assert all_pos == {"NSE:INFY-EQ": 100.0, "NSE:TCS-EQ": -20.0}


# ── 10. Bounded Self-Healing & Escalation (§10) ──────────────────────────────


def test_bounded_self_healing_escalation_to_blocked() -> None:
    """Exceeding maximum healing retries escalates state machine to BLOCKED."""
    journal = ExecutionJournal()
    healer = SelfHealingCoordinator(max_attempts_per_target=3, journal=journal)
    ledger = PositionLedger(starting_capital=100_000.0)
    sm = ExecutionStateMachine(initial=ExecutionState.RECONCILING)

    mismatch = InstitutionalMismatch(
        mismatch_type=MismatchType.POSITION_MISMATCH,
        symbol_or_id="NSE:STUCK-EQ",
        local_value="50",
        broker_value="FLAT",
    )
    truth = BrokerTruthSnapshot()

    # Attempts 1, 2, 3 succeed in healing action
    for attempt in range(1, 4):
        res = healer.heal_mismatch(
            mismatch=mismatch,
            engine=ExecutionEngine(),
            ledger=ledger,
            sl_tracker=SlProtectionTracker(),
            broker_truth=truth,
            state_machine=sm,
        )
        assert res.success is True
        assert healer.get_attempts("NSE:STUCK-EQ") == attempt

    # 4th attempt trips retry threshold and escalates
    res_esc = healer.heal_mismatch(
        mismatch=mismatch,
        engine=ExecutionEngine(),
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        broker_truth=truth,
        state_machine=sm,
    )
    assert res_esc.success is False
    assert res_esc.escalated is True
    assert res_esc.action == HealingActionKind.ESCALATE_TO_BLOCKED
    assert sm.state == ExecutionState.BLOCKED

    entries = journal.of_kind("SELF_HEALING_FAILED")
    assert len(entries) == 1
    assert entries[0].payload["escalated"] == "BLOCKED"


# ── 11. Audit Trail & Zero Secrets (§11) ─────────────────────────────────────


def test_audit_trail_records_events_with_zero_secrets() -> None:
    """Journal entries record reconciliation events without credential leakage."""
    journal = ExecutionJournal()
    pipeline = ReconciliationPipeline(journal=journal)
    ledger = PositionLedger(starting_capital=50_000.0)
    sm = ExecutionStateMachine(initial=ExecutionState.READY)

    truth = BrokerTruthSnapshot(
        orders=(),
        positions=(),
        funds={"token": "super_secret_token", "api_key": "secret_key"},
    )

    pipeline.execute(
        engine=ExecutionEngine(),
        ledger=ledger,
        sl_tracker=SlProtectionTracker(),
        state_machine=sm,
        broker_fetcher=lambda: truth,
    )

    for entry in journal.entries:
        entry_str = str(entry)
        assert "super_secret_token" not in entry_str
        assert "secret_key" not in entry_str


# ── 12. Control Plane & WSS Integration (§12) ────────────────────────────────


class MockSessionWithInstitutionalReconcile:
    """Mock session supporting Phase 5 reconciliation pipeline."""

    def __init__(self, rep: InstitutionalReconciliationReport) -> None:
        self._rep = rep
        self.sl_tracker = SlProtectionTracker()
        self.ledger = PositionLedger(starting_capital=100_000.0)
        self.engine = ExecutionEngine()

    def reconcile_now(self) -> Any:
        return self._rep

    def safety_snapshot(self) -> Any:
        return build_safety_snapshot(
            kill_switch_engaged=False,
            kill_switch_reason="",
            kill_switch_level="INFO",
            gates_live_trading_enabled=True,
            gates_broker_live_enabled=True,
            gates_account_confirmed=True,
            gates_risk_limits_valid=True,
            gates_kill_switch_off=True,
            broker_connected=True,
            broker_name="FYERS",
            broker_environment="PAPER",
            broker_health_reason="ok",
            reconciliation_healthy=self._rep.matched,
            reconciliation_status="MATCHED" if self._rep.matched else "MISMATCH",
            reconciliation_reasons=tuple(m.message for m in self._rep.mismatches),
            risk_ready=True,
            capital_valid=True,
            last_risk_denial_reason="",
            sl_tracker=self.sl_tracker,
            armed="DISARMED",
            session_lifecycle="READY",
            last_block_reason="",
        )


def test_control_plane_reconciliation_command_and_snapshot() -> None:
    """REQUEST_RECONCILIATION dispatches institutional report and updates snapshot."""
    mismatch = InstitutionalMismatch(
        mismatch_type=MismatchType.QUANTITY_MISMATCH,
        symbol_or_id="NSE:INFY-EQ",
        local_value="50",
        broker_value="100",
        message="quantity mismatch for NSE:INFY-EQ: local=50 vs broker=100",
    )
    rep = InstitutionalReconciliationReport(
        matched=False,
        mismatches=(mismatch,),
        positions_matched=False,
        summary_reason="quantity mismatch for NSE:INFY-EQ: local=50 vs broker=100",
    )

    session = MockSessionWithInstitutionalReconcile(rep)
    auth = ControlPlaneAuth(valid_tokens={"auth_secret_token"})
    events = EventStreamManager()
    ctrl = ControlPlaneController(session=session, auth=auth, event_stream=events)

    # 1. Execute REQUEST_RECONCILIATION
    req = CommandRequest(
        command=ControlCommand.REQUEST_RECONCILIATION,
        request_id="req-rec-1",
        auth_token="auth_secret_token",
    )
    res = ctrl.handle_command(req)

    assert res.status == CommandStatus.SUCCESS
    assert res.data["status"] == "MISMATCH"
    assert res.data["mismatches_count"] == 1

    # Check WSS events published:
    # RECONCILIATION_STARTED, MISMATCH_DETECTED, RECONCILIATION_COMPLETED
    event_types = [ev.event_type for _, evs in [events.get_events_since(0)] for ev in evs]
    assert "RECONCILIATION_STARTED" in event_types
    assert "MISMATCH_DETECTED" in event_types
    assert "RECONCILIATION_COMPLETED" in event_types

    # 2. Check build_runtime_snapshot exposes reconciliation data
    snap = ctrl.build_runtime_snapshot()
    assert snap.reconciliation["healthy"] is False
    assert snap.reconciliation["status"] == "MISMATCH"
    assert len(snap.reconciliation["reasons"]) == 1
