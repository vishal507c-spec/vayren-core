"""Original v5.3.0 execution-layer parity: EOD wall-clock exits (incl. F&O 15:08).

No real money is involved — the venue is the simulated ``_Venue`` from
``test_live_pipeline.py``. Covers:
  1. ``eod_sweep`` cancels pending entries and force-exits open positions at
     the 15:15 cutoff (standard) or 15:08 (F&O-eligible, venue-proven).
  2. Idempotence: repeated sweeps after the exit is terminal do nothing new;
     day rollover resets.
  3. Tick-level pending-SELL cancellation on LTP >= ref_high.
  4. ``MarketDataFanout`` background pump keeps the venue stream drained.
"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution.broker.adapter import BrokerAdapter  # noqa: E402
from execution.events import CandleEvent, MarketEvent  # noqa: E402
from execution.market_data.provider import MarketDataProvider  # noqa: E402
from execution.models.contract import StrategyRuntimeContract  # noqa: E402
from execution.models.order import Fill, OrderPlan, OrderState  # noqa: E402
from execution.models.position import Position  # noqa: E402
from execution.modes import ExecutionMode, LiveArm  # noqa: E402
from execution.runtime.lifecycle import LifecycleState  # noqa: E402
from execution.runtime.session import LiveSession, SessionConfig  # noqa: E402
from execution.runtime.strategy_runtime import StrategyContext  # noqa: E402
from risk import RiskPolicy  # noqa: E402
from strategy import StrategyParameters  # noqa: E402
from strategy.models.signal import Signal  # noqa: E402
from strategy.obr_c1c4_engine import Strategy as ObrC1C4Strategy  # noqa: E402
from strategy.runtime import BarView, StrategyLogic  # noqa: E402

SYMBOL = "KAYNES"
ENTRY = 1218.50
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
TS_EOD = datetime.datetime(2026, 11, 23, 15, 30, tzinfo=IST)
TS_BEFORE = datetime.datetime(2026, 11, 23, 15, 7, tzinfo=IST)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class _Venue:
    """Minimal fake venue (mirrors test_live_pipeline._Venue, plus ltp marks)."""

    name = "fake-live"

    def __init__(self) -> None:
        self._seq = 0
        self.placed: list[OrderPlan] = []
        self.cancelled: list[str] = []
        self._events: list[dict] = []
        self._open: list[dict] = []

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("orders.market", "account.funds", "stream.events")

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def order_stream_health(self) -> tuple[bool, str]:
        return True, "ok"

    def account(self) -> dict:
        return {"account_id": "ACC1", "mode": "LIVE"}

    def funds(self) -> dict:
        return {"available": 100000.0, "used": 0.0, "total": 100000.0}

    def positions(self) -> list[dict]:
        return [dict(row) for row in self._open]

    def open_orders(self) -> list[dict]:
        return [dict(row) for row in self._open]

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:
        self._seq += 1
        broker_id = f"VENUE-{self._seq:04d}"
        self.placed.append(plan)
        self._events.append(
            {"type": "ack", "client_order_id": client_order_id, "broker_order_id": broker_id}
        )
        return broker_id

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancelled.append(broker_order_id)
        return True

    def modify_order(
        self,
        broker_order_id: str,  # noqa: ARG002
        quantity: float | None,  # noqa: ARG002
        price: float | None,  # noqa: ARG002
    ) -> bool:
        return False

    def stream_events(self) -> tuple[dict, ...]:
        drained = tuple(self._events)
        self._events.clear()
        return drained

    # Protocol parameter names are part of the BrokerAdapter contract.
    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:  # noqa: ARG002
        return None

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        return None

    def emit_fill(
        self,
        client_order_id: str,
        qty: float,
        price: float,
        *,
        partial: bool,
        side: str = "SELL",
    ) -> None:
        self._events.append(
            {
                "type": "fill",
                "client_order_id": client_order_id,
                "fill": Fill(
                    client_order_id=client_order_id,
                    broker_order_id="VENUE-1",
                    symbol=SYMBOL,
                    side=side,
                    fill_qty=qty,
                    fill_price=price,
                    commission=0.0,
                    timestamp=_now_iso(),
                    partial=partial,
                ),
            }
        )


class _Provider:
    """MarketDataProvider-conforming stub (no transport, no real stream)."""

    name = "fake-md"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("tick", "candle-close")

    def __init__(self) -> None:
        self.last_close: dict[str, float] = {SYMBOL: 1000.0}

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        return None

    def poll(self) -> tuple[MarketEvent, ...]:
        return ()

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def close(self) -> None:
        return None


class _FakeEngine:
    """Minimal engine stub for tick-invalidation tests (ccSellSet/refHigh)."""

    def __init__(self) -> None:
        self.ccSellSet = False
        self.refHigh: float | None = None

    def isIdle(self) -> bool:  # noqa: N802 - engine API parity
        return True


class _Logic:
    def __init__(self) -> None:
        self.engine = _FakeEngine()

    def warmup(self) -> int:
        return 0

    def on_bar(self, view: BarView) -> Signal | None:  # noqa: ARG002
        return None


def _session(venue: _Venue, provider: _Provider) -> LiveSession:
    # Protocol assignments make the stubs prove their shape to the type
    # checker — no Any escape hatch and no suppression comment.
    broker: BrokerAdapter = venue
    market_data: MarketDataProvider = provider
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, journal_path=None, memory_path=None),
        market_data,
        RiskPolicy(),
        request_id="test-eod",
    )
    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    session._broker = broker
    session._subscribed_symbols = (SYMBOL,)
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")
    contract = StrategyRuntimeContract(strategy_id="OBR C1C4", strategy_version="1")
    logic: StrategyLogic = _Logic()
    context = StrategyContext(
        strategy_id="OBR C1C4",
        strategy_version="1",
        logic=logic,
        params=StrategyParameters({}),
        contract=contract,
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


def _candle(seq: int, close: float = ENTRY) -> CandleEvent:
    return CandleEvent(
        symbol=SYMBOL,
        timestamp=_now_iso(),
        seq=seq,
        source="test",
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=100,
        timeframe="30m",
        is_closed=True,
    )


def _entry_order(session: LiveSession, venue: _Venue) -> str:
    """Simulate a working SELL entry order with a broker id (for cancel tests)."""
    from execution.models.order import BrokerOrder

    client_id = "eodtest:entry:o1"
    order = BrokerOrder(
        client_order_id=client_id,
        intent_id="eodtest:entry",
        symbol=SYMBOL,
        side="SELL",
        quantity=10.0,
        session_date="2026-11-23",
        order_type="SLM",
    )
    session.engine.create(order)
    session.engine.transition(client_id, OrderState.VALIDATED, reason="t")
    broker_id = venue.place_order(
        OrderPlan(
            intent_id="eodtest:entry", symbol=SYMBOL, side="SELL", quantity=10.0, order_type="SLM"
        ),
        client_id,
    )
    session.engine.transition(
        client_id, OrderState.SUBMITTED, reason="t", broker_order_id=broker_id
    )
    return client_id


def test_eod_sweep_exits_open_position_and_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr("execution.runtime.session.ist_session_date", lambda: "2026-11-23")
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)

    # Before 15:15: nothing happens.
    session.eod_sweep(TS_BEFORE)
    assert venue.placed == []
    assert venue.cancelled == []

    # Open a LONG position (BUY fills -> +qty; the ledger convention).
    session.ledger.adopt_position(Position(symbol=SYMBOL, quantity=10.0))
    venue.placed.clear()

    # 15:15: the position is force-exited via a MARKET SELL.
    session.eod_sweep(TS_EOD)
    assert len(venue.placed) == 1
    exit_plan = venue.placed[0]
    assert exit_plan.side == "SELL"
    assert exit_plan.order_type == "MARKET"
    assert session._eod_exit_order[SYMBOL]

    # Second sweep: nothing new while the exit order is not terminal.
    session.eod_sweep(TS_EOD)
    assert len(venue.placed) == 1

    # Exit filled: flat. Next sweep marks done, places nothing new.
    exit_cid = session._eod_exit_order[SYMBOL]
    venue.emit_fill(exit_cid, 10.0, ENTRY, partial=False, side="SELL")
    session._process_broker_stream()
    assert session.ledger.position(SYMBOL).flat
    session.eod_sweep(TS_EOD)
    assert len(venue.placed) == 1
    assert SYMBOL in session._eod_done


def test_eod_fno_cutoff_lower_for_fno_symbols(monkeypatch) -> None:
    monkeypatch.setattr("execution.runtime.session.ist_session_date", lambda: "2026-11-23")
    # Non-F&O: nothing happens at 15:08 — the standard 15:15 exit applies.
    v1, p1 = _Venue(), _Provider()
    s1 = _session(v1, p1)
    s1.eod_sweep(datetime.datetime(2026, 11, 23, 15, 8, tzinfo=IST))
    assert v1.placed == []
    # 15:09 non-F&O still quiet.
    s1.eod_sweep(datetime.datetime(2026, 11, 23, 15, 9, tzinfo=IST))
    assert v1.placed == []
    # F&O-eligible symbol (venue-proven): forced exit AT 15:08.
    v2, p2 = _Venue(), _Provider()
    s2 = _session(v2, p2)
    s2.set_fno_classification({SYMBOL})
    s2.ledger.adopt_position(Position(symbol=SYMBOL, quantity=-10.0))
    s2.eod_sweep(datetime.datetime(2026, 11, 23, 15, 8, tzinfo=IST))
    assert any(p.side == "BUY" and p.order_type == "MARKET" for p in v2.placed)


def test_tick_invalidation_cancels_pending_entry_on_ref_high() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    context = session._contexts["OBR C1C4:1"]
    cast(ObrC1C4Strategy, context.logic).engine.ccSellSet = True
    cast(ObrC1C4Strategy, context.logic).engine.refHigh = 100.5
    cid = _entry_order(session, venue)
    provider.last_close[SYMBOL] = 100.5

    session.check_tick_invalidation()
    assert venue.cancelled == ["VENUE-0001"]
    tracked = session.engine.get(cid)
    assert tracked is not None and tracked.state == OrderState.CANCELLED

    # Second call: one-shot per symbol.
    provider.last_close[SYMBOL] = 101.0
    session.check_tick_invalidation()
    assert len(venue.cancelled) == 1


def test_tick_invalidation_below_ref_high_does_nothing() -> None:
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    context = session._contexts["OBR C1C4:1"]
    cast(ObrC1C4Strategy, context.logic).engine.ccSellSet = True
    cast(ObrC1C4Strategy, context.logic).engine.refHigh = 100.5
    _entry_order(session, venue)
    provider.last_close[SYMBOL] = 100.0

    session.check_tick_invalidation()
    assert venue.cancelled == []


def test_tick_invalidation_scans_every_subscribed_symbol() -> None:
    """The tick sweep must not stop at subscribed_symbols[0]."""
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    context = session._contexts["OBR C1C4:1"]
    cast(ObrC1C4Strategy, context.logic).engine.ccSellSet = True
    cast(ObrC1C4Strategy, context.logic).engine.refHigh = 100.5
    session._subscribed_symbols = ("AAA", SYMBOL)
    provider.last_close = {"AAA": 90.0, SYMBOL: 100.5}
    cid = _entry_order(session, venue)

    session.check_tick_invalidation()
    assert venue.cancelled == ["VENUE-0001"]
    tracked = session.engine.get(cid)
    assert tracked is not None and tracked.state == OrderState.CANCELLED


def test_tick_invalidation_ignores_unsubscribed_symbol() -> None:
    """A foreign symbol's tick can never cancel this session's order."""
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    context = session._contexts["OBR C1C4:1"]
    cast(ObrC1C4Strategy, context.logic).engine.ccSellSet = True
    cast(ObrC1C4Strategy, context.logic).engine.refHigh = 100.5
    _entry_order(session, venue)

    session._maybe_cancel_invalidated_entry("OTHER", 200.0)
    assert venue.cancelled == []


def test_eod_unlinked_working_order_blocks_only_its_symbol(monkeypatch) -> None:
    """One unlinked working order must not starve other symbols' EOD exits."""
    from execution.models.order import BrokerOrder

    monkeypatch.setattr("execution.runtime.session.ist_session_date", lambda: "2026-11-23")
    venue, provider = _Venue(), _Provider()
    session = _session(venue, provider)
    session._subscribed_symbols = ("AAA", SYMBOL)
    unlinked = BrokerOrder(
        client_order_id="eodtest:aaa:o1",
        intent_id="eodtest:aaa",
        symbol="AAA",
        side="SELL",
        quantity=10.0,
        session_date="2026-11-23",
        order_type="SLM",
    )
    session.engine.create(unlinked)
    session.engine.transition("eodtest:aaa:o1", OrderState.VALIDATED, reason="t")
    session.engine.transition("eodtest:aaa:o1", OrderState.SUBMITTED, reason="t")
    session.ledger.adopt_position(Position(symbol=SYMBOL, quantity=10.0))

    session.eod_sweep(TS_EOD)
    assert [p.symbol for p in venue.placed] == [SYMBOL]
    assert session.engine.get("eodtest:aaa:o1") is not None


def test_eod_unknown_exit_is_tracked_and_not_duplicated(monkeypatch) -> None:
    """A timeout means UNKNOWN, not "retry blindly" — never two exit orders."""
    monkeypatch.setattr("execution.runtime.session.ist_session_date", lambda: "2026-11-23")

    class _TimeoutVenue(_Venue):
        attempts = 0

        def place_order(self, plan: OrderPlan, client_order_id: str) -> str:  # noqa: ARG002
            self.attempts += 1
            raise TimeoutError("venue did not answer")

    venue = _TimeoutVenue()
    session = _session(venue, _Provider())
    session.ledger.adopt_position(Position(symbol=SYMBOL, quantity=10.0))

    session.eod_sweep(TS_EOD)
    assert session._eod_exit_order.get(SYMBOL)
    session.eod_sweep(TS_EOD)
    assert venue.attempts == 1  # UNKNOWN tracked; never submitted twice


def test_fanout_background_pump_no_loss() -> None:
    from broker.market_data_fanout import MarketDataFanout

    class _Upstream:
        def __init__(self) -> None:
            self._quotes: list[dict] = []

        def poll(self) -> tuple[dict, ...]:
            out = tuple(self._quotes)
            self._quotes.clear()
            return out

    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    fanout.subscriptions_batch([("c1", (SYMBOL,))])
    upstream._quotes.append({"symbol": SYMBOL, "price": 42.0})
    fanout.start_background_pump(interval_s=0.01)
    got: tuple[dict, ...] = ()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        got = fanout.poll("c1")
        if got:
            break
        time.sleep(0.01)
    fanout.close()
    assert [q["price"] for q in got] == [42.0]
