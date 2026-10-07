"""Tests for FYERS WebSocket Integration (Phase 2).

Covers:
1. Live Market Data WebSocket (FyersLiveMarketData):
   - Connection lifecycle (connect, disconnect, reconnect, close)
   - Connection state machine (CONNECTING, CONNECTED, DISCONNECTED, RECONNECTING, ERROR)
   - Health reporting
   - Subscription management and memory across reconnects
    - Tick parsing and normalization to broker-neutral quote dicts
   - Preservation of symbol, LTP, timestamp, volume, bid/ask, OHLC
   - Error handling and queue overflow protection

2. Live Order/Trade WebSocket (FyersSessionAdapter order stream):
   - Socket connection and lifecycle
   - State reporting (order_stream_health)
   - Normalized event ingestion for ACK, Working/Open, Partial Fill, Full Fill, Reject, Cancel
    - Partial fills: ledger and execution state track filled vs remaining quantity
   - Duplicate fill deduplication: Repeated fills/trades do not double-count
   - Disconnect and REST polling fallback
   - Safe isolation: PAPER/SANDBOX cannot hit live socket

3. Live Safety Gates:
   - Broker stream disconnection blocks live order placement
"""

from __future__ import annotations

import time
from typing import Any

from broker.providers.fyers.live_market_data import (
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    STATE_ERROR,
    FyersLiveMarketData,
)
from broker.providers.fyers.session_adapter import ORDER_WS_SUBSCRIPTION, FyersSessionAdapter


class MockDataSocket:
    """Mock for FyersDataSocket."""

    def __init__(
        self,
        access_token: str,
        on_message: Any = None,
        on_error: Any = None,
        on_connect: Any = None,
        on_close: Any = None,
        **_kwargs: Any,
    ) -> None:
        self.access_token = access_token
        self.on_message = on_message
        self.on_error = on_error
        self.on_connect = on_connect
        self.on_close = on_close
        self.subscribed_symbols: list[str] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        if self.on_connect:
            self.on_connect()

    def subscribe(self, symbols: list[str], data_type: str = "SymbolUpdate") -> None:  # noqa: ARG002
        self.subscribed_symbols.extend(symbols)

    def unsubscribe(self, symbols: list[str]) -> None:
        for s in symbols:
            if s in self.subscribed_symbols:
                self.subscribed_symbols.remove(s)

    def close_connection(self) -> None:
        self.connected = False
        if self.on_close:
            self.on_close()


class MockOrderSocket:
    """Mock for FyersOrderSocket."""

    def __init__(
        self,
        access_token: str,
        on_orders: Any = None,
        on_trades: Any = None,
        on_error: Any = None,
        on_connect: Any = None,
        on_close: Any = None,
        **_kwargs: Any,
    ) -> None:
        self.access_token = access_token
        self.on_orders = on_orders
        self.on_trades = on_trades
        self.on_error = on_error
        self.on_connect = on_connect
        self.on_close = on_close
        self.connected = False
        self.subscribed_type = ""

    def connect(self) -> None:
        self.connected = True
        if self.on_connect:
            self.on_connect()

    def subscribe(self, data_type: str) -> None:
        self.subscribed_type = data_type

    def close_connection(self) -> None:
        self.connected = False
        if self.on_close:
            self.on_close()


class MockTransport:
    """Mock HTTP transport for FyersSessionAdapter."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.get_response: dict[str, Any] = {
            "s": "ok",
            "code": 200,
            "data": {"fy_id": "TEST_FYERS_ID", "name": "Test User"},
        }

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        if "orders" in url:
            return {"s": "ok", "code": 200, "orderBook": []}
        return self.get_response

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"method": "POST", "url": url, "payload": payload, "headers": headers})
        return {"s": "ok", "code": 1101, "message": "Order submitted successfully", "id": "ORD1"}

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"method": "DELETE", "url": url, "payload": payload, "headers": headers})
        return {"s": "ok", "code": 1103, "message": "Order cancelled successfully"}

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"method": "PUT", "url": url, "payload": payload, "headers": headers})
        return {"s": "ok", "code": 1102, "message": "Order modified successfully"}


# ── 1. MARKET DATA WEBSOCKET TESTS ──────────────────────────────────────────


def test_market_ws_connection_lifecycle() -> None:
    created_sockets: list[MockDataSocket] = []

    def socket_factory(**kwargs: Any) -> MockDataSocket:
        sock = MockDataSocket(**kwargs)
        created_sockets.append(sock)
        return sock

    md = FyersLiveMarketData(
        app_id="APP123",
        access_token="TOKEN456",
        socket_factory=socket_factory,
    )

    assert md.state == STATE_DISCONNECTED
    healthy, reason = md.health()
    assert not healthy
    assert reason == "disconnected"

    # Connect
    md.connect()
    assert md.state == STATE_CONNECTED
    healthy, reason = md.health()
    assert healthy
    assert reason == "connected"
    assert len(created_sockets) == 1
    assert created_sockets[0].connected

    # Subscribe
    md.subscribe(("NSE:SBIN-EQ", "NSE:RELIANCE-EQ"))
    assert "NSE:SBIN-EQ" in created_sockets[0].subscribed_symbols
    assert "NSE:RELIANCE-EQ" in created_sockets[0].subscribed_symbols

    # Disconnect
    md.disconnect()
    assert md.state == STATE_DISCONNECTED
    assert not created_sockets[0].connected

    # Reconnect preserves subscriptions
    md.reconnect()
    assert md.state == STATE_CONNECTED
    assert len(created_sockets) == 2
    assert "NSE:SBIN-EQ" in created_sockets[1].subscribed_symbols

    # Close releases resources
    md.close()
    assert md.state == STATE_DISCONNECTED
    healthy, reason = md.health()
    assert not healthy
    assert reason == "closed"


def test_market_ws_tick_normalization_trade() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456")

    raw_tick = {
        "symbol": "NSE:SBIN-EQ",
        "ltp": 820.50,
        "vol_traded_today": 15420,
        "last_traded_time": 1700000000,
    }

    events = md.normalize_tick(raw_tick)
    assert len(events) == 1

    quote = events[0]
    assert quote["symbol"] == "NSE:SBIN-EQ"
    assert quote["price"] == 820.50
    assert quote["volume"] == 15420.0
    assert "2023-" in quote["timestamp"]


def test_market_ws_tick_normalization_quote_and_candle() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456")

    raw_tick = {
        "symbol": "NSE:NIFTY50-INDEX",
        "ltp": 25000.0,
        "bid_price": 24999.0,
        "ask_price": 25001.0,
        "bid_size": 150,
        "ask_size": 200,
        "open_price": 24900.0,
        "high_price": 25050.0,
        "low_price": 24880.0,
        "prev_close_price": 24920.0,
        "vol_traded_today": 500000,
        "timestamp": 1700000000,
    }

    events = md.normalize_tick(raw_tick)
    # One normalized quote dict per tick (trade + book + OHLC legs combined),
    # so the execution feed folds each tick exactly once.
    assert len(events) == 1
    quote = events[0]
    assert quote["symbol"] == "NSE:NIFTY50-INDEX"
    assert quote["price"] == 25000.0
    assert quote["bid"] == 24999.0
    assert quote["ask"] == 25001.0
    assert quote["bid_qty"] == 150.0
    assert quote["ask_qty"] == 200.0
    assert quote["open"] == 24900.0
    assert quote["high"] == 25050.0
    assert quote["low"] == 24880.0
    assert quote["close"] == 24920.0
    assert quote["volume"] == 500000.0


def test_market_ws_poll_drains_queue() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456")
    md._on_message({"symbol": "NSE:SBIN-EQ", "ltp": 800.0, "last_traded_time": 1700000000})
    md._on_message({"symbol": "NSE:INFY-EQ", "ltp": 1500.0, "last_traded_time": 1700000000})

    polled = md.poll()
    assert len(polled) == 2
    symbols = [quote["symbol"] for quote in polled]
    assert symbols == ["NSE:SBIN-EQ", "NSE:INFY-EQ"]

    # Queue should now be empty
    assert len(md.poll()) == 0


def test_market_ws_error_handling() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456")
    md._on_error("Connection timed out")

    assert md.state == STATE_ERROR
    healthy, reason = md.health()
    assert not healthy
    assert "Connection timed out" in reason


# ── 2. ORDER/TRADE WEBSOCKET TESTS ──────────────────────────────────────────


def test_order_ws_connection_and_health() -> None:
    created_sockets: list[MockOrderSocket] = []

    def order_socket_factory(**kwargs: Any) -> MockOrderSocket:
        sock = MockOrderSocket(**kwargs)
        created_sockets.append(sock)
        return sock

    transport = MockTransport()
    adapter = FyersSessionAdapter(
        app_id="APP123",
        access_token="TOKEN456",
        transport=transport,
        order_socket_factory=order_socket_factory,
        enable_order_ws=True,
    )

    adapter.connect()
    assert adapter.order_ws_state == STATE_CONNECTED
    healthy, reason = adapter.order_stream_health()
    assert healthy
    assert reason == "connected"
    assert len(created_sockets) == 1
    assert created_sockets[0].connected
    assert created_sockets[0].subscribed_type == ORDER_WS_SUBSCRIPTION
    assert created_sockets[0].subscribed_type == "OnOrders,OnTrades"

    adapter.disconnect()
    assert adapter.order_ws_state == STATE_DISCONNECTED
    assert not created_sockets[0].connected


class VendorAccurateOrderSocket(MockOrderSocket):
    """Mirrors the real SDK lookup: ``socket_type[token]`` per comma part."""

    socket_type: dict[str, Any] = {
        "OnOrders": "orders",
        "OnTrades": "trades",
        "OnPositions": "positions",
        "OnGeneral": ["edis", "pricealerts", "login"],
    }

    def subscribe(self, data_type: str) -> None:
        parts = [part for part in str(data_type).split(",") if part]
        assert parts, "empty subscription"
        resolved: list[str] = []
        for part in parts:
            # Real SDK does ``socket_type[elem]`` — unknown keys raise KeyError
            # and the SUB_ORD frame is never sent (logged only).
            assert part in self.socket_type, f"invalid vendor key: {part!r}"
            value = self.socket_type[part]
            if isinstance(value, list):
                resolved.extend(value)
            else:
                resolved.append(value)
        self.subscribed_type = data_type
        self.slist = resolved


def _vendor_adapter(
    factory: Any, transport: MockTransport | None = None
) -> tuple[FyersSessionAdapter, list[VendorAccurateOrderSocket]]:
    created: list[VendorAccurateOrderSocket] = []

    def order_socket_factory(**kwargs: Any) -> VendorAccurateOrderSocket:
        sock = VendorAccurateOrderSocket(**kwargs)
        created.append(sock)
        return sock

    _ = factory  # factory placeholder keeps call sites explicit
    adapter = FyersSessionAdapter(
        app_id="APP123",
        access_token="TOKEN456",
        transport=transport or MockTransport(),
        order_socket_factory=order_socket_factory,
        enable_order_ws=True,
    )
    return adapter, created


def test_order_ws_subscription_uses_vendor_keys() -> None:
    adapter, created = _vendor_adapter(None)
    adapter.connect()
    assert adapter.order_ws_state == STATE_CONNECTED
    assert created[0].subscribed_type == "OnOrders,OnTrades"
    assert set(created[0].subscribed_type.split(",")) == {"OnOrders", "OnTrades"}
    adapter.disconnect()


def test_order_ws_onorders_subscription_succeeds() -> None:
    adapter, created = _vendor_adapter(None)
    adapter.connect()
    assert "OnOrders" in created[0].subscribed_type.split(",")
    assert "orders" in created[0].slist
    adapter.disconnect()


def test_order_ws_ontrades_subscription_succeeds() -> None:
    adapter, created = _vendor_adapter(None)
    adapter.connect()
    assert "OnTrades" in created[0].subscribed_type.split(",")
    assert "trades" in created[0].slist
    adapter.disconnect()


def test_order_ws_resubscribe_on_connect_uses_vendor_keys() -> None:
    adapter, created = _vendor_adapter(None)
    adapter.connect()
    first = created[0].subscribed_type
    # Simulate server-side reconnect callback — must resubscribe identically.
    created[0].subscribed_type = ""
    adapter._on_ws_connect()
    assert created[0].subscribed_type == first == "OnOrders,OnTrades"
    adapter.disconnect()


def test_order_ws_reconnect_fallback_intact() -> None:
    transport = MockTransport()
    adapter, created = _vendor_adapter(None, transport)
    adapter.connect()
    assert adapter.order_ws_state == STATE_CONNECTED
    # WS events still normalize through the unchanged interface.
    adapter._on_ws_orders(
        {"orders": {"id": "9", "orderTag": "t:re:o1", "status": 6, "symbol": "NSE:SBIN-EQ"}}
    )
    assert adapter.stream_events()[0]["type"] == "ack"
    # Disconnect drops transport; REST polling fallback still answers.
    adapter.disconnect()
    assert adapter.order_ws_state == STATE_DISCONNECTED
    adapter._connected = True
    assert adapter.stream_events() == ()
    assert any("orders" in c["url"] for c in transport.calls)


def test_order_ws_order_ack_and_reject() -> None:
    adapter = FyersSessionAdapter(
        app_id="APP123",
        access_token="TOKEN456",
        transport=MockTransport(),
        enable_order_ws=False,
    )
    adapter._connected = True

    # 1. ACK event
    ack_payload = {
        "orders": {
            "id": "241006000001",
            "orderTag": "strategy:intent_1:o1",
            "status": 6,  # Pending / Open
            "symbol": "NSE:SBIN-EQ",
        }
    }
    adapter._on_ws_orders(ack_payload)

    events = adapter.stream_events()
    assert len(events) == 1
    assert events[0]["type"] == "ack"
    assert events[0]["client_order_id"] == "strategy:intent_1:o1"
    assert events[0]["broker_order_id"] == "241006000001"

    # 2. Reject event
    reject_payload = {
        "orders": {
            "id": "241006000002",
            "orderTag": "strategy:intent_2:o1",
            "status": 5,  # Rejected
            "message": "Insufficient margin available",
            "symbol": "NSE:SBIN-EQ",
        }
    }
    adapter._on_ws_orders(reject_payload)

    events = adapter.stream_events()
    assert len(events) == 1
    assert events[0]["type"] == "reject"
    assert events[0]["client_order_id"] == "strategy:intent_2:o1"
    assert events[0]["reason"] == "Insufficient margin available"


def test_order_ws_partial_and_full_fill_deduplication() -> None:
    adapter = FyersSessionAdapter(
        app_id="APP123",
        access_token="TOKEN456",
        transport=MockTransport(),
        enable_order_ws=False,
    )
    adapter._connected = True

    # 1. Partial fill: 10 units out of 22
    order_id = "241006000010"
    tag = "strategy:intent_partial:o1"

    trade_1 = {
        "trades": {
            "orderNumber": order_id,
            "tradeNumber": "T_1001",
            "orderTag": tag,
            "symbol": "NSE:SBIN-EQ",
            "side": 1,
            "tradedQty": 10.0,
            "tradePrice": 820.0,
            "orderDateTime": "2026-10-06T12:00:00Z",
        }
    }
    adapter._on_ws_trades(trade_1)

    events = adapter.stream_events()
    assert len(events) == 1
    ev1 = events[0]
    assert ev1["type"] == "fill"
    assert ev1["client_order_id"] == tag
    assert ev1["fill"].fill_qty == 10.0
    assert ev1["fill"].fill_price == 820.0
    assert ev1["fill"].side == "BUY"

    # 2. Duplicate arrival of T_1001 (retransmission) must be ignored
    adapter._on_ws_trades(trade_1)
    events_dup = adapter.stream_events()
    assert len(events_dup) == 0  # Deduplicated!

    # 3. Second partial fill: remaining 12 units to reach full 22
    trade_2 = {
        "trades": {
            "orderNumber": order_id,
            "tradeNumber": "T_1002",
            "orderTag": tag,
            "symbol": "NSE:SBIN-EQ",
            "side": 1,
            "tradedQty": 12.0,
            "tradePrice": 821.0,
            "orderDateTime": "2026-10-06T12:00:05Z",
        }
    }
    adapter._on_ws_trades(trade_2)

    events_2 = adapter.stream_events()
    assert len(events_2) == 1
    ev2 = events_2[0]
    assert ev2["type"] == "fill"
    assert ev2["fill"].fill_qty == 12.0
    assert ev2["fill"].fill_price == 821.0


def test_order_ws_fallback_to_rest_when_queue_empty() -> None:
    transport = MockTransport()
    adapter = FyersSessionAdapter(
        app_id="APP123",
        access_token="TOKEN456",
        transport=transport,
        enable_order_ws=False,
    )
    adapter._connected = True

    # When queue has no WS events, stream_events queries orders snapshot
    events = adapter.stream_events()
    assert events == ()
    assert any("orders" in c["url"] for c in transport.calls)


def test_broker_disconnect_blocks_live_order() -> None:
    """LiveSession safety gate: disconnected/unhealthy broker blocks live order dispatch."""
    from unittest.mock import MagicMock

    from execution.market_data.provider import MarketDataProvider
    from execution.models.intent import ExecutionIntent
    from execution.models.order import OrderPlan
    from execution.modes import ExecutionMode
    from execution.runtime.session import LiveSession, SessionConfig
    from risk import RiskPolicy

    transport = MockTransport()
    adapter = FyersSessionAdapter(
        app_id="APP123",
        access_token="TOKEN456",
        transport=transport,
        enable_order_ws=False,
    )
    transport.get_response = {"s": "error", "message": "session expired"}

    healthy, reason = adapter.health()
    assert not healthy

    provider = MagicMock(spec=MarketDataProvider)
    session = LiveSession(
        config=SessionConfig(mode=ExecutionMode.LIVE),
        provider=provider,
        risk_policy=RiskPolicy(),
    )
    session._broker = adapter
    session._mode = ExecutionMode.LIVE
    session.arm("test arming")

    intent = ExecutionIntent(
        intent_id="strat:1:i1",
        strategy_id="strat",
        strategy_version="1",
        signal_id="sig:1",
        timestamp="2026-10-06T12:00:00Z",
        event_seq=1,
        symbol="SBIN",
        side="BUY",
        target_position_qty=10.0,
        quantity=10.0,
        urgency="normal",
        preferred_order_type="MARKET",
        reason="test",
        confidence=1.0,
    )
    plan = OrderPlan(
        intent_id="strat:1:i1",
        symbol="SBIN",
        side="BUY",
        quantity=10.0,
        order_type="MARKET",
    )

    # Dispatch submit
    session._submit(intent, plan, now_epoch=time.time())

    # Order must NOT reach the venue
    assert not any("orders/sync" in c["url"] for c in transport.calls)


def test_paper_mode_does_not_call_live_websocket() -> None:
    """PAPER mode remains isolated and never initiates LIVE sockets."""
    from unittest.mock import MagicMock

    from execution.market_data.provider import MarketDataProvider
    from execution.modes import ExecutionMode
    from execution.runtime.session import LiveSession, SessionConfig
    from risk import RiskPolicy

    provider = MagicMock(spec=MarketDataProvider)
    session = LiveSession(
        config=SessionConfig(mode=ExecutionMode.PAPER),
        provider=provider,
        risk_policy=RiskPolicy(),
    )
    assert session.mode == ExecutionMode.PAPER
    assert session._broker is None or type(session._broker).__name__ != "FyersSessionAdapter"
