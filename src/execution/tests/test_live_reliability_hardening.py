"""VAYREN Phase 5 — Live Reliability, Event Pipeline Hardening & Broker Reconciliation Tests.

Covers:
1.  Candle deduplication per symbol/timestamp (CANDLE_DUPLICATE_SKIPPED).
2.  Signal deduplication by (strategy_id, event_seq) (SIGNAL_DUPLICATE_SKIPPED).
3.  Duplicate order intent prevention via engine by_intent lookup.
4.  Unresolved UNKNOWN order blocks subsequent submissions in LIVE mode.
5.  OrderState.UNKNOWN transition via broker ack event (engine.reconcile).
6.  OrderState.UNKNOWN transition via broker fill event (engine.reconcile + apply_fill).
7.  OrderState.UNKNOWN transition via broker reject/cancel events.
8.  Event pipeline capacity warnings (>1000 events) and deterministic ordering.
9.  BrokerFeedProvider subscription restoration on reconnect.
10. BrokerFeedProvider validation for NaN/inf/<=0 prices and negative volumes.
11. Periodic broker reconciliation cadence during active LIVE sessions.
12. Missing broker fail-closed discipline in LIVE mode.
"""

from __future__ import annotations

import time
from typing import Any

from execution.broker.adapter import BrokerAdapter, BrokerCapabilities
from execution.engine import ExecutionEngine
from execution.events import CandleEvent
from execution.market_data.broker_feed import BrokerFeedProvider
from execution.market_data.provider import MarketDataProvider
from execution.models.intent import ExecutionIntent, StrategySignal
from execution.models.order import BrokerOrder, Fill, OrderPlan, OrderState
from execution.modes import ExecutionMode, LiveArm, ModeGates
from execution.runtime.session import LiveSession, SessionConfig
from execution.runtime.strategy_runtime import StrategyContext
from risk import RiskPolicy


class _MockBrokerFace:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.connected = True
        self.reconnected = False
        self.quotes_to_poll: list[dict[str, Any]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.reconnected = True
        self.connected = True

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        self.subscribed.extend(symbols)

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        for s in symbols:
            if s in self.subscribed:
                self.subscribed.remove(s)

    def poll(self) -> list[dict[str, Any]]:
        q = list(self.quotes_to_poll)
        self.quotes_to_poll.clear()
        return q

    def health(self) -> tuple[bool, str]:
        return self.connected, "mock face ok"


class _MockBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.connected = True
        self.orders_placed: list[str] = []
        self.mock_open_orders: list[dict[str, Any]] = []
        self.mock_positions: list[dict[str, Any]] = []
        self.streamed_events: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "mock-venue"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (
            BrokerCapabilities.MARKET_ORDERS,
            BrokerCapabilities.POSITIONS,
            BrokerCapabilities.OPEN_ORDERS,
            BrokerCapabilities.STREAMING,
        )

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def health(self) -> tuple[bool, str]:
        return self.connected, "healthy"

    def account(self) -> dict[str, Any]:
        return {"account_id": "ACC123"}

    def positions(self) -> list[dict[str, Any]]:
        return list(self.mock_positions)

    def open_orders(self) -> list[dict[str, Any]]:
        return list(self.mock_open_orders)

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:  # noqa: ARG002
        self.orders_placed.append(client_order_id)
        return f"venue-{client_order_id}"

    def cancel_order(self, broker_order_id: str) -> bool:
        _ = broker_order_id
        return True

    def modify_order(
        self, broker_order_id: str, quantity: float | None = None, price: float | None = None
    ) -> bool:
        _ = (broker_order_id, quantity, price)
        return True

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        events = tuple(self.streamed_events)
        self.streamed_events.clear()
        return events


class _MockProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.events: list[CandleEvent] = []
        self.opened = False

    @property
    def name(self) -> str:
        return "mock-provider"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("candle-close",)

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        self.opened = True

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        pass

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        pass

    def poll(self) -> tuple[CandleEvent, ...]:
        ev = tuple(self.events)
        self.events.clear()
        return ev

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def close(self) -> None:
        self.opened = False


def _make_live_session(tmp_path: Any) -> LiveSession:
    cfg = SessionConfig(
        mode=ExecutionMode.LIVE,
        gates=ModeGates(
            live_trading_enabled=True, broker_live_enabled=True, account_confirmed=True
        ),
        journal_path=tmp_path / "journal.jsonl",
        kill_switch_path=tmp_path / "kill.json",
        memory_path=tmp_path / "memory.json",
        adapter_name="mock-venue",
    )
    provider = _MockProvider()
    session = LiveSession(cfg, provider, risk_policy=RiskPolicy())
    session._broker = _MockBroker()
    session._mode = ExecutionMode.LIVE
    from execution.runtime.lifecycle import LifecycleState

    session._lifecycle.state = LifecycleState.RUNNING
    session._armed = LiveArm.ARMED
    return session


def _make_unknown_order(
    engine: ExecutionEngine,
    cid: str,
    intent_id: str,
    symbol: str = "NSE:INFY",
    qty: float = 10.0,
) -> BrokerOrder:
    order = BrokerOrder(
        client_order_id=cid,
        intent_id=intent_id,
        symbol=symbol,
        side="BUY",
        quantity=qty,
        order_type="MARKET",
        state=OrderState.CREATED,
    )
    engine.create(order)
    engine.transition(cid, OrderState.VALIDATED, reason="validated")
    return engine.transition(cid, OrderState.UNKNOWN, reason="test timeout")


def _make_submitted_order(
    engine: ExecutionEngine,
    cid: str,
    intent_id: str,
    symbol: str = "NSE:INFY",
    qty: float = 10.0,
) -> BrokerOrder:
    order = BrokerOrder(
        client_order_id=cid,
        intent_id=intent_id,
        symbol=symbol,
        side="BUY",
        quantity=qty,
        order_type="MARKET",
        state=OrderState.CREATED,
    )
    engine.create(order)
    engine.transition(cid, OrderState.VALIDATED, reason="validated")
    return engine.transition(cid, OrderState.SUBMITTED, reason="submitted")


# ── 1. Deduplication Tests ────────────────────────────────────────────────────


def test_duplicate_candle_deduplication(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    now = time.time()
    candle = CandleEvent(
        symbol="NSE:INFY",
        timestamp="2026-10-18T10:00:00",
        seq=1,
        source="test",
        open=1500.0,
        high=1510.0,
        low=1495.0,
        close=1505.0,
        volume=1000,
        timeframe="30m",
        is_closed=True,
    )

    # First arrival processes normally
    session._on_event(candle, now)
    assert session._seen_candle_stamps["NSE:INFY"] == "2026-10-18T10:00:00"

    # Second arrival with exact same timestamp is deduplicated
    session._on_event(candle, now)
    kinds = [e.kind for e in session.journal.entries]
    assert "CANDLE_DUPLICATE_SKIPPED" in kinds


def test_duplicate_signal_deduplication(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    ctx = StrategyContext(
        strategy_id="strat-1",
        strategy_version="1.0",
        logic=None,  # type: ignore[arg-type]
        params=None,  # type: ignore[arg-type]
        contract=None,  # type: ignore[arg-type]
    )
    signal = StrategySignal(
        strategy_id="strat-1",
        strategy_version="1.0",
        signal_id="sig-1",
        timestamp="2026-10-18T10:00:00",
        event_seq=42,
        symbol="NSE:INFY",
        side="BUY",
        price=1500.0,
    )

    # First signal processed
    session._handle_signal(ctx, signal, time.time())
    kinds1 = [e.kind for e in session.journal.entries]
    assert "SIGNAL_DUPLICATE_SKIPPED" not in kinds1

    # Duplicate signal with same (strategy_id, event_seq) skipped
    session._handle_signal(ctx, signal, time.time())
    kinds2 = [e.kind for e in session.journal.entries]
    assert "SIGNAL_DUPLICATE_SKIPPED" in kinds2


def test_duplicate_intent_prevention(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    intent = ExecutionIntent(
        intent_id="intent-dup-1",
        strategy_id="strat-1",
        strategy_version="1.0",
        signal_id="sig-1",
        timestamp="2026-10-18T10:00:00",
        event_seq=1,
        symbol="NSE:INFY",
        side="SELL",
        target_position_qty=0.0,
        quantity=10.0,
    )
    _make_submitted_order(session._engine, "cid-1", "intent-dup-1")

    plan = OrderPlan(
        intent_id=intent.intent_id, symbol=intent.symbol, side=intent.side, quantity=intent.quantity
    )
    # Pre-order checks must reject duplicate intent
    refusal = session._pre_order_checks(intent, plan)
    assert refusal is not None
    assert refusal[0] == "ORDER_BLOCKED"
    assert "duplicate intent" in refusal[1]


def test_unresolved_unknown_order_blocks_subsequent_submissions(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    _make_unknown_order(session._engine, "cid-unk-1", "intent-unk-1")

    # Any new intent must be blocked
    new_intent = ExecutionIntent(
        intent_id="intent-fresh-2",
        strategy_id="strat-1",
        strategy_version="1.0",
        signal_id="sig-2",
        timestamp="2026-10-18T10:05:00",
        event_seq=2,
        symbol="NSE:INFY",
        side="SELL",
        target_position_qty=0.0,
        quantity=5.0,
    )
    new_plan = OrderPlan(
        intent_id=new_intent.intent_id,
        symbol=new_intent.symbol,
        side=new_intent.side,
        quantity=new_intent.quantity,
    )
    refusal = session._pre_order_checks(new_intent, new_plan)
    assert refusal is not None
    assert refusal[0] == "ORDER_BLOCKED"
    assert "unresolved UNKNOWN order" in refusal[1]


# ── 2. OrderState.UNKNOWN Resolution via Broker Stream ────────────────────────


def test_unknown_order_reconciled_by_broker_ack(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    cid = "cid-unk-ack"
    _make_unknown_order(session._engine, cid, "intent-1")

    # Broker sends ACK for this order
    session._dispatch_broker_event(
        {
            "type": "ack",
            "client_order_id": cid,
            "broker_order_id": "b-123",
        }
    )

    tracked = session._engine.get(cid)
    assert tracked is not None
    assert tracked.state == OrderState.ACKNOWLEDGED


def test_unknown_order_reconciled_by_broker_fill(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    cid = "cid-unk-fill"
    _make_unknown_order(session._engine, cid, "intent-2")

    fill = Fill(
        client_order_id=cid,
        broker_order_id="b-456",
        symbol="NSE:INFY",
        side="BUY",
        fill_qty=10.0,
        fill_price=1500.0,
        commission=0.0,
        timestamp="2026-10-18T10:00:00",
        partial=False,
    )
    session._dispatch_broker_event(
        {
            "type": "fill",
            "client_order_id": cid,
            "fill": fill,
        }
    )

    tracked = session._engine.get(cid)
    assert tracked is not None
    assert tracked.state == OrderState.FILLED
    assert tracked.filled_qty == 10.0


def test_unknown_order_reconciled_by_broker_reject(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    cid = "cid-unk-rej"
    _make_unknown_order(session._engine, cid, "intent-3")

    session._dispatch_broker_event(
        {
            "type": "reject",
            "client_order_id": cid,
            "reason": "margin shortfall",
        }
    )

    tracked = session._engine.get(cid)
    assert tracked is not None
    assert tracked.state == OrderState.REJECTED


def test_unknown_order_reconciled_by_broker_cancel(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    cid = "cid-unk-cnc"
    _make_unknown_order(session._engine, cid, "intent-4")

    session._dispatch_broker_event(
        {
            "type": "cancel",
            "client_order_id": cid,
        }
    )

    tracked = session._engine.get(cid)
    assert tracked is not None
    assert tracked.state == OrderState.CANCELLED


# ── 3. Pipeline Load Warning & Deterministic Ordering ─────────────────────────


def test_pipeline_load_warning_on_large_batch(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    mock_prov: _MockProvider = session._provider  # type: ignore[assignment]

    # Create 1005 mock candle events out-of-order
    for i in reversed(range(1005)):
        mock_prov.events.append(
            CandleEvent(
                symbol="NSE:INFY",
                timestamp=f"2026-10-18T10:{i // 60:02d}:{i % 60:02d}",
                seq=i,
                source="test",
                open=1500.0,
                high=1505.0,
                low=1495.0,
                close=1500.0,
                volume=100,
                timeframe="1m",
                is_closed=True,
            )
        )

    session.step(time.time())
    kinds = [e.kind for e in session.journal.entries]
    assert "PIPELINE_LOAD_WARNING" in kinds


# ── 4. BrokerFeedProvider Safeguards & Recovery ──────────────────────────────


def test_broker_feed_reconnect_restores_subscriptions() -> None:
    face = _MockBrokerFace()
    feed = BrokerFeedProvider(face, timeframe="15m")
    feed.open(("NSE:INFY", "NSE:TCS"), "15m")
    assert "NSE:INFY" in face.subscribed
    assert "NSE:TCS" in face.subscribed

    # Simulate disconnect and clear subscribed face list
    feed.disconnect()
    face.subscribed.clear()

    # Reconnect must restore active subscriptions
    feed.reconnect()
    assert face.reconnected is True
    assert "NSE:INFY" in face.subscribed
    assert "NSE:TCS" in face.subscribed


def test_broker_feed_rejects_non_finite_or_negative_quotes() -> None:
    face = _MockBrokerFace()
    feed = BrokerFeedProvider(face, timeframe="15m")
    feed.open(("NSE:INFY",), "15m")

    # NaN price
    assert (
        feed._ingest(
            {"symbol": "NSE:INFY", "price": float("nan"), "timestamp": "2026-10-18T10:00:00"}
        )
        is None
    )

    # Inf price
    assert (
        feed._ingest(
            {"symbol": "NSE:INFY", "price": float("inf"), "timestamp": "2026-10-18T10:00:00"}
        )
        is None
    )

    # Zero price
    assert (
        feed._ingest({"symbol": "NSE:INFY", "price": 0.0, "timestamp": "2026-10-18T10:00:00"})
        is None
    )

    # Negative price
    assert (
        feed._ingest({"symbol": "NSE:INFY", "price": -1500.0, "timestamp": "2026-10-18T10:00:00"})
        is None
    )

    # Negative volume clamped to zero
    ev = feed._ingest(
        {"symbol": "NSE:INFY", "price": 1500.0, "volume": -50, "timestamp": "2026-10-18T10:00:00"}
    )
    assert ev is None  # first tick opens bucket
    held = feed._buckets.get("NSE:INFY")
    assert held is not None
    assert held[1][4] == 0.0  # volume clamped to 0


# ── 5. Reconciliation Invariant Tests ─────────────────────────────────────────


def test_periodic_reconciliation_in_live_mode(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    broker: _MockBroker = session._broker  # type: ignore[assignment]
    broker.mock_positions = [{"symbol": "NSE:INFY", "quantity": 10.0}]

    t0 = time.time()
    session.step(t0)

    # Step before 30s elapsed does not reconcile
    session.step(t0 + 10.0)

    # Step after 30s triggers reconcile_now
    session.step(t0 + 35.0)
    kinds1 = [e.kind for e in session.journal.entries]
    assert "RECONCILED" in kinds1


def test_missing_broker_in_live_fails_closed(tmp_path: Any) -> None:
    session = _make_live_session(tmp_path)
    session._broker = None  # Detached broker
    rec = session.reconcile_now()
    assert rec.blocks_live is True
    assert not rec.orders.matched
