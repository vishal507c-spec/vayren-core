"""Zerodha live adapter proofs — real contract, scripted venue, zero network.

A ``FakeKite`` stands in for the installed ``kiteconnect`` SDK (same method
names, verified against kiteconnect 5.0.1). Nothing here touches the
network; production wiring differs only in credentials + activation.

Covers the required safety surface: auth failure, submit, tag-dedup,
rejection mapping, partial/full fills, cancel, reconnect resubscribe,
timeout-tag-resolve, positions/funds mapping, gated registration,
PAPER-never-real.
"""

from __future__ import annotations

import pytest
from data.provider.zerodha import live_activation as venue
from data.provider.zerodha.live_activation import LIVE_VENUE_ID
from data.provider.zerodha.live_market_data import ZerodhaMarketDataFace
from data.provider.zerodha.live_trading import BrokerError, ZerodhaTradingAdapter
from execution import BrokerAdapter

from broker.registry import default_registry
from broker.vocab import Domain


class FakeKite:
    """Scripted KiteConnect double (method names mirror the real SDK)."""

    VARIETY_REGULAR = "regular"
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT = "LIMIT"
    VALIDITY_DAY = "DAY"

    def __init__(self) -> None:
        self.token: str | None = None
        self.profile_data = {"user_id": "AB1234", "user_name": "Test", "email": "t@x.in"}
        self.profile_error: Exception | None = None
        self.orders_data: list[dict] = []
        self.place_calls = 0
        self.place_error: Exception | None = None
        self.fail_place_once: Exception | None = None
        self.margins_data = {
            "equity": {"available": {"live_balance": 50000.0}, "utilised": {"total": 5000.0}}
        }
        self.positions_data = {"net": [{"tradingsymbol": "RELIANCE", "quantity": 10}]}
        self.quotes_data: dict = {}

    def set_access_token(self, token: str) -> None:
        self.token = token

    def profile(self) -> dict:
        if self.profile_error is not None:
            raise self.profile_error
        return dict(self.profile_data)

    def margins(self) -> dict:
        return dict(self.margins_data)

    def positions(self) -> dict:
        return dict(self.positions_data)

    def orders(self) -> list[dict]:
        return [dict(o) for o in self.orders_data]

    def place_order(self, **kwargs) -> str:
        self.place_calls += 1
        if self.fail_place_once is not None:
            exc, self.fail_place_once = self.fail_place_once, None
            raise exc
        if self.place_error is not None:
            raise self.place_error
        order_id = f"25010100{self.place_calls:05d}"
        self.orders_data.append(
            {
                "order_id": order_id,
                "tag": kwargs.get("tag", ""),
                "status": "OPEN",
                "tradingsymbol": kwargs.get("tradingsymbol", ""),
                "transaction_type": kwargs.get("transaction_type", ""),
                "quantity": kwargs.get("quantity", 0),
                "filled_quantity": 0,
                "pending_quantity": kwargs.get("quantity", 0),
                "average_price": 0.0,
                "order_timestamp": "2026-01-05 09:30:00",
                "exchange_timestamp": "2026-01-05 09:30:00",
            }
        )
        return order_id

    def cancel_order(self, variety, order_id, **kwargs) -> None:  # noqa: ARG002
        for order in self.orders_data:
            if order["order_id"] == order_id:
                order["status"] = "CANCELLED"
                return
        raise FakeOrderError("invalid order")

    def quote(self, instruments: list[str]) -> dict:
        return {key: dict(self.quotes_data.get(key, {})) for key in instruments}


class FakeOrderError(Exception):
    pass


class FakeTokenError(Exception):
    pass


class FakeNetworkError(Exception):
    pass


def _rename(exc: Exception, name: str) -> Exception:
    exc.__class__ = type(name, (Exception,), {})
    return exc


def _adapter(kite: FakeKite | None = None, **kwargs) -> ZerodhaTradingAdapter:
    kite = kite if kite is not None else FakeKite()
    return ZerodhaTradingAdapter(api_key="key123", access_token="tok123", kite=kite, **kwargs)


def _plan(symbol: str = "RELIANCE", side: str = "BUY", quantity: float = 1.0):
    from execution.models.order import OrderPlan

    return OrderPlan(intent_id="i1", symbol=symbol, side=side, quantity=quantity)


def test_missing_credentials_fail_closed() -> None:
    adapter = ZerodhaTradingAdapter(api_key="", access_token="", kite=FakeKite())
    with pytest.raises(BrokerError) as exc_info:
        adapter.connect()
    assert exc_info.value.code == "CREDENTIALS"


def test_auth_failure_is_exact() -> None:
    kite = FakeKite()
    kite.profile_error = _rename(FakeTokenError("bad token"), "TokenException")
    adapter = _adapter(kite)
    with pytest.raises(BrokerError) as exc_info:
        adapter.connect()
    assert exc_info.value.code == "AUTH"
    assert "tok123" not in str(exc_info.value)


def test_connect_and_account() -> None:
    adapter = _adapter()
    adapter.connect()
    assert adapter.health() == (True, "zerodha live ready")
    account = adapter.account()
    assert account["account_id"] == "AB1234"
    assert account["environment"] == "live"
    assert isinstance(adapter, BrokerAdapter)


def test_submit_returns_broker_id() -> None:
    adapter = _adapter()
    adapter.connect()
    broker_id = adapter.place_order(_plan(), "live-test-1")
    assert broker_id.startswith("25010100")
    assert kite_open(adapter) == ["live-test-1"]


def kite_open(adapter: ZerodhaTradingAdapter) -> list[str]:
    return [o["client_order_id"] for o in adapter.open_orders()]


def test_duplicate_tag_adopts_instead_of_placing() -> None:
    kite = FakeKite()
    adapter = _adapter(kite)
    adapter.connect()
    first = adapter.place_order(_plan(), "dup-tag-9")
    second = adapter.place_order(_plan(), "dup-tag-9")
    assert first == second
    assert kite.place_calls == 1


def test_venue_rejection_maps_with_reason() -> None:
    kite = FakeKite()
    kite.place_error = _rename(FakeOrderError("insufficient margin"), "OrderException")
    adapter = _adapter(kite)
    adapter.connect()
    with pytest.raises(BrokerError) as exc_info:
        adapter.place_order(_plan(), "rej-1")
    assert exc_info.value.code == "REJECTED"
    assert "insufficient margin" in str(exc_info.value)


def test_timeout_resolves_by_tag_before_raising() -> None:
    kite = FakeKite()
    adapter = _adapter(kite)
    adapter.connect()
    real_place = kite.place_order

    def accept_then_timeout(**kwargs) -> str:
        real_place(**kwargs)  # venue accepted…
        raise _rename(FakeNetworkError("read timeout"), "NetworkException")

    kite.place_order = accept_then_timeout  # type: ignore[method-assign]
    assert adapter.place_order(_plan(), "timeout-tag-1") == "2501010000001"
    assert kite.place_calls == 1  # accepted once, never retried blindly


def test_partial_then_full_fill_stream() -> None:
    kite = FakeKite()
    adapter = _adapter(kite, poll_interval_s=0.0)
    adapter.connect()
    broker_id = adapter.place_order(_plan(quantity=10.0), "pf-1")
    assert adapter.stream_events() == ()  # first sighting establishes baseline
    row = kite.orders_data[0]
    row["filled_quantity"] = 4
    row["pending_quantity"] = 6
    row["average_price"] = 2500.5
    (event,) = adapter.stream_events()
    assert event["type"] == "fill"
    assert event["fill"].fill_qty == 4
    assert event["fill"].partial is True
    assert event["fill"].fill_price == 2500.5
    row["filled_quantity"] = 10
    row["pending_quantity"] = 0
    row["status"] = "COMPLETE"
    (event2,) = adapter.stream_events()
    assert event2["fill"].fill_qty == 6
    assert event2["fill"].partial is False
    assert adapter.stream_events() == ()
    assert broker_id.startswith("25010100")


def test_cancel_paths() -> None:
    kite = FakeKite()
    adapter = _adapter(kite)
    adapter.connect()
    broker_id = adapter.place_order(_plan(), "cx-1")
    assert adapter.cancel_order(broker_id) is True
    assert adapter.cancel_order("no-such-id") is False


def test_positions_and_funds_mapping() -> None:
    adapter = _adapter()
    adapter.connect()
    assert adapter.positions() == [{"symbol": "RELIANCE", "quantity": 10.0}]
    funds = adapter.funds()
    assert funds == {"available": 50000.0, "used": 5000.0, "equity": 55000.0}


def test_reconnect_keeps_subscriptions() -> None:
    face = ZerodhaMarketDataFace(api_key="key123", access_token="tok123", kite=FakeKite())
    face.open(("RELIANCE",), "15m")
    face.disconnect()
    assert face.health()[0] is False
    face.reconnect()
    assert face.health()[0] is True
    assert face._subscribed == ("RELIANCE",)


def test_market_data_poll_normalizes() -> None:
    kite = FakeKite()
    kite.quotes_data = {
        "NSE:RELIANCE": {
            "last_price": 2501.25,
            "volume_traded": 123456,
            "timestamp": "2026-01-05 09:30:00",
        }
    }
    face = ZerodhaMarketDataFace(api_key="key123", access_token="tok123", kite=kite)
    face.open(("RELIANCE",), "15m")
    (quote,) = face.poll()
    assert quote == {
        "symbol": "RELIANCE",
        "price": 2501.25,
        "timestamp": "2026-01-05 09:30:00",
        "volume": 123456,
    }


def test_registration_gated_without_activation(monkeypatch) -> None:
    monkeypatch.delenv("VAYREN_ENABLE_LIVE_VENUE", raising=False)
    monkeypatch.delenv("VAYREN_ZERODHA_API_KEY", raising=False)
    monkeypatch.delenv("VAYREN_ZERODHA_ACCESS_TOKEN", raising=False)
    ok, reason = venue.register_zerodha_live()
    assert ok is False
    assert "VAYREN_ENABLE_LIVE_VENUE=true" in reason
    assert venue.live_requirements() != ()


def test_registration_registers_trading_face(monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_ENABLE_LIVE_VENUE", "true")
    monkeypatch.setenv("VAYREN_ZERODHA_API_KEY", "key123")
    monkeypatch.setenv("VAYREN_ZERODHA_ACCESS_TOKEN", "tok123")
    ok, reason = venue.register_zerodha_live()
    assert ok, reason
    try:
        record = default_registry().get(LIVE_VENUE_ID)
        face = record.plugin.face(Domain.TRADING)
        assert isinstance(face, BrokerAdapter)
        md = record.plugin.face(Domain.MARKET_DATA)
        assert hasattr(md, "poll") and hasattr(md, "subscribe")
    finally:
        registry = default_registry()
        if LIVE_VENUE_ID in registry:
            registry.unregister(LIVE_VENUE_ID)


def test_paper_ignores_live_registration(monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_ENABLE_LIVE_VENUE", "true")
    monkeypatch.setenv("VAYREN_ZERODHA_API_KEY", "key123")
    monkeypatch.setenv("VAYREN_ZERODHA_ACCESS_TOKEN", "tok123")
    venue.register_zerodha_live()
    try:
        from execution import ExecutionMode, resolve_broker
        from execution.modes import ModeGates

        broker, effective, _ = resolve_broker(ExecutionMode.PAPER, ModeGates())
        assert effective == ExecutionMode.PAPER
        assert broker.name == "paper"
    finally:
        registry = default_registry()
        if LIVE_VENUE_ID in registry:
            registry.unregister(LIVE_VENUE_ID)


def test_history_record_untouched_by_live_module() -> None:
    import broker.adapters.zerodha as pkg

    assert set(pkg.__all__) == {
        "BROKER_ID",
        "DISPLAY_NAME",
        "HISTORICAL_CAPABILITIES",
        "zerodha_plugin_record",
    }
    assert LIVE_VENUE_ID == "zerodha-live"
    assert LIVE_VENUE_ID != pkg.BROKER_ID
