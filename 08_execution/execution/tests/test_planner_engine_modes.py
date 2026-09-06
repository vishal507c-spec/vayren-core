"""Planner, engine state machine and mode-gate tests."""

import pytest

from execution.engine import ExecutionEngine, IllegalTransitionError
from execution.models.intent import ExecutionIntent, make_intent_id
from execution.models.order import BrokerOrder, Fill, OrderState
from execution.modes import ExecutionMode, ModeGates, gates_from_env, resolve_mode
from execution.planner import ExecutionPreferences, OrderPlanner


def _intent(**overrides) -> ExecutionIntent:
    values = {
        "intent_id": "s:1.0:10:1",
        "strategy_id": "s",
        "strategy_version": "1.0",
        "signal_id": "sig-1",
        "timestamp": "2026-01-06T10:00:00+00:00",
        "event_seq": 10,
        "symbol": "RELIANCE",
        "side": "BUY",
        "target_position_qty": 100.0,
        "quantity": 100.0,
    }
    values.update(overrides)
    return ExecutionIntent(**values)  # type: ignore[arg-type]


def test_intent_ids_are_deterministic() -> None:
    assert make_intent_id("s", "1.0", 10, 1) == make_intent_id("s", "1.0", 10, 1)
    assert make_intent_id("s", "1.0", 10, 1) != make_intent_id("s", "1.0", 10, 2)
    assert make_intent_id("s", "1.0", 10, 1) != make_intent_id("s", "1.0", 11, 1)


def test_planner_is_deterministic_and_honors_preferences() -> None:
    planner = OrderPlanner()
    first = planner.plan(_intent(), 100.0)
    assert planner.plan(_intent(), 100.0) == first
    assert first.order_type == "MARKET" and first.limit_price is None
    limited = planner.plan(_intent(), 100.0, ExecutionPreferences(prefer_limit=True))
    assert limited.order_type == "LIMIT" and limited.limit_price == 100.0
    shrunk = planner.plan(_intent(), 100.0, ExecutionPreferences(size_multiplier=0.5))
    assert shrunk.quantity == 50.0
    with pytest.raises(ValueError):
        ExecutionPreferences(size_multiplier=1.5)
    with pytest.raises(ValueError):
        ExecutionPreferences(size_multiplier=0.0)


def test_engine_full_lifecycle_walk() -> None:
    engine = ExecutionEngine()
    order = BrokerOrder(client_order_id="c1", intent_id="i1", symbol="R", side="BUY", quantity=10.0)
    created = engine.create(order)
    assert created.state == OrderState.CREATED
    assert len(created.history) == 1
    validated = engine.transition("c1", OrderState.VALIDATED)
    submitted = engine.transition("c1", OrderState.SUBMITTED)
    acked = engine.transition("c1", OrderState.ACKNOWLEDGED)
    assert (created.state, validated.state, submitted.state, acked.state) == (
        OrderState.CREATED,
        OrderState.VALIDATED,
        OrderState.SUBMITTED,
        OrderState.ACKNOWLEDGED,
    )
    partial = engine.apply_fill(
        acked,
        Fill(
            client_order_id="c1",
            broker_order_id="b1",
            symbol="R",
            side="BUY",
            fill_qty=4.0,
            fill_price=100.0,
            commission=0.1,
            timestamp="t",
            partial=True,
        ),
    )
    assert partial.filled_qty == 4.0 and partial.avg_fill_price == 100.0
    engine.transition("c1", OrderState.PARTIALLY_FILLED)
    rest = engine.apply_fill(
        partial,
        Fill(
            client_order_id="c1",
            broker_order_id="b1",
            symbol="R",
            side="BUY",
            fill_qty=6.0,
            fill_price=102.0,
            commission=0.1,
            timestamp="t",
        ),
    )
    assert rest.filled_qty == 10.0
    assert rest.avg_fill_price == pytest.approx((4 * 100.0 + 6 * 102.0) / 10.0)
    filled = engine.transition("c1", OrderState.FILLED)
    assert filled.state == OrderState.FILLED
    assert engine.open_orders() == ()


def test_engine_rejects_illegal_transitions() -> None:
    engine = ExecutionEngine()
    order = BrokerOrder(client_order_id="c1", intent_id="i1", symbol="R", side="BUY", quantity=10.0)
    engine.create(order)
    with pytest.raises(IllegalTransitionError):
        engine.transition("c1", OrderState.FILLED)  # skip ahead
    with pytest.raises(IllegalTransitionError):
        engine.transition("nope", OrderState.VALIDATED)
    with pytest.raises(IllegalTransitionError):
        engine.create(order)  # duplicate client id
    other = BrokerOrder(client_order_id="c2", intent_id="i1", symbol="R", side="BUY", quantity=1.0)
    with pytest.raises(IllegalTransitionError):
        engine.create(other)  # duplicate intent


def test_unknown_exits_only_via_reconcile() -> None:
    engine = ExecutionEngine()
    order = BrokerOrder(client_order_id="c1", intent_id="i1", symbol="R", side="BUY", quantity=10.0)
    engine.create(order)
    engine.transition("c1", OrderState.VALIDATED)
    engine.transition("c1", OrderState.SUBMITTED)
    engine.transition("c1", OrderState.UNKNOWN, reason="lost ack")
    with pytest.raises(IllegalTransitionError):
        engine.transition("c1", OrderState.FILLED)  # never blind-resolve
    resolved = engine.reconcile("c1", "FILLED", broker_filled_qty=10.0, reason="broker snapshot")
    assert resolved.state == OrderState.FILLED
    assert resolved.filled_qty == 10.0
    with pytest.raises(IllegalTransitionError):
        engine.reconcile("c1", "UNKNOWN")
    with pytest.raises(IllegalTransitionError):
        engine.reconcile("c1", "NOT_A_STATE")


def test_modes_default_paper_and_gates() -> None:
    assert resolve_mode(ExecutionMode.PAPER, ModeGates()) == (ExecutionMode.PAPER, ())
    mode, notes = resolve_mode(ExecutionMode.LIVE, ModeGates())
    assert mode == ExecutionMode.PAPER
    assert len(notes) == 5  # every missing gate reported, never silent
    full = ModeGates(True, True, True, True, True)
    assert full.all_satisfied
    assert resolve_mode(ExecutionMode.LIVE, full) == (ExecutionMode.LIVE, ())
    assert resolve_mode(ExecutionMode.SANDBOX, ModeGates()) == (ExecutionMode.SANDBOX, ())


def test_gates_parse_strictly() -> None:
    assert gates_from_env({}).all_satisfied is False
    tricky = {
        "LIVE_TRADING_ENABLED": "1",
        "BROKER_LIVE_ENABLED": "yes",
        "ACCOUNT_CONFIRMED": "TRUE",
        "RISK_LIMITS_VALID": " true ",
        "KILL_SWITCH_OFF": "True",
    }
    gates = gates_from_env(tricky)
    assert gates.live_trading_enabled is False  # "1" is not an explicit true
    assert gates.broker_live_enabled is False  # "yes" is not an explicit true
    assert gates.account_confirmed is True
