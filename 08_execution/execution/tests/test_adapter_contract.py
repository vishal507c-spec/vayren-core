"""Adapter contract conformance: paper AND sandbox satisfy the same surface.

Any future real adapter must pass this same suite (run it against the new
venue implementation). No venue-specific assertions below — only the
:class:`BrokerAdapter` contract.
"""

import pytest

from execution.broker.adapter import BrokerError, adapter_supports
from execution.broker.paper import PaperBroker
from execution.broker.readonly import ReadOnlyBroker
from execution.broker.sandbox import SandboxBroker
from execution.models.order import OrderPlan


def _plan(**overrides) -> OrderPlan:
    values = {
        "intent_id": "i1",
        "symbol": "SYM",
        "side": "BUY",
        "quantity": 10.0,
        "order_type": "MARKET",
    }
    values.update(overrides)
    return OrderPlan(**values)  # type: ignore[arg-type]


def _venues():
    paper = PaperBroker(capital=1_000_000.0)
    sandbox = SandboxBroker(account_id="contract-acct")
    return [paper, sandbox]


def test_structural_protocol_conformance() -> None:
    methods = (
        "connect",
        "disconnect",
        "health",
        "account",
        "positions",
        "open_orders",
        "place_order",
        "cancel_order",
        "modify_order",
        "stream_events",
        "on_market_price",
        "reference_spread",
    )
    for venue in _venues():
        for attr in methods:
            assert callable(getattr(venue, attr, None)), f"{venue.name} missing {attr}"
        assert isinstance(venue.name, str) and venue.name
        assert isinstance(venue.capabilities, tuple) and venue.capabilities
        assert adapter_supports(venue, "orders.market")


def test_connect_health_disconnect_cycle() -> None:
    for venue in _venues():
        assert venue.health()[0] is False
        venue.connect()
        assert venue.health()[0] is True
        venue.disconnect()
        assert venue.health()[0] is False
        with pytest.raises(BrokerError):
            venue.place_order(_plan(), "c1")


def test_place_ack_fill_stream_roundtrip() -> None:
    for venue in _venues():
        venue.connect()
        broker_id = venue.place_order(_plan(quantity=5.0), "c-round")
        assert broker_id
        acked = venue.stream_events()
        assert acked and acked[0]["type"] == "ack"
        assert acked[0]["client_order_id"] == "c-round"
        venue.on_market_price("SYM", 100.0, "t")
        fills = [e for e in venue.stream_events() if e["type"] == "fill"]
        assert len(fills) == 1
        assert fills[0]["fill"].fill_qty == 5.0
        assert fills[0]["fill"].fill_price > 0
        assert venue.stream_events() == ()


def test_positions_and_open_orders_agree() -> None:
    for venue in _venues():
        venue.connect()
        venue.place_order(_plan(quantity=7.0), "c-pos")
        venue.stream_events()
        venue.on_market_price("SYM", 50.0, "t")
        venue.stream_events()
        positions = venue.positions()
        assert positions == [{"symbol": "SYM", "quantity": 7.0}]
        assert venue.open_orders() == []
        account = venue.account()
        assert isinstance(account, dict) and account


def test_duplicate_client_order_rejected() -> None:
    for venue in _venues():
        venue.connect()
        venue.place_order(_plan(), "c-dup")
        with pytest.raises(BrokerError):
            venue.place_order(_plan(), "c-dup")


def test_cancel_before_and_after_fill() -> None:
    for venue in _venues():
        venue.connect()
        broker_id = venue.place_order(_plan(quantity=3.0), "c-can")
        assert venue.cancel_order(broker_id) is True
        assert venue.open_orders() == []
        venue.place_order(_plan(quantity=3.0), "c-fill")
        venue.stream_events()
        venue.on_market_price("SYM", 10.0, "t")
        venue.stream_events()
        assert venue.cancel_order("VENUE-unknown") is False


def test_readonly_blocks_every_mutation() -> None:
    for venue in _venues():
        venue.connect()
        ro = ReadOnlyBroker(venue)
        assert ro.health()[0] is True
        assert ro.account() == venue.account()
        assert ro.positions() == venue.positions()
        assert ro.open_orders() == venue.open_orders()
        assert ro.name.endswith("+readonly")
        with pytest.raises(BrokerError) as exc_info:
            ro.place_order(_plan(), "c-ro")
        assert exc_info.value.code == "READ_ONLY"
        with pytest.raises(BrokerError):
            ro.cancel_order("whatever")
        with pytest.raises(BrokerError):
            ro.modify_order("whatever", 1.0, None)
        # read-only never advances fills, even when asked for a price
        ro.on_market_price("SYM", 100.0, "t")
        assert venue.open_orders() == []
