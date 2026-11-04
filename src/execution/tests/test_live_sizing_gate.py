"""Phase-3 LIVE execution gates — sizing from broker capital + final guard.

* LIVE BUY with no broker funds → blocked (RISK_DENIED, no order).
* LIVE BUY with funds + entry/stop → sized by the 0.15% pipeline.
* Final ``_submit`` gate rejects a planner-inflated quantity that would
  push planned risk past the verdict ceiling.
* PAPER path is untouched (legacy default quantity, no stop needed).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution.models.intent import ExecutionIntent, StrategySignal  # noqa: E402
from execution.models.order import OrderPlan  # noqa: E402
from execution.modes import ExecutionMode, LiveArm  # noqa: E402
from execution.runtime.session import LiveSession, SessionConfig  # noqa: E402
from risk import RiskPolicy  # noqa: E402
from risk.sizing import READY, SizingVerdict  # noqa: E402


class _Provider:
    capabilities = ("candles",)

    def open(self, symbols, timeframe) -> None: ...

    def poll(self):  # noqa: ANN204
        return ()


class _Broker:
    name = "fake-live"

    def __init__(self, *, healthy=True, funds=None) -> None:
        self._healthy = healthy
        self._funds = funds
        self.placed: list[str] = []

    def health(self):  # noqa: ANN204
        return (self._healthy, "ok" if self._healthy else "down")

    def funds(self):  # noqa: ANN204
        if self._funds is None:
            raise RuntimeError("funds not reported")
        return dict(self._funds)

    def place_order(self, plan, client_order_id: str) -> str:  # noqa: ANN001, ARG002
        self.placed.append(client_order_id)
        return "BROKER-1"


def _session(*, mode=ExecutionMode.LIVE, broker=None) -> LiveSession:
    from execution.runtime.lifecycle import LifecycleState

    session = LiveSession(
        SessionConfig(mode=mode, journal_path=None, memory_path=None, kill_switch_path=None),
        _Provider(),  # pyright: ignore[reportArgumentType]
        RiskPolicy(max_order_qty=10_000.0, max_position_qty=10_000.0),
        request_id="test-sizing",
    )
    session._mode = mode
    session._armed = LiveArm.ARMED
    session._broker = broker if broker is not None else _Broker()  # pyright: ignore[reportAttributeAccessIssue]
    # Real flow reaches RUNNING before any submission is possible.
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")
    return session


def _signal(price: float, stop_loss: float | None) -> StrategySignal:
    return StrategySignal(
        signal_id="s1",
        strategy_id="OBR C1C4",
        strategy_version="1",
        timestamp="2026-10-06T09:30:00+00:00",
        event_seq=1,
        symbol="KAYNES",
        side="BUY",
        price=price,
        stop_loss=stop_loss,
    )


def _intent(**overrides) -> ExecutionIntent:
    base = {
        "intent_id": "i1",
        "strategy_id": "OBR C1C4",
        "strategy_version": "1",
        "signal_id": "s1",
        "timestamp": "2026-10-06T09:30:00+00:00",
        "event_seq": 1,
        "symbol": "KAYNES",
        "side": "BUY",
        "target_position_qty": 22.0,
        "quantity": 22.0,
    }
    base.update(overrides)
    return ExecutionIntent(**base)


def _plan(**overrides) -> OrderPlan:
    base = {
        "intent_id": "i1",
        "symbol": "KAYNES",
        "side": "BUY",
        "quantity": 22.0,
        "order_type": "MARKET",
    }
    base.update(overrides)
    return OrderPlan(**base)


def test_live_buy_without_broker_funds_is_blocked() -> None:
    broker = _Broker(funds=None)
    session = _session(broker=broker)
    signal = _signal(1218.50, 1245.00)
    assert session._live_sized_quantity("i1", signal, 1_700_000_000.0) is None
    kinds = [entry.kind for entry in session._journal.entries]
    assert "RISK_DENIED" in kinds
    assert broker.placed == []


def test_live_buy_sized_from_broker_capital() -> None:
    session = _session(broker=_Broker(funds={"available": 100_000.0}))
    signal = _signal(1218.50, 1245.00)
    verdict = session._live_sized_quantity("i1", signal, 1_700_000_000.0)
    assert verdict is not None and verdict.ready
    assert (verdict.quantity, verdict.risk_per_share, verdict.planned_risk) == (22, 26.50, 583.0)


def test_live_buy_without_stop_is_blocked() -> None:
    session = _session(broker=_Broker(funds={"available": 100_000.0}))
    signal = _signal(1218.50, None)
    assert session._live_sized_quantity("i1", signal, 1_700_000_000.0) is None


def test_final_gate_rejects_inflated_quantity() -> None:
    broker = _Broker(funds={"available": 100_000.0})
    session = _session(broker=broker)
    session._sized_risk["i1"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=100_000.0,
        effective_capital=400_000.0,
        max_risk_per_stock=600.0,
        entry_price=1218.50,
        stop_price=1245.00,
        risk_per_share=26.50,
        quantity=22,
        planned_risk=583.0,
    )
    session._submit(_intent(), _plan(quantity=30.0), 1_700_000_000.0)  # 30×26.50=₹795
    assert broker.placed == []
    assert tuple(session._engine.open_orders()) == ()
    kinds = [entry.kind for entry in session._journal.entries]
    assert "RISK_DENIED" in kinds


def test_final_gate_passes_sized_quantity() -> None:
    broker = _Broker(funds={"available": 100_000.0})
    session = _session(broker=broker)
    session._sized_risk["i1"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=100_000.0,
        effective_capital=400_000.0,
        max_risk_per_stock=600.0,
        entry_price=1218.50,
        stop_price=1245.00,
        risk_per_share=26.50,
        quantity=22,
        planned_risk=583.0,
    )
    session._submit(_intent(), _plan(quantity=22.0), 1_700_000_000.0)
    assert broker.placed == ["i1:o1"]


def test_pre_order_buying_power_passes_when_funds_sufficient() -> None:
    from typing import Any

    class _MarginBroker(_Broker):
        def order_margin(self, plan: Any) -> float:  # noqa: ARG002
            return 15_000.0

    broker = _MarginBroker(funds={"available": 50_000.0})
    session = _session(broker=broker)
    session._sized_risk["i1"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=50_000.0,
        effective_capital=200_000.0,
        max_risk_per_stock=300.0,
        entry_price=800.0,
        stop_price=803.0,
        risk_per_share=3.0,
        quantity=100,
        planned_risk=300.0,
    )
    session._submit(_intent(), _plan(quantity=100.0), 1_700_000_000.0)
    assert broker.placed == ["i1:o1"]


def test_pre_order_buying_power_blocks_when_margin_exceeds_funds() -> None:
    from typing import Any

    class _MarginBroker(_Broker):
        def order_margin(self, plan: Any) -> float:  # noqa: ARG002
            return 70_000.0

    broker = _MarginBroker(funds={"available": 50_000.0})
    session = _session(broker=broker)
    session._sized_risk["i1"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=50_000.0,
        effective_capital=200_000.0,
        max_risk_per_stock=300.0,
        entry_price=2500.0,
        stop_price=2510.0,
        risk_per_share=10.0,
        quantity=30,
        planned_risk=300.0,
    )
    session._submit(_intent(), _plan(quantity=30.0), 1_700_000_000.0)
    assert broker.placed == []
    kinds = [entry.kind for entry in session._journal.entries]
    assert "ORDER_BLOCKED" in kinds
    blocked_entries = [e for e in session._journal.entries if e.kind == "ORDER_BLOCKED"]
    assert any(
        "insufficient buying power" in str(e.payload.get("reason", "")).lower()
        for e in blocked_entries
    )


def test_independent_per_stock_margin_evaluation_without_fixed_leverage() -> None:
    from typing import Any

    class _MultiStockMarginBroker(_Broker):
        def order_margin(self, plan: Any) -> float:
            rates = {"SBIN": 0.20, "HDFCBANK": 0.40}
            rate = rates.get(plan.symbol, 0.25)
            price = 1000.0
            return float(plan.quantity * price * rate)

    broker = _MultiStockMarginBroker(funds={"available": 25_000.0})
    session = _session(broker=broker)

    # Stock 1: SBIN (100 shares @ 1000, 20% margin = 20,000 <= 25,000) -> PASS
    session._sized_risk["i_sbin"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=25_000.0,
        effective_capital=100_000.0,
        max_risk_per_stock=150.0,
        entry_price=1000.0,
        stop_price=1001.5,
        risk_per_share=1.5,
        quantity=100,
        planned_risk=150.0,
    )
    session._submit(
        _intent(intent_id="i_sbin", symbol="SBIN"),
        _plan(intent_id="i_sbin", symbol="SBIN", quantity=100.0),
        1_700_000_000.0,
    )
    assert "i_sbin:o1" in broker.placed

    # Stock 2: HDFCBANK (100 shares @ 1000, 40% margin = 40,000 > 25,000) -> BLOCKED
    session._sized_risk["i_hdfc"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=25_000.0,
        effective_capital=100_000.0,
        max_risk_per_stock=150.0,
        entry_price=1000.0,
        stop_price=1001.5,
        risk_per_share=1.5,
        quantity=100,
        planned_risk=150.0,
    )
    session._submit(
        _intent(intent_id="i_hdfc", symbol="HDFCBANK"),
        _plan(intent_id="i_hdfc", symbol="HDFCBANK", quantity=100.0),
        1_700_000_000.0,
    )
    assert "i_hdfc:o1" not in broker.placed
