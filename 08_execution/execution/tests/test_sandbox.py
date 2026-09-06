"""SandboxBroker unit tests — contract surface, policies, failure hooks."""

import pytest

from execution.broker.adapter import BrokerError, adapter_supports
from execution.broker.credentials import BrokerCredentials
from execution.broker.sandbox import SandboxBroker
from execution.models.order import Fill, OrderPlan


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


def _broker(**overrides) -> SandboxBroker:
    broker = SandboxBroker(**overrides)
    broker.connect()
    return broker


def test_identity_capabilities_and_health() -> None:
    broker = SandboxBroker(account_id="sbx-7")
    assert broker.name == "sandbox"
    assert broker.health() == (False, "not connected")
    broker.connect()
    assert broker.health() == (True, "sandbox ready")
    account = broker.account()
    assert account["account_id"] == "sbx-7"
    assert account["environment"] == "sandbox"
    assert account["mode"] == "SANDBOX"
    assert adapter_supports(broker, "orders.market")
    assert adapter_supports(broker, "stream.events")
    assert not adapter_supports(broker, "nope.missing")


def test_place_ack_fill_stream_shapes() -> None:
    broker = _broker()
    broker_id = broker.place_order(_plan(), "c1")
    assert broker_id.startswith("SANDBOX-")
    acked = broker.stream_events()
    assert acked[0]["type"] == "ack"
    assert acked[0]["client_order_id"] == "c1"
    broker.on_market_price("RELIANCE", 100.0, "t")
    events = broker.stream_events()
    kinds = [e["type"] for e in events]
    assert kinds == ["fill"]
    assert events[0]["client_order_id"] == "c1"
    fill = events[0]["fill"]
    assert isinstance(fill, Fill)
    assert fill.fill_price == pytest.approx(100.0 * 1.0002)
    assert broker.stream_events() == ()


def test_credentials_required_and_never_leaked() -> None:
    broker = SandboxBroker(
        credentials=BrokerCredentials(account_id="", environment=""),
    )
    broker.connect()
    with pytest.raises(BrokerError) as exc_info:
        broker.place_order(_plan(), "c1")
    assert exc_info.value.code == "CREDENTIALS"
    text = repr(BrokerCredentials(account_id="a", environment="sandbox", key_refs=("K",)))
    assert "redacted" in text and "'a'" in text


def test_disconnect_fails_closed() -> None:
    broker = _broker()
    broker.place_order(_plan(), "c1")
    broker.simulate_disconnect()
    assert broker.health()[0] is False
    with pytest.raises(BrokerError):
        broker.place_order(_plan(), "c2")
    with pytest.raises(BrokerError):
        broker.stream_events()
    broker.connect()
    assert broker.health()[0] is True


def test_reject_partial_delay_unknown_policies() -> None:
    broker = _broker()
    broker.place_order(_plan(), "r1")
    broker.set_fill_policy("r1", "reject:venue halt")
    assert broker.settle("r1", 100.0, "t") is None
    rejects = [e for e in broker.stream_events() if e["type"] == "reject"]
    assert len(rejects) == 1 and "venue halt" in str(rejects[0]["reason"])

    broker.place_order(_plan(quantity=100.0), "p1")
    broker.set_fill_policy("p1", "partial:30")
    partial = broker.settle("p1", 100.0, "t")
    assert partial is not None and partial.partial and partial.fill_qty == 30.0

    broker.place_order(_plan(), "d1")
    broker.set_fill_policy("d1", "delay:2")
    assert broker.settle("d1", 100.0, "t") is None
    assert broker.settle("d1", 100.0, "t") is None
    assert broker.settle("d1", 100.0, "t") is not None

    broker.place_order(_plan(), "u1")
    broker.set_fill_policy("u1", "nonsense")
    assert broker.settle("u1", 100.0, "t") is None
    rejects = [e for e in broker.stream_events() if e.get("client_order_id") == "u1"]
    assert [e["type"] for e in rejects if e["type"] == "reject"] == ["reject"]


def test_cancel_modify_and_positions() -> None:
    broker = _broker()
    broker_id = broker.place_order(_plan(quantity=10.0), "c1")
    assert broker.modify_order(broker_id, quantity=5.0, price=None) is True
    assert broker.modify_order(broker_id, quantity=0.0, price=None) is False
    assert broker.cancel_order(broker_id) is True
    assert broker.cancel_order(broker_id) is False
    assert broker.cancel_order("SANDBOX-999999") is False
    broker.place_order(_plan(quantity=10.0), "c2")
    broker.settle("c2", 100.0, "t")
    assert broker.positions() == [{"symbol": "RELIANCE", "quantity": 10.0}]
    assert broker.open_orders() == []
    assert broker.capital < 1_000_000.0


def test_not_connected_and_bad_orders() -> None:
    broker = SandboxBroker()
    with pytest.raises(BrokerError):
        broker.place_order(_plan(), "c1")
    broker.connect()
    with pytest.raises(BrokerError):
        broker.place_order(_plan(quantity=0.0), "c1")
    with pytest.raises(BrokerError):
        broker.place_order(_plan(), "c1")
        broker.place_order(_plan(), "c1")
    with pytest.raises(ValueError):
        SandboxBroker(capital=0.0)
