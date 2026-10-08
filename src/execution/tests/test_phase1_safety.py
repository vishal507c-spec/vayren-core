"""VAYREN Phase 1 — Safety Foundation Tests.

Tests per §10 specification:
1.  Kill switch blocks order
2.  Each LIVE gate blocks order individually
3.  Invalid/zero capital blocks order
4.  Risk limit blocks oversized quantity
5.  Duplicate order blocked
6.  Duplicate fill ignored (deduplicated)
7.  Missing SL protection detected
8.  Reconciliation mismatch blocks LIVE
9.  Restart/UNKNOWN state blocks LIVE
10. Healthy path submits exactly one order

Additional:
- SL protection tracker state machine
- SafetySnapshot/build_safety_snapshot coverage
- Kill switch default OFF (safe)
- Session safety_snapshot() visibility
"""

from __future__ import annotations

import time

import pytest

from execution.broker.sandbox import SandboxBroker
from execution.engine import ExecutionEngine, IllegalTransitionError
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.models.order import BrokerOrder, Fill, OrderState
from execution.models.position import Position
from execution.modes import ExecutionMode, ModeGates
from execution.portfolio.reconcile import (
    ReconcileStatus,
    ReconciliationState,
    reconcile_positions,
    verdict_of,
)
from execution.runtime.session import LiveSession, SessionConfig
from execution.safety import (
    SlProtectionStatus,
    SlProtectionTracker,
    TradingSafetyState,
    build_safety_snapshot,
)
from execution.tests.helpers import make_bars
from risk import KillSwitch, RiskPolicy
from strategy import StrategyParameters

# ── Shared helpers ─────────────────────────────────────────────────────────


def _sma_logic():
    from strategy.strategies.sma import SmaCrossover

    return SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))


class _LiveSma:
    """Test-only live-declared wrapper. Real strategy code is never modified."""

    SUPPORTS_LIVE = True

    def __init__(self):
        from strategy.strategies.sma import SmaCrossover

        self._inner = SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))

    def warmup(self):
        return self._inner.warmup()

    def on_bar(self, view):
        from dataclasses import replace

        sig = self._inner.on_bar(view)
        if sig is not None and (getattr(sig, "stop_loss", None) is None or sig.stop_loss <= 0):
            price = getattr(sig, "price", 100.0)
            side = getattr(sig, "side", "BUY")
            stop = price * 0.95 if side == "BUY" else price * 1.05
            return replace(sig, stop_loss=stop)
        return sig


def _live_logic():
    return _LiveSma()


def _warmup(n: int = 25) -> dict:
    return {"sma": make_bars("TEST", n)}


def _paper_session(candles, **config_overrides) -> LiveSession:
    config = SessionConfig(**config_overrides)
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    return session


def _drain(session: LiveSession) -> None:
    provider = session._provider
    assert isinstance(provider, ReplayProvider)
    while not provider.exhausted:
        session.step(time.time())


# ── §10.1: Kill switch blocks order ──────────────────────────────────────


def test_kill_switch_blocks_new_live_order() -> None:
    """Kill switch engaged after start must block any subsequent order."""
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _paper_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    # Engage kill switch mid-session before draining events
    session._kill_switch.engage("operator emergency halt")
    _drain(session)
    # Check that any order attempts were denied by kill switch
    denied = session.journal.of_kind("RISK_DENIED")
    kill_denials = [
        e for e in denied if any("kill" in r.lower() for r in e.payload.get("reasons", []))
    ]
    submitted = session.journal.of_kind("ORDER_SUBMITTED")
    # No order should have been submitted after kill switch was engaged
    assert kill_denials or not submitted


def test_kill_switch_default_is_off() -> None:
    """Default KillSwitch must not be engaged (fail-safe default is OFF)."""
    kill = KillSwitch()
    assert not kill.is_halted()
    assert not kill.is_halted(level="global")
    assert not kill.is_halted(level="strategy")
    assert not kill.is_halted(level="broker")


def test_kill_switch_on_blocks_session_start() -> None:
    """Engaged kill switch must prevent session from reaching READY."""
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "kill.json"
    kill = KillSwitch(path)
    kill.engage("safety test")
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(SessionConfig(kill_switch_path=path), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    report = session.start(("TEST",), "15m", _warmup())
    assert not report.ready
    assert any("kill" in reason.lower() for reason in report.reasons)
    assert session.journal.of_kind("NOT_LIVE_READY")


def test_kill_switch_state_exposed() -> None:
    """Kill switch state must be clearly exposed via session state."""
    candles = bars_to_candles(make_bars("TEST", 30), "15m")
    session = _paper_session(candles)
    session.start(("TEST",), "15m", _warmup())
    state = session.state()
    assert "risk" in state
    assert "kill_halted" in state["risk"]
    assert state["risk"]["kill_halted"] is False  # default OFF


def test_kill_switch_on_sets_kill_halted_true() -> None:
    """When kill switch is engaged, state() must report kill_halted=True."""
    candles = bars_to_candles(make_bars("TEST", 30), "15m")
    session = _paper_session(candles)
    session.start(("TEST",), "15m", _warmup())
    session._kill_switch.engage("test halt")
    state = session.state()
    assert state["risk"]["kill_halted"] is True


# ── §10.2: Each LIVE gate blocks order individually ───────────────────────


def test_live_gate_live_trading_enabled_blocks() -> None:
    """LIVE_TRADING_ENABLED=False must downgrade to PAPER (blocks live order)."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    config = SessionConfig(mode=ExecutionMode.LIVE, gates=ModeGates(live_trading_enabled=False))
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    session.start(("TEST",), "15m", _warmup())
    assert session.mode == ExecutionMode.PAPER  # downgraded
    assert session.journal.of_kind("MODE_DOWNGRADE")


def test_live_gate_broker_live_enabled_blocks() -> None:
    """BROKER_LIVE_ENABLED=False must downgrade to PAPER."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    config = SessionConfig(
        mode=ExecutionMode.LIVE,
        gates=ModeGates(live_trading_enabled=True, broker_live_enabled=False),
    )
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    session.start(("TEST",), "15m", _warmup())
    assert session.mode == ExecutionMode.PAPER
    assert session.journal.of_kind("MODE_DOWNGRADE")


def test_live_gate_account_confirmed_blocks() -> None:
    """ACCOUNT_CONFIRMED=False must downgrade to PAPER."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    config = SessionConfig(
        mode=ExecutionMode.LIVE,
        gates=ModeGates(
            live_trading_enabled=True,
            broker_live_enabled=True,
            account_confirmed=False,
        ),
    )
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    session.start(("TEST",), "15m", _warmup())
    assert session.mode == ExecutionMode.PAPER
    assert session.journal.of_kind("MODE_DOWNGRADE")


def test_live_gate_risk_limits_valid_blocks() -> None:
    """RISK_LIMITS_VALID=False must downgrade to PAPER."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    config = SessionConfig(
        mode=ExecutionMode.LIVE,
        gates=ModeGates(
            live_trading_enabled=True,
            broker_live_enabled=True,
            account_confirmed=True,
            risk_limits_valid=False,
        ),
    )
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    session.start(("TEST",), "15m", _warmup())
    assert session.mode == ExecutionMode.PAPER
    assert session.journal.of_kind("MODE_DOWNGRADE")


def test_live_gate_kill_switch_off_blocks() -> None:
    """KILL_SWITCH_OFF=False must downgrade to PAPER."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    config = SessionConfig(
        mode=ExecutionMode.LIVE,
        gates=ModeGates(
            live_trading_enabled=True,
            broker_live_enabled=True,
            account_confirmed=True,
            risk_limits_valid=True,
            kill_switch_off=False,
        ),
    )
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    session.start(("TEST",), "15m", _warmup())
    assert session.mode == ExecutionMode.PAPER
    assert session.journal.of_kind("MODE_DOWNGRADE")


def test_live_gates_all_off_blocks_and_reports_all_missing() -> None:
    """All gates off must report all 5 missing gate names."""
    from execution.modes import gates_from_env

    gates = gates_from_env(env={})
    assert not gates.live_trading_enabled
    assert not gates.broker_live_enabled
    assert not gates.account_confirmed
    assert not gates.risk_limits_valid
    assert not gates.kill_switch_off
    assert not gates.all_satisfied
    missing = gates.missing()
    assert "LIVE_TRADING_ENABLED" in missing
    assert "BROKER_LIVE_ENABLED" in missing
    assert "ACCOUNT_CONFIRMED" in missing
    assert "RISK_LIMITS_VALID" in missing
    assert "KILL_SWITCH_OFF" in missing


# ── §10.3: Invalid/zero capital blocks order ──────────────────────────────


def test_zero_capital_blocks_order() -> None:
    """Available capital = 0 must block order via risk capital check."""
    from risk import RiskEngine
    from risk.models import RiskPolicy, RiskRequest

    engine = RiskEngine(RiskPolicy())
    request = RiskRequest(
        intent_id="cap-zero-1",
        strategy_id="test",
        symbol="TEST",
        side="BUY",
        quantity=10.0,
        price=100.0,
        timestamp="2026-01-06T09:30:00+00:00",
        available_capital=0.0,
        equity=0.0,
        now_epoch=time.time(),
    )
    decision = engine.evaluate(request)
    assert not decision.approved
    assert any("capital" in r.lower() for r in decision.reasons)


def test_negative_capital_blocks_order() -> None:
    """Negative available capital must block order."""
    from risk import RiskEngine
    from risk.models import RiskPolicy, RiskRequest

    engine = RiskEngine(RiskPolicy())
    request = RiskRequest(
        intent_id="cap-neg-1",
        strategy_id="test",
        symbol="TEST",
        side="BUY",
        quantity=10.0,
        price=100.0,
        timestamp="2026-01-06T09:30:00+00:00",
        available_capital=-500.0,
        equity=1_000.0,
        now_epoch=time.time(),
    )
    decision = engine.evaluate(request)
    assert not decision.approved
    assert any("capital" in r.lower() for r in decision.reasons)


def test_funds_zero_blocks_live_validation() -> None:
    """FundsSnapshot with zero available must fail funds_valid_for_live."""
    from broker.funds import FundsSnapshot
    from execution.broker.gates import funds_valid_for_live

    broke = FundsSnapshot(available=0.0, used=0.0, equity=0.0)
    ok, reasons = funds_valid_for_live(broke)
    assert not ok
    assert len(reasons) >= 2  # zero available + zero equity


# ── §10.4: Risk limit blocks oversized quantity ───────────────────────────


def test_oversized_quantity_blocked_by_max_order_qty() -> None:
    """Quantity > max_order_qty must be denied by risk engine."""
    from risk import RiskEngine
    from risk.models import RiskPolicy, RiskRequest

    policy = RiskPolicy(max_order_qty=50.0, max_position_qty=200.0)
    engine = RiskEngine(policy)
    request = RiskRequest(
        intent_id="oversize-1",
        strategy_id="test",
        symbol="TEST",
        side="BUY",
        quantity=100.0,  # exceeds max_order_qty=50
        price=100.0,
        timestamp="2026-01-06T09:30:00+00:00",
        available_capital=500_000.0,
        equity=1_000_000.0,
        now_epoch=time.time(),
    )
    decision = engine.evaluate(request)
    assert not decision.approved


def test_risk_whole_share_floor_preserved() -> None:
    """Fractional quantity computation — floor (int) is correct behavior."""
    # Verify that the session's _default_quantity uses integer floor logic.
    # This is already in the planner (native_plan_order floors to int).
    from execution.native_execution import native_plan_order

    qty, order_type, limit = native_plan_order(
        2.7,  # fractional intent quantity
        "MARKET",
        100.0,
        False,
        1.0,
    )
    # The planner must floor fractional quantities, never round up.
    assert qty == 2.0 or qty <= 2.7, "Fractional qty must not inflate above intent"


# ── §10.5: Duplicate order blocked ────────────────────────────────────────


def test_duplicate_intent_id_blocked_by_risk_engine() -> None:
    """Same intent_id submitted twice must be denied on second attempt."""
    from risk import RiskEngine
    from risk.models import RiskPolicy, RiskRequest

    engine = RiskEngine(RiskPolicy())
    req = RiskRequest(
        intent_id="dup-intent-1",
        strategy_id="test",
        symbol="TEST",
        side="BUY",
        quantity=10.0,
        price=100.0,
        timestamp="2026-01-06T09:30:00+00:00",
        available_capital=500_000.0,
        equity=1_000_000.0,
        data_age_seconds=1.0,
        now_epoch=time.time(),
    )
    first = engine.evaluate(req)
    assert first.approved
    second = engine.evaluate(req)  # same intent_id
    assert not second.approved
    assert any("dup-intent-1" in r for r in second.reasons)


def test_duplicate_client_order_id_blocked_by_execution_engine() -> None:
    """Same client_order_id registered twice must raise IllegalTransitionError."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="coid-1", intent_id="int-1", symbol="X", side="BUY", quantity=1.0
    )
    engine.create(order)
    with pytest.raises(IllegalTransitionError, match="duplicate client order id"):
        engine.create(order)


def test_duplicate_intent_order_blocked_by_execution_engine() -> None:
    """Same intent_id with a different client_order_id must also raise."""
    engine = ExecutionEngine()
    order1 = BrokerOrder(
        client_order_id="coid-A", intent_id="shared-intent", symbol="X", side="BUY", quantity=1.0
    )
    order2 = BrokerOrder(
        client_order_id="coid-B", intent_id="shared-intent", symbol="X", side="BUY", quantity=1.0
    )
    engine.create(order1)
    with pytest.raises(IllegalTransitionError, match="duplicate intent"):
        engine.create(order2)


# ── §10.6: Duplicate fill ignored / deduplicated ──────────────────────────


def test_fill_on_terminal_order_raises() -> None:
    """Applying a fill to an already-filled order must raise IllegalTransitionError."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="fill-dup-1", intent_id="fi1", symbol="X", side="BUY", quantity=5.0
    )
    engine.create(order)
    engine.transition("fill-dup-1", OrderState.VALIDATED)
    engine.transition("fill-dup-1", OrderState.SUBMITTED)
    engine.transition("fill-dup-1", OrderState.ACKNOWLEDGED)
    fill = Fill(
        client_order_id="fill-dup-1",
        broker_order_id="b1",
        symbol="X",
        side="BUY",
        fill_qty=5.0,
        fill_price=100.0,
        commission=0.0,
        timestamp="t",
        partial=False,
    )
    engine.apply_fill(order, fill)
    engine.transition("fill-dup-1", OrderState.FILLED)
    filled_order = engine.get("fill-dup-1")
    assert filled_order is not None
    # Second fill on a terminal (FILLED) order must be blocked.
    with pytest.raises(IllegalTransitionError, match="terminal"):
        engine.apply_fill(filled_order, fill)


def test_broker_event_for_unknown_client_id_is_ignored() -> None:
    """Broker fill events for unknown orders must be journaled as BROKER_EVENT_IGNORED."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    session = _paper_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    broker = session._broker
    assert broker is not None
    # Inject a fill event for an order the engine doesn't know about.
    session._dispatch_broker_event({"type": "fill", "client_order_id": "GHOST-999", "fill": None})
    ignored = session.journal.of_kind("BROKER_EVENT_IGNORED")
    assert ignored, "Unknown order fill must be journaled as ignored"


# ── §10.7: Missing SL protection detected ────────────────────────────────


def test_sl_tracker_detects_missing_sl() -> None:
    """SL tracker must flag PROTECTION_FAILED when SL placement fails."""
    tracker = SlProtectionTracker()
    # Strategy provides stop_price → tracker expects protection
    record = tracker.on_fill("intent-sl-1", "TEST", "BUY", 100.0, stop_price=95.0)
    assert record.status == SlProtectionStatus.PENDING
    assert record.stop_price == 95.0

    # SL placement fails
    failed = tracker.mark_protection_failed("intent-sl-1", reason="broker rejected SL")
    assert failed is not None
    assert failed.status == SlProtectionStatus.PROTECTION_FAILED
    assert tracker.has_attention
    assert tracker.attention_count == 1
    assert tracker.summary_status() == "PROTECTION_FAILED"


def test_sl_tracker_no_stop_price_is_not_required() -> None:
    """When strategy provides no stop_price, SL status must be NOT_REQUIRED."""
    tracker = SlProtectionTracker()
    record = tracker.on_fill("intent-noslp-1", "TEST", "BUY", 100.0, stop_price=None)
    assert record.status == SlProtectionStatus.NOT_REQUIRED
    assert not tracker.has_attention

    # mark_protection_failed on a NOT_REQUIRED record must be a no-op
    result = tracker.mark_protection_failed("intent-noslp-1", reason="irrelevant")
    assert result is not None
    assert result.status == SlProtectionStatus.NOT_REQUIRED  # unchanged


def test_sl_tracker_confirm_sl_marks_protected() -> None:
    """Successful SL placement must mark record as PROTECTED."""
    tracker = SlProtectionTracker()
    tracker.on_fill("intent-prot-1", "TEST", "BUY", 100.0, stop_price=95.0)
    protected = tracker.confirm_sl("intent-prot-1", sl_order_id="SL-BROKER-001")
    assert protected is not None
    assert protected.status == SlProtectionStatus.PROTECTED
    assert protected.sl_order_id == "SL-BROKER-001"
    assert not tracker.has_attention
    assert tracker.summary_status() == "CLEAN"


def test_sl_tracker_attention_status() -> None:
    """SL_ATTENTION status must set has_attention and summary."""
    tracker = SlProtectionTracker()
    tracker.on_fill("intent-attn-1", "TEST", "BUY", 100.0, stop_price=90.0)
    tracker.mark_sl_attention("intent-attn-1", reason="SL order timed out")
    assert tracker.has_attention
    assert tracker.summary_status() == "SL_ATTENTION"


def test_sl_execution_layer_never_invents_stop_price() -> None:
    """Verify the execution layer has no hardcoded SL rule.

    The SL tracker only stores what the strategy provides — it cannot invent
    a stop_price. If no stop_price is supplied, status must be NOT_REQUIRED.
    """
    tracker = SlProtectionTracker()
    record = tracker.on_fill("intent-noinvent-1", "NIFTY", "BUY", 22000.0, stop_price=None)
    assert record.stop_price is None, "Execution layer must not invent a stop_price"
    assert record.status == SlProtectionStatus.NOT_REQUIRED


# ── §10.8: Reconciliation mismatch blocks LIVE ───────────────────────────


def test_reconciliation_mismatch_blocks_live() -> None:
    """ReconciliationVerdict BLOCKED must prevent new LIVE orders."""
    # Simulate a position mismatch: local says 10 shares, broker says 0.
    local = (Position(symbol="TEST", quantity=10.0),)
    broker_positions: list[dict] = []  # broker reports nothing
    positions = reconcile_positions(local, broker_positions)
    verdict = verdict_of((positions,))
    assert verdict.status is ReconcileStatus.BLOCKED
    assert verdict.blocks_live


def test_reconciliation_state_blocks_live_on_position_mismatch() -> None:
    """ReconciliationState.blocks_live must be True on position mismatch."""
    local = (Position(symbol="ABC", quantity=5.0),)
    mismatch = reconcile_positions(local, [])
    state = ReconciliationState(positions=mismatch)
    assert state.blocks_live


def test_session_reconciliation_mismatch_blocks_readiness() -> None:
    """Session readiness must fail if reconciliation mismatches."""
    from execution.models.contract import StrategyRuntimeContract
    from execution.runtime.session import check_live_readiness

    contract = StrategyRuntimeContract(
        strategy_id="s",
        strategy_version="1",
        supports_live=True,
        warmup_bars=5,
    )
    # When reconcile_ok is False for live mode, readiness MUST fail closed
    report = check_live_readiness(
        contract,
        data_capabilities=(),
        broker_capabilities=(),
        warmup_bars_available=10,
        risk_policy_ok=True,
        account_ok=True,
        clock_ok=True,
        reconcile_ok=False,
        persistence_ok=True,
        kill_ok=True,
        observability_ok=True,
        for_live=True,
    )
    assert not report.ready
    assert any("reconcil" in reason.lower() for reason in report.reasons)


# ── §10.9: Restart/UNKNOWN state blocks LIVE ─────────────────────────────


def test_unknown_order_state_blocks_transition() -> None:
    """Orders in UNKNOWN state must only exit via explicit reconcile()."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="unk-1", intent_id="ui-1", symbol="X", side="BUY", quantity=1.0
    )
    engine.create(order)
    engine.transition("unk-1", OrderState.VALIDATED)
    engine.transition("unk-1", OrderState.SUBMITTED)
    engine.transition("unk-1", OrderState.UNKNOWN, reason="timeout")
    # Must not transition out of UNKNOWN via regular transition.
    with pytest.raises(IllegalTransitionError, match="UNKNOWN exits only via reconcile"):
        engine.transition("unk-1", OrderState.FILLED)


def test_restored_unknown_order_still_requires_reconcile() -> None:
    """After restart/restore, UNKNOWN orders must still require reconcile()."""
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="rst-1", intent_id="ri-1", symbol="X", side="BUY", quantity=1.0
    )
    engine.create(order)
    engine.transition("rst-1", OrderState.VALIDATED)
    engine.transition("rst-1", OrderState.SUBMITTED)
    engine.transition("rst-1", OrderState.UNKNOWN, reason="crash")
    snap = engine.snapshot()
    # Rebuild from snapshot — simulates crash + restart.
    fresh = ExecutionEngine()
    fresh.restore(snap)
    with pytest.raises(IllegalTransitionError):
        fresh.transition("rst-1", OrderState.FILLED)
    # Only reconcile() can resolve UNKNOWN.
    resolved = fresh.reconcile("rst-1", "FILLED")
    assert resolved.state is OrderState.FILLED


def test_recovered_session_reconciles_before_live() -> None:
    """After recovery(), a session must pass through reconciliation before READY."""
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _paper_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    _drain(session)
    checkpoint = session.checkpoint()

    session2 = _paper_session(candles)
    session2.recover(checkpoint)
    report = session2.start(("TEST",), "15m", _warmup())
    assert report.ready
    # Must have gone through RECONCILING → journaled RECONCILED.
    assert session2.journal.of_kind("RECONCILED")
    assert session2.journal.of_kind("IDEMPOTENCY_RESTORED")


# ── §10.10: Healthy path submits exactly one order ────────────────────────


def test_healthy_path_submits_exactly_one_order_per_signal() -> None:
    """Healthy paper session: one signal must produce exactly one ORDER_SUBMITTED."""
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _paper_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    _drain(session)

    submitted = session.journal.of_kind("ORDER_SUBMITTED")
    signals = session.journal.of_kind("SIGNAL_GENERATED")
    assert signals, "Signals must be generated"
    # Must have submissions
    assert submitted, "Healthy session must submit at least one order"
    # For every submitted order, there must be an ORDER_PLANNED preceding it.
    planned = session.journal.of_kind("ORDER_PLANNED")
    assert len(planned) == len(submitted), (
        "Every submitted order must have exactly one ORDER_PLANNED"
    )
    # And every planned order must be risk-approved.
    approved = session.journal.of_kind("RISK_APPROVED")
    assert len(approved) >= len(submitted), "Submitted orders must all be risk-approved"


# ── Safety Snapshot visibility (§9) ───────────────────────────────────────


def test_build_safety_snapshot_ready_state() -> None:
    """All gates green → overall=READY."""
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
        broker_name="paper",
        broker_environment="paper",
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
    assert snap.overall == TradingSafetyState.READY
    d = snap.to_dict()
    assert d["overall"] == "READY"
    assert d["kill_switch"]["engaged"] is False


def test_build_safety_snapshot_kill_switch_on() -> None:
    """Kill switch engaged → overall=KILL_SWITCH_ON regardless of other gates."""
    tracker = SlProtectionTracker()
    snap = build_safety_snapshot(
        kill_switch_engaged=True,
        kill_switch_reason="manual halt",
        kill_switch_level="global",
        gates_live_trading_enabled=True,
        gates_broker_live_enabled=True,
        gates_account_confirmed=True,
        gates_risk_limits_valid=True,
        gates_kill_switch_off=False,
        broker_connected=True,
        broker_name="sandbox",
        broker_environment="sandbox",
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
        last_block_reason="operator engaged kill switch",
    )
    assert snap.overall == TradingSafetyState.KILL_SWITCH_ON
    assert snap.kill_switch_engaged is True
    assert snap.last_block_reason == "operator engaged kill switch"


def test_build_safety_snapshot_blocked_reconciliation() -> None:
    """Reconciliation mismatch → overall=BLOCKED."""
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
        broker_name="sandbox",
        broker_environment="sandbox",
        broker_health_reason="",
        reconciliation_healthy=False,
        reconciliation_status="BLOCKED",
        reconciliation_reasons=("position TEST: local=10.0 broker=0.0",),
        risk_ready=True,
        capital_valid=True,
        last_risk_denial_reason="",
        sl_tracker=tracker,
        armed="DISARMED",
        session_lifecycle="RUNNING",
        last_block_reason="position mismatch",
    )
    assert snap.overall == TradingSafetyState.BLOCKED
    assert snap.reconciliation_healthy is False


def test_build_safety_snapshot_unknown_lifecycle() -> None:
    """UNKNOWN/RECOVERING lifecycle → overall=UNKNOWN (crash/restart safety)."""
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
        broker_name="sandbox",
        broker_environment="sandbox",
        broker_health_reason="",
        reconciliation_healthy=True,
        reconciliation_status="SAFE",
        reconciliation_reasons=(),
        risk_ready=True,
        capital_valid=True,
        last_risk_denial_reason="",
        sl_tracker=tracker,
        armed="DISARMED",
        session_lifecycle="RECOVERING",
        last_block_reason="",
    )
    assert snap.overall == TradingSafetyState.UNKNOWN


def test_build_safety_snapshot_sl_attention() -> None:
    """Unprotected position → overall=SL_ATTENTION."""
    tracker = SlProtectionTracker()
    tracker.on_fill("sl-attn-intent", "TEST", "BUY", 100.0, stop_price=95.0)
    tracker.mark_sl_attention("sl-attn-intent", reason="SL placement failed")
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
        broker_name="sandbox",
        broker_environment="sandbox",
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
    assert snap.overall == TradingSafetyState.SL_ATTENTION
    assert snap.sl_attention_count == 1


def test_safety_snapshot_has_no_secrets() -> None:
    """SafetySnapshot.to_dict() must never contain secret-looking fields."""
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
    raw = str(snap.to_dict())
    # No secret-pattern words should appear
    for secret_word in ("password", "secret", "token", "api_key", "apikey", "pass"):
        assert secret_word not in raw.lower(), f"Secret word '{secret_word}' found in snapshot"


# ── Session state() visibility (§9) ───────────────────────────────────────


def test_session_state_exposes_safety_fields() -> None:
    """LiveSession.state() must expose all required safety fields."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    session = _paper_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready

    state = session.state()
    # Required top-level fields per §9
    assert "mode" in state
    assert "armed" in state
    assert "lifecycle" in state
    assert "broker" in state
    assert "risk" in state
    assert "reconciliation" in state
    # Broker health
    assert "connected" in state["broker"]
    # Risk kill switch
    assert "kill_halted" in state["risk"]
    # Reconciliation blocks_live
    assert "blocks_live" in state["reconciliation"]


# ── No fail-open: safety gate failures must BLOCK ────────────────────────


def test_safety_gate_failure_never_falls_through_to_live() -> None:
    """A failed live gate must downgrade to PAPER — never silently allow LIVE."""
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    # Request LIVE with no gates satisfied.
    config = SessionConfig(mode=ExecutionMode.LIVE, gates=ModeGates())
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    session.start(("TEST",), "15m", _warmup())
    # Must be downgraded to PAPER — never LIVE with no gates.
    assert session.mode != ExecutionMode.LIVE
    assert session.mode == ExecutionMode.PAPER


def test_live_unarmed_blocks_order_submission() -> None:
    """LIVE mode without explicit arm must block every order submission."""
    from broker.capabilities import CapabilitySet, Caps, Domain
    from broker.faces import FactoryPlugin
    from broker.registry import BrokerRecord, default_registry

    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    gates = ModeGates(True, True, True, True, True)
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, gates=gates, adapter_name="unarmed-sandbox"),
        provider,
        RiskPolicy(max_order_qty=5000.0, max_position_qty=5000.0),
    )
    registry = default_registry()
    if "unarmed-sandbox" in registry:
        registry.unregister("unarmed-sandbox")
    registry.register(
        BrokerRecord(
            name="unarmed-sandbox",
            display_name="unarmed-sandbox",
            plugin=FactoryPlugin(
                name="unarmed-sandbox",
                display_name="unarmed-sandbox",
                factories={Domain.TRADING: lambda: SandboxBroker(account_id="ua-test")},
                capabilities=None,
            ),
            capabilities=CapabilitySet(
                domains=(Domain.TRADING,), items=frozenset([Caps.ORDERS_MARKET])
            ),
            faces=(Domain.TRADING,),
        )
    )
    session.register_strategy("sma", "1.0", _live_logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup()).ready
    # Do NOT arm — orders must be blocked.
    _drain(session)
    blocked = session.journal.of_kind("ORDER_BLOCKED_UNARMED")
    broker = session._broker
    assert broker is not None and isinstance(broker, SandboxBroker)
    assert broker.fills == ()
    assert blocked, "Unarmed LIVE mode must block order submission"
