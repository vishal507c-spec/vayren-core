"""Paper broker + portfolio ledger + reconciliation tests."""

import pytest

from execution.broker.adapter import BrokerError, adapter_supports
from execution.broker.factory import NotConfiguredError, resolve_broker
from execution.broker.paper import PaperBroker
from execution.journal import ExecutionJournal, LatencyTracker
from execution.models.order import Fill, OrderPlan
from execution.modes import ExecutionMode, ModeGates
from execution.portfolio.ledger import PositionLedger
from execution.portfolio.reconcile import (
    ReconciliationState,
    reconcile_orders,
    reconcile_positions,
)


def _plan(**overrides) -> OrderPlan:
    values = {
        "intent_id": "i1",
        "symbol": "RELIANCE",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "MARKET",
    }
    values.update(overrides)
    return OrderPlan(**values)  # type: ignore[arg-type]


def _broker() -> PaperBroker:
    broker = PaperBroker(capital=1_000_000.0)
    broker.connect()
    return broker


def test_paper_capabilities_and_health() -> None:
    broker = PaperBroker()
    assert broker.name == "paper"
    assert adapter_supports(broker, "orders.market")
    assert not adapter_supports(broker, "stream.events")
    assert broker.health() == (False, "not connected")
    broker.connect()
    assert broker.health()[0] is True
    with pytest.raises(ValueError):
        PaperBroker(capital=0.0)


def test_market_fill_math_mirrors_backtest_semantics() -> None:
    broker = _broker()
    broker_id = broker.place_order(_plan(quantity=100.0), "c1")
    assert broker_id.startswith("PAPER-")
    fill = broker.settle("c1", 100.0, "t")
    assert fill is not None and not fill.partial
    assert fill.fill_qty == 100.0
    assert fill.fill_price == pytest.approx(100.0 * 1.0002)  # close + slippage
    assert fill.commission == pytest.approx(fill.fill_price * 100.0 * 0.0003)
    with pytest.raises(BrokerError):
        broker.place_order(_plan(), "c1")  # duplicate client id
    assert broker.settle("c1", 100.0, "t") is None  # already filled
    assert broker.settle("missing", 100.0, "t") is None


def test_limit_prices_and_partial_fills() -> None:
    broker = PaperBroker(capital=100.0)  # tiny capital forces partials
    broker.connect()
    broker.place_order(_plan(quantity=100.0), "c1")
    fill = broker.settle("c1", 100.0, "t")
    assert fill is not None and fill.partial
    assert fill.fill_qty < 100.0
    broker2 = _broker()
    broker2.place_order(_plan(order_type="LIMIT", limit_price=99.0), "c2")
    limited = broker2.settle("c2", 100.0, "t")
    assert limited is not None and limited.fill_price == pytest.approx(99.0)
    broker2.place_order(_plan(order_type="LIMIT", limit_price=120.0, side="SELL"), "c3")
    assert broker2.settle("c3", 100.0, "t") is not None


def test_cancel_modify_and_positions() -> None:
    broker = _broker()
    broker_id = broker.place_order(_plan(quantity=10.0), "c1")
    assert broker.cancel_order(broker_id) is True
    assert broker.cancel_order(broker_id) is False
    assert broker.cancel_order("PAPER-999999") is False
    broker.place_order(_plan(quantity=10.0), "c2")
    assert broker.modify_order("PAPER-000002", quantity=5.0, price=None) is True
    assert broker.modify_order("PAPER-000002", quantity=0.0, price=None) is False
    broker.set_quote("RELIANCE", 100.0)
    broker.place_order(_plan(quantity=10.0), "c3")
    broker.settle("c3", 100.0, "t")
    positions = broker.positions()
    assert positions == [{"symbol": "RELIANCE", "quantity": 10.0}]
    assert broker.account()["mode"] == "PAPER"


def test_factory_defaults_paper_and_live_not_configured() -> None:
    broker, mode, notes = resolve_broker(ExecutionMode.PAPER, ModeGates())
    assert isinstance(broker, PaperBroker) and mode == ExecutionMode.PAPER
    with pytest.raises(NotConfiguredError):
        resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="missing")
    live_broker, live_mode, live_notes = resolve_broker(ExecutionMode.LIVE, ModeGates())
    assert isinstance(live_broker, PaperBroker) and live_mode == ExecutionMode.PAPER
    assert live_notes  # downgrade never silent


def test_ledger_long_lifecycle_and_pnl() -> None:
    ledger = PositionLedger(100_000.0)
    pos = ledger.apply_fill(
        Fill(
            client_order_id="c1",
            broker_order_id="b1",
            symbol="R",
            side="BUY",
            fill_qty=100.0,
            fill_price=100.0,
            commission=3.0,
            timestamp="t",
        )
    )
    assert (pos.quantity, pos.avg_price) == (100.0, 100.0)
    pos = ledger.apply_fill(
        Fill(
            client_order_id="c2",
            broker_order_id="b2",
            symbol="R",
            side="SELL",
            fill_qty=100.0,
            fill_price=110.0,
            commission=3.3,
            timestamp="t",
        )
    )
    assert pos.flat
    assert pos.realized_pnl == pytest.approx(100 * 10.0 - 3.3)
    snapshot = ledger.snapshot({"R": 110.0})
    assert snapshot.day_pnl == pytest.approx(pos.realized_pnl)


def test_reconcile_blocks_live_on_mismatch_only() -> None:
    ledger = PositionLedger(100_000.0)
    ledger.apply_fill(
        Fill(
            client_order_id="c1",
            broker_order_id="b1",
            symbol="R",
            side="BUY",
            fill_qty=10.0,
            fill_price=100.0,
            commission=0.1,
            timestamp="t",
        )
    )
    match = reconcile_positions(tuple(ledger.all_positions()), [{"symbol": "R", "quantity": 10.0}])
    assert match.matched and not match.blocks_live
    mismatch = reconcile_positions(
        tuple(ledger.all_positions()), [{"symbol": "R", "quantity": 5.0}]
    )
    assert not mismatch.matched and mismatch.blocks_live
    assert mismatch.mismatches[0].kind == "position"
    orders_ok = reconcile_orders(("a",), ("a",))
    assert orders_ok.matched
    orders_bad = reconcile_orders(("a",), ("b",))
    assert orders_bad.blocks_live
    state = ReconciliationState(positions=mismatch, orders=orders_ok)
    assert state.blocks_live


def test_journal_and_latency() -> None:
    journal = ExecutionJournal()
    journal.record("SIGNAL_GENERATED", strategy_id="s", signal_id="g1")
    journal.record("RISK_DENIED", intent_id="i1")
    assert len(journal.entries) == 2
    assert len(journal.of_kind("RISK_DENIED")) == 1
    tracker = LatencyTracker()
    for sample in (1.0, 2.0, 3.0, 4.0):
        tracker.observe("risk", sample)
    summary = tracker.summary("risk")
    assert summary["n"] == 4 and summary["max"] == 4.0 and summary["p50"] == 3.0
    assert tracker.summary("missing") == {"n": 0}
    assert tracker.stages() == ("risk",)
