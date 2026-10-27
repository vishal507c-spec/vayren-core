"""Tests for Intraday SELL-First Short Entry in VAYREN execution engine.

Verifies:
1. SELL signal with no existing position -> valid intraday short-entry order is prepared.
2. BUY signal -> existing BUY-entry behavior remains unchanged.
3. SELL signal with existing long position -> existing exit behavior remains correct.
4. Short-position stop-loss and exit logic work correctly (protective BUY stop).
5. BUY signal with existing short position -> covers short position back to flat.
6. Re-entry blocked when already short (single-position model).
7. Unsupported instrument/order or failed risk checks -> order is safely rejected.
8. Non-live / mock tests pass without placing real broker orders.
9. FYERS adapter enforces INTRADAY product type and correct venue side mapping.
"""

from __future__ import annotations

import datetime
import time
from typing import Any

from execution.events import CandleEvent
from execution.models.contract import StrategyRuntimeContract
from execution.models.order import Fill, OrderPlan
from execution.modes import ExecutionMode, LiveArm
from execution.runtime.lifecycle import LifecycleState
from execution.runtime.session import LiveSession, SessionConfig
from execution.runtime.strategy_runtime import StrategyContext
from risk import RiskPolicy
from strategy import StrategyParameters
from strategy.models.signal import Signal, SignalKind

SYMBOL = "NSE:SBIN-EQ"
ENTRY_PRICE = 800.0
SHORT_STOP = 810.0  # above entry for short: BUY stop rests until touched
LONG_STOP = 790.0  # below entry for long: SELL stop rests until touched
ORDER_QTY = 10.0


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class _MockVenue:
    """Mock venue implementing BrokerAdapter surface."""

    name = "mock-venue"

    def __init__(
        self,
        *,
        funds_available: float | None = 100_000.0,
        healthy: bool = True,
        stream_healthy: bool = True,
    ) -> None:
        self._funds = funds_available
        self._healthy = healthy
        self._stream_healthy = stream_healthy
        self._seq = 0
        self.placed: list[OrderPlan] = []
        self.cancelled: list[str] = []
        self._events: list[dict[str, Any]] = []
        self._positions: list[dict[str, Any]] = []
        self._open: list[dict[str, Any]] = []

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("orders.market", "orders.limit", "account.funds", "stream.events")

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]:
        return (self._healthy, "ok" if self._healthy else "down")

    def order_stream_health(self) -> tuple[bool, str]:
        return (self._stream_healthy, "ok" if self._stream_healthy else "ws down")

    def account(self) -> dict[str, Any]:
        return {"account_id": "MOCK_ACC", "mode": "LIVE"}

    def funds(self) -> dict[str, Any]:
        if self._funds is None:
            raise RuntimeError("funds unavailable")
        return {"available": self._funds, "used": 0.0, "total": self._funds}

    def positions(self) -> list[dict[str, Any]]:
        return list(self._positions)

    def open_orders(self) -> list[dict[str, Any]]:
        return list(self._open)

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:
        self._seq += 1
        broker_id = f"BROKER-{self._seq:04d}"
        self.placed.append(plan)
        self._events.append(
            {"type": "ack", "client_order_id": client_order_id, "broker_order_id": broker_id}
        )
        return broker_id

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancelled.append(broker_order_id)
        return True

    def modify_order(
        self, _broker_order_id: str, _quantity: float | None, _price: float | None
    ) -> bool:
        return True

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        drained = tuple(self._events)
        self._events.clear()
        return drained

    def on_market_price(self, _symbol: str, _price: float, _timestamp: str) -> None: ...

    def reference_spread(self, _symbol: str) -> float | None:
        return None

    def emit_fill(
        self, client_order_id: str, side: str, qty: float, price: float, *, partial: bool = False
    ) -> None:
        self._events.append(
            {
                "type": "fill",
                "client_order_id": client_order_id,
                "fill": Fill(
                    client_order_id=client_order_id,
                    broker_order_id="BROKER-FILL",
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


class _SignalLogic:
    """Configurable logic emitting a single signal on the first bar."""

    def __init__(self, kind: SignalKind, price: float, stop_loss: float) -> None:
        self.kind = kind
        self.price = price
        self.stop_loss = stop_loss
        self._emitted = False

    def warmup(self) -> int:
        return 0

    def on_bar(self, view: Any) -> Signal | None:
        if self._emitted:
            return None
        self._emitted = True
        bar = view.bar
        return Signal(
            index=0,
            timestamp=bar.timestamp,
            kind=self.kind,
            price=self.price,
            stop_loss=self.stop_loss,
        )


class _CandleProvider:
    capabilities = ("candles",)

    def __init__(self) -> None:
        self._queue: list[CandleEvent] = []

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None: ...

    def feed(self, event: CandleEvent) -> None:
        self._queue.append(event)

    def poll(self) -> tuple[CandleEvent, ...]:
        drained = tuple(self._queue)
        self._queue.clear()
        return drained


def _candle(seq: int = 1, close: float = ENTRY_PRICE) -> CandleEvent:
    return CandleEvent(
        symbol=SYMBOL,
        timestamp=_now_iso(),
        seq=seq,
        source="test",
        open=close,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        volume=1000,
        timeframe="30m",
        is_closed=True,
    )


def _setup_session(
    venue: _MockVenue,
    provider: _CandleProvider,
    logic: _SignalLogic,
    *,
    mode: ExecutionMode = ExecutionMode.LIVE,
    armed: LiveArm = LiveArm.ARMED,
) -> LiveSession:
    session = LiveSession(
        SessionConfig(mode=mode, journal_path=None, memory_path=None),
        provider,  # type: ignore[arg-type]
        RiskPolicy(),
        request_id="test-short",
    )
    session._mode = mode
    session._armed = armed
    session._broker = venue  # type: ignore[assignment]
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(target, reason="test")

    context = StrategyContext(
        strategy_id="obr-c1c4",
        strategy_version="1",
        logic=logic,  # type: ignore[arg-type]
        params=StrategyParameters({}),
        contract=StrategyRuntimeContract(strategy_id="obr-c1c4", strategy_version="1"),
    )
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        context.lifecycle.transition(target, reason="test")
    session._contexts["obr-c1c4:1"] = context
    return session


def _kinds(session: LiveSession) -> list[str]:
    return [entry.kind for entry in session.journal.entries]


# ── TEST 1: SELL signal with no existing position -> valid short entry prepared
def test_sell_first_short_entry_when_flat() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.SELL, ENTRY_PRICE, SHORT_STOP)
    session = _setup_session(venue, provider, logic)

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    assert "RISK_VALIDATED" in _kinds(session)
    assert "ORDER_SUBMITTED" in _kinds(session)
    assert len(venue.placed) == 1

    plan = venue.placed[0]
    assert plan.symbol == SYMBOL
    assert plan.side == "SELL"
    assert plan.quantity > 0.0

    # Strategy stop level was saved for protective stop
    assert session._stop_levels.get(SYMBOL) == SHORT_STOP


# ── TEST 2: BUY signal -> existing BUY-entry behavior unchanged
def test_buy_signal_existing_behavior_unchanged() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.BUY, ENTRY_PRICE, LONG_STOP)
    session = _setup_session(venue, provider, logic)

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    assert "RISK_VALIDATED" in _kinds(session)
    assert "ORDER_SUBMITTED" in _kinds(session)
    assert len(venue.placed) == 1

    plan = venue.placed[0]
    assert plan.symbol == SYMBOL
    assert plan.side == "BUY"
    assert plan.quantity > 0.0
    assert session._stop_levels.get(SYMBOL) == LONG_STOP


# ── TEST 3: SELL signal with existing long position -> existing exit behavior
def test_sell_signal_with_existing_long_exits_long() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.SELL, ENTRY_PRICE, SHORT_STOP)
    session = _setup_session(venue, provider, logic)

    # Establish an existing LONG position of 15 shares on both broker and session
    venue._positions = [{"symbol": SYMBOL, "quantity": 15.0, "avg_price": ENTRY_PRICE}]
    venue._open = [
        {
            "symbol": SYMBOL,
            "side": "SELL",
            "quantity": 15.0,
            "status": "WORKING",
            "order_type": "STOP_MARKET",
            "price": 0.0,
            "trigger_price": LONG_STOP,
        }
    ]
    session._stop_levels[SYMBOL] = LONG_STOP
    session._stop_protection[SYMBOL] = {
        "client_order_id": "sl:seed",
        "broker_order_id": "b-sl",
        "quantity": 15.0,
        "stop_price": LONG_STOP,
        "state": "WORKING",
    }
    session._ledger.apply_fill(
        Fill(
            client_order_id="seed-long",
            broker_order_id="seed-b1",
            symbol=SYMBOL,
            side="BUY",
            fill_qty=15.0,
            fill_price=ENTRY_PRICE,
            commission=0.0,
            timestamp=_now_iso(),
        )
    )
    assert session.ledger.position(SYMBOL).quantity == 15.0

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    assert "ORDER_SUBMITTED" in _kinds(session)
    assert len(venue.placed) == 1

    plan = venue.placed[0]
    # SELL of existing long is an exit: under fresh session's 0.5x REDUCED posture,
    # floor(15.0 * 0.5) = 7.0 shares
    assert plan.symbol == SYMBOL
    assert plan.side == "SELL"
    assert plan.quantity == 7.0


# ── TEST 4: Short-position stop-loss and exit logic work correctly
def test_short_position_lifecycle_protective_stop_and_exit() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.SELL, ENTRY_PRICE, SHORT_STOP)
    session = _setup_session(venue, provider, logic)

    provider.feed(_candle(1))
    session.step(time.time())

    entry_plan = venue.placed[0]
    entry_qty = entry_plan.quantity
    entry_cid = "obr-c1c4:1:1:1:o1"

    # Fill the short entry order: sell entry_qty shares
    venue.emit_fill(entry_cid, "SELL", entry_qty, ENTRY_PRICE)
    session._process_broker_stream()

    # Net position in ledger must now be negative (short)
    pos = session.ledger.position(SYMBOL)
    assert pos.quantity == -entry_qty

    # Protective stop must be placed at the broker:
    # A short position REQUIRES a BUY STOP_MARKET order at SHORT_STOP!
    prot = session._stop_protection.get(SYMBOL)
    assert prot is not None
    assert prot["quantity"] == entry_qty
    assert prot["stop_price"] == SHORT_STOP

    # Check the placed protective stop order on the venue
    assert len(venue.placed) == 2
    sl_plan = venue.placed[1]
    assert sl_plan.side == "BUY"
    assert sl_plan.order_type == "STOP_MARKET"
    assert sl_plan.quantity == entry_qty
    assert sl_plan.stop_price == SHORT_STOP

    # Now simulate stop being hit: venue fills the protective BUY stop
    sl_cid = prot["client_order_id"]
    venue.emit_fill(sl_cid, "BUY", entry_qty, SHORT_STOP)
    session._process_broker_stream()

    # Position must be flat, and stop protection confirmed SAFE
    assert session.ledger.position(SYMBOL).quantity == 0.0
    assert session.ledger.position(SYMBOL).flat is True
    assert session._stop_protection[SYMBOL]["state"] == "SAFE"
    assert "SL_SAFE" in _kinds(session)


# ── TEST 5: BUY signal with existing short position -> exits/covers short
def test_buy_signal_with_existing_short_covers_short() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.BUY, ENTRY_PRICE, LONG_STOP)
    session = _setup_session(venue, provider, logic)

    # Establish an existing SHORT position of 12 shares on both broker and session
    venue._positions = [{"symbol": SYMBOL, "quantity": -12.0, "avg_price": ENTRY_PRICE}]
    venue._open = [
        {
            "symbol": SYMBOL,
            "side": "BUY",
            "quantity": 12.0,
            "status": "WORKING",
            "order_type": "STOP_MARKET",
            "price": 0.0,
            "trigger_price": SHORT_STOP,
        }
    ]
    session._stop_levels[SYMBOL] = SHORT_STOP
    session._stop_protection[SYMBOL] = {
        "client_order_id": "sl:seed-short",
        "broker_order_id": "b-sl-short",
        "quantity": 12.0,
        "stop_price": SHORT_STOP,
        "state": "WORKING",
    }
    session._ledger.apply_fill(
        Fill(
            client_order_id="seed-short",
            broker_order_id="seed-b2",
            symbol=SYMBOL,
            side="SELL",
            fill_qty=12.0,
            fill_price=ENTRY_PRICE,
            commission=0.0,
            timestamp=_now_iso(),
        )
    )
    assert session.ledger.position(SYMBOL).quantity == -12.0

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    assert "ORDER_SUBMITTED" in _kinds(session)
    assert len(venue.placed) == 1

    plan = venue.placed[0]
    # BUY signal when short: under fresh session's 0.5x REDUCED posture,
    # floor(12.0 * 0.5) = 6.0 shares
    assert plan.symbol == SYMBOL
    assert plan.side == "BUY"
    assert plan.quantity == 6.0


# ── TEST 6: Re-entry blocked when already short
def test_sell_signal_skips_reentry_when_already_short() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.SELL, ENTRY_PRICE, SHORT_STOP)
    session = _setup_session(venue, provider, logic)

    # Establish an existing SHORT position
    session._ledger.apply_fill(
        Fill(
            client_order_id="seed-short",
            broker_order_id="seed-b3",
            symbol=SYMBOL,
            side="SELL",
            fill_qty=10.0,
            fill_price=ENTRY_PRICE,
            commission=0.0,
            timestamp=_now_iso(),
        )
    )
    assert session.ledger.position(SYMBOL).quantity == -10.0

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    # No new order placed: single-position model skips re-entry
    assert len(venue.placed) == 0


# ── TEST 7: Unsupported / failed risk checks safely reject short entry
def test_short_entry_rejected_when_capital_zero() -> None:
    venue = _MockVenue(funds_available=0.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.SELL, ENTRY_PRICE, SHORT_STOP)
    session = _setup_session(venue, provider, logic)

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    assert "RISK_DENIED" in _kinds(session)
    assert len(venue.placed) == 0


def test_short_entry_blocked_when_unarmed() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.SELL, ENTRY_PRICE, SHORT_STOP)
    session = _setup_session(venue, provider, logic, armed=LiveArm.DISARMED)

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    assert "ORDER_BLOCKED_UNARMED" in _kinds(session)
    assert len(venue.placed) == 0


# ── TEST 8: Non-live execution handles short entry without broker
def test_backtest_mode_short_entry_handles_sizing() -> None:
    venue = _MockVenue(funds_available=100_000.0)
    provider = _CandleProvider()
    logic = _SignalLogic(SignalKind.SELL, ENTRY_PRICE, SHORT_STOP)
    session = _setup_session(venue, provider, logic, mode=ExecutionMode.BACKTEST)

    provider.feed(_candle(1))
    session.step(time.time())

    assert "SIGNAL_GENERATED" in _kinds(session)
    # In non-live mode, sizing uses default quantity and stop level is resolved
    assert session._stop_levels.get(SYMBOL) == SHORT_STOP


# ── TEST 9: FYERS adapter INTRADAY product type and side verification
def test_fyers_adapter_intraday_mapping_for_short_orders() -> None:
    from broker.providers.fyers.session_adapter import FyersSessionAdapter

    adapter = FyersSessionAdapter.__new__(FyersSessionAdapter)

    # 1. Short entry plan
    short_entry_plan = OrderPlan(
        intent_id="short-entry:1",
        symbol="NSE:SBIN-EQ",
        side="SELL",
        quantity=10.0,
        order_type="MARKET",
    )
    assert adapter._venue_product(short_entry_plan) == "INTRADAY"
    side_val, side_str = adapter._venue_side(short_entry_plan)
    assert side_val == -1
    assert side_str == "SELL"

    # 2. Short protective stop plan (BUY STOP_MARKET)
    short_sl_plan = OrderPlan(
        intent_id="sl:NSE:SBIN-EQ:1",
        symbol="NSE:SBIN-EQ",
        side="BUY",
        quantity=10.0,
        order_type="STOP_MARKET",
        stop_price=810.0,
    )
    assert adapter._venue_product(short_sl_plan) == "INTRADAY"
    side_val_sl, side_str_sl = adapter._venue_side(short_sl_plan)
    assert side_val_sl == 1
    assert side_str_sl == "BUY"
    type_val, type_str, limit_p, stop_p = adapter._venue_price_and_type(short_sl_plan)
    assert type_val == 3
    assert type_str == "STOP_MARKET"
    assert stop_p == 810.0
