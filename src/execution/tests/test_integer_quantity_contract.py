"""Regression tests enforcing the Integer Quantity Contract and elimination of float scaling.

Guarantees:
1. Risk-approved integer quantities (1, 2, 3, etc.) are NEVER modified into fractions.
2. Downstream execution logic (OrderPlanner, adaptive layers) cannot multiply integer quantities
   by float scaling factors (e.g. 0.5x).
3. Integer floor division: quantity = int(math.floor(max_risk_budget / risk_per_share)).
4. If quantity >= 1: Forwarded and submitted to broker as-is without downward modification.
5. If quantity < 1: Cleanly skipped/aborted before dispatching to the broker venue.
"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution.events import CandleEvent  # noqa: E402
from execution.models.contract import StrategyRuntimeContract  # noqa: E402
from execution.models.intent import ExecutionIntent  # noqa: E402
from execution.models.order import OrderPlan  # noqa: E402
from execution.modes import ExecutionMode, LiveArm  # noqa: E402
from execution.planner import ExecutionPreferences, OrderPlanner  # noqa: E402
from execution.runtime.lifecycle import LifecycleState  # noqa: E402
from execution.runtime.session import LiveSession, SessionConfig  # noqa: E402
from execution.runtime.strategy_runtime import StrategyContext  # noqa: E402
from risk import RiskPolicy  # noqa: E402
from strategy import StrategyParameters  # noqa: E402
from strategy.models.signal import Signal, SignalKind  # noqa: E402


def _make_intent(quantity: float, side: str = "BUY", symbol: str = "NSE:TEST") -> ExecutionIntent:
    return ExecutionIntent(
        intent_id="test:1:1:1",
        strategy_id="test_strat",
        strategy_version="1.0",
        signal_id="sig_1",
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        event_seq=1,
        symbol=symbol,
        side=side,
        target_position_qty=quantity,
        quantity=quantity,
    )


# ── TEST SUITE 1: OrderPlanner eliminates adaptive float scaling ─────────────


def test_order_planner_preserves_quantity_one_without_fractional_scaling() -> None:
    planner = OrderPlanner()
    intent = _make_intent(quantity=1.0)

    # Even with adaptive cautious posture (size_multiplier = 0.5), quantity must stay 1
    prefs_reduced = ExecutionPreferences(prefer_limit=False, size_multiplier=0.5)
    plan = planner.plan(intent, reference_price=100.0, preferences=prefs_reduced)

    assert plan.quantity == 1.0, f"Expected 1.0, got {plan.quantity}"
    assert float(plan.quantity) == int(plan.quantity), "Quantity must be an integer unit"
    assert plan.quantity != 0.5, "Quantity must NEVER be shrunk to 0.5 fractional share"


def test_order_planner_preserves_quantity_two_and_three() -> None:
    planner = OrderPlanner()

    for target_qty in (2.0, 3.0, 5.0, 10.0):
        intent = _make_intent(quantity=target_qty)
        # Test with various shrink multipliers that previously caused fractional shares
        for mult in (0.5, 0.25, 0.75):
            prefs = ExecutionPreferences(prefer_limit=False, size_multiplier=mult)
            plan = planner.plan(intent, reference_price=100.0, preferences=prefs)

            assert plan.quantity == target_qty, (
                f"Expected {target_qty}, got {plan.quantity} with multiplier {mult}"
            )
            assert float(plan.quantity) == int(plan.quantity), "Must be integer units"


def test_order_planner_aborts_sub_unit_quantities() -> None:
    planner = OrderPlanner()

    for sub_qty in (0.0, 0.5, 0.8, -1.0):
        intent = _make_intent(quantity=sub_qty)
        plan = planner.plan(intent, reference_price=100.0)
        assert plan.quantity == 0.0, f"Sub-unit quantity {sub_qty} must plan as 0.0"


# ── TEST SUITE 2: Pre-order safety checks reject sub-unit & fractions ─────────


class _StubBroker:
    name = "stub"

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def order_stream_health(self) -> tuple[bool, str]:
        return True, "ok"

    def funds(self) -> dict[str, float]:
        return {"available": 1000.0}

    def order_margin(self, plan: OrderPlan) -> float:
        return float(plan.quantity) * 10.0


def test_pre_order_checks_rejects_quantities_less_than_one() -> None:
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, journal_path=None, memory_path=None),
        cast(Any, _MockProvider()),
        RiskPolicy(),
        request_id="test-qty-sub",
    )
    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    session._broker = _StubBroker()  # pyright: ignore[reportAttributeAccessIssue]
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")

    intent = _make_intent(quantity=0.0)
    plan_zero = OrderPlan(
        intent_id=intent.intent_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=0.0,
    )
    blocked = session._pre_order_checks(intent, plan_zero)
    assert blocked is not None
    assert blocked[0] == "ORDER_BLOCKED"
    assert "quantity < 1 unit" in blocked[1]


def test_pre_order_checks_rejects_fractional_units() -> None:
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, journal_path=None, memory_path=None),
        cast(Any, _MockProvider()),
        RiskPolicy(),
        request_id="test-qty-frac",
    )
    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    session._broker = _StubBroker()  # pyright: ignore[reportAttributeAccessIssue]
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")

    intent = _make_intent(quantity=1.5)
    plan_frac = OrderPlan(
        intent_id=intent.intent_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=1.5,
    )
    blocked = session._pre_order_checks(intent, plan_frac)
    assert blocked is not None
    assert blocked[0] == "ORDER_BLOCKED"
    assert "must be whole integer units" in blocked[1]


# ── TEST SUITE 3: End-to-End Floor Division & Sizing Forwarding ───────────────


class _CaptureVenue:
    name = "capture"

    def __init__(self, funds: float = 100.92) -> None:
        self._funds = funds
        self.placed: list[OrderPlan] = []
        self._seq = 0

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def order_stream_health(self) -> tuple[bool, str]:
        return True, "ok"

    def account(self) -> dict[str, str]:
        return {"account_id": "ACC1", "mode": "LIVE"}

    def funds(self) -> dict[str, float]:
        return {"available": self._funds, "used": 0.0, "total": self._funds}

    def positions(self) -> list[dict]:
        return []

    def open_orders(self) -> list[dict]:
        return []

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:  # noqa: ARG002
        self.placed.append(plan)
        self._seq += 1
        return f"V-{self._seq}"

    def stream_events(self) -> tuple:
        return ()

    def reference_spread(self, _symbol: str) -> float | None:
        return None

    def on_market_price(self, _symbol: str, _price: float, _timestamp: str) -> None:
        pass


class _MockProvider:
    capabilities = ("candles",)

    def __init__(self) -> None:
        self.queue: list[CandleEvent] = []

    def open(self, symbols: tuple, timeframe: str) -> None: ...

    def poll(self) -> tuple:
        res = tuple(self.queue)
        self.queue.clear()
        return res


class _SingleSignalLogic:
    def __init__(self, entry: float, stop: float) -> None:
        self.entry = entry
        self.stop = stop
        self.fired = False

    def warmup(self) -> int:
        return 0

    def on_bar(self, view) -> Signal | None:  # noqa: ANN001, ANN204
        if self.fired:
            return None
        self.fired = True
        bar = view.bar
        return Signal(
            index=0,
            timestamp=bar.timestamp,
            kind=SignalKind.BUY,
            price=self.entry,
            stop_loss=self.stop,
        )


def _setup_session(
    venue: _CaptureVenue, provider: _MockProvider, logic: _SingleSignalLogic
) -> LiveSession:
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, journal_path=None, memory_path=None),
        cast(Any, provider),
        RiskPolicy(),
        request_id="test-integer-contract",
    )
    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    session._broker = venue  # pyright: ignore[reportAttributeAccessIssue]
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")
    context = StrategyContext(
        strategy_id="OBR C1C4",
        strategy_version="1",
        logic=logic,
        params=StrategyParameters({}),
        contract=StrategyRuntimeContract(strategy_id="OBR C1C4", strategy_version="1"),
    )
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        context.lifecycle.transition(target, reason="test")
    session._contexts["OBR C1C4:1"] = context
    return session


def test_live_session_forwards_exact_integer_quantities_to_venue() -> None:
    """Verifies:

    Budget ₹0.61 / ₹0.32 risk per share -> exactly 1 share to venue.
    Budget ₹0.61 / ₹0.20 risk per share -> exactly 3 shares to venue.
    Budget ₹0.61 / ₹1.08 risk per share -> 0 shares -> RISK_DENIED, 0 to venue.
    """
    venue = _CaptureVenue(funds=100.92)  # 100.92 * 4 * 0.0015 = ~0.60552 max risk
    provider = _MockProvider()
    symbol = "NSE:TEST-EQ"

    # Scenario 1: ₹0.32 risk per share -> floor(0.60552 / 0.32) = 1 share
    logic1 = _SingleSignalLogic(entry=100.00, stop=99.68)  # risk = 0.32
    session1 = _setup_session(venue, provider, logic1)

    candle = CandleEvent(
        symbol=symbol,
        timeframe="30m",
        timestamp="2026-11-06T11:15:00+05:30",
        seq=1,
        source="test",
        open=100.0,
        high=100.5,
        low=99.5,
        close=100.0,
        volume=1000,
    )
    provider.queue.append(candle)
    session1.step(time.time())

    assert len(venue.placed) == 1, "Expected order to be placed"
    assert venue.placed[0].quantity == 1.0, f"Expected 1.0, got {venue.placed[0].quantity}"
    assert float(venue.placed[0].quantity) == int(venue.placed[0].quantity)


def test_live_session_multiple_shares_forwarded_to_venue() -> None:
    # Scenario 2: ₹0.20 risk per share -> floor(0.60552 / 0.20) = 3 shares
    venue = _CaptureVenue(funds=100.92)
    provider = _MockProvider()
    symbol = "NSE:TEST-EQ"

    logic2 = _SingleSignalLogic(entry=100.00, stop=99.80)  # risk = 0.20
    session2 = _setup_session(venue, provider, logic2)

    candle = CandleEvent(
        symbol=symbol,
        timeframe="30m",
        timestamp="2026-11-06T11:15:00+05:30",
        seq=1,
        source="test",
        open=100.0,
        high=100.5,
        low=99.5,
        close=100.0,
        volume=1000,
    )
    provider.queue.append(candle)
    session2.step(time.time())

    assert len(venue.placed) == 1, "Expected order to be placed"
    assert venue.placed[0].quantity == 3.0, f"Expected 3.0, got {venue.placed[0].quantity}"


def test_live_session_sub_unit_risk_denied_never_dispatched() -> None:
    # Scenario 3: ₹1.08 risk per share -> floor(0.60552 / 1.08) = 0 shares -> blocked
    venue = _CaptureVenue(funds=100.92)
    provider = _MockProvider()
    symbol = "NSE:TEST-EQ"

    logic3 = _SingleSignalLogic(entry=100.00, stop=98.92)  # risk = 1.08
    session3 = _setup_session(venue, provider, logic3)

    candle = CandleEvent(
        symbol=symbol,
        timeframe="30m",
        timestamp="2026-11-06T11:15:00+05:30",
        seq=1,
        source="test",
        open=100.0,
        high=100.5,
        low=99.5,
        close=100.0,
        volume=1000,
    )
    provider.queue.append(candle)
    session3.step(time.time())

    assert len(venue.placed) == 0, "No orders should be placed when risk budget is too small"
    kinds = [e.kind for e in session3.journal.entries]
    assert "RISK_DENIED" in kinds
