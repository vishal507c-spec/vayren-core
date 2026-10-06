"""Tests for FyersSessionAdapter live order execution.

Covers:
1. BUY order mapping
2. SELL order mapping
3. quantity mapping
4. price mapping
5. trigger/stop mapping (SL-M and SL-L)
6. order-type mapping
7. product-type mapping
8. successful FYERS response
9. broker order ID extraction
10. rejected order
11. authentication failure
12. timeout/network failure
13. invalid order (zero qty, negative price, empty symbol)
14. PAPER cannot call LIVE endpoint
15. SANDBOX cannot call LIVE endpoint
16. LIVE calls FYERS LIVE endpoint
17. credential secrecy in logs and errors
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest

from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.vocab import BrokerError, Environment, ErrorCode


@dataclass
class DummyPlan:
    symbol: str = "SBIN"
    side: str = "BUY"
    quantity: float = 10.0
    order_type: str = "MARKET"
    limit_price: float | None = None
    trigger_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "DAY"
    product: str = "INTRADAY"
    bracket: tuple[dict[str, object], ...] = ()


class MockTransport:
    """Mock HTTP transport capturing requests and simulating responses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.post_response: dict[str, Any] = {
            "s": "ok",
            "code": 1101,
            "message": "Order submitted successfully",
            "id": "FYERS-ORDER-9999",
        }
        self.get_response: dict[str, Any] = {
            "s": "ok",
            "code": 200,
            "data": {"fy_id": "XY12345", "name": "Test User"},
        }
        self.post_exc: Exception | None = None
        self.get_exc: Exception | None = None

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"method": "POST", "url": url, "payload": payload, "headers": headers})
        if self.post_exc:
            raise self.post_exc
        return self.post_response

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        if self.get_exc:
            raise self.get_exc
        return self.get_response

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"method": "PUT", "url": url, "payload": payload, "headers": headers})
        return {"s": "ok", "code": 1102, "message": "Order modified successfully"}

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"method": "DELETE", "url": url, "payload": payload, "headers": headers})
        return {"s": "ok", "code": 1103, "message": "Order cancelled successfully"}

    @property
    def post_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "POST"]


@pytest.fixture
def connected_adapter() -> tuple[FyersSessionAdapter, MockTransport]:
    transport = MockTransport()
    adapter = FyersSessionAdapter("APP_ID_123", "TOKEN_ABC_XYZ", transport=transport)
    adapter.connect()
    transport.calls.clear()
    return adapter, transport


# ── Scenario 1: BUY order mapping ──────────────────────────────────────


def test_buy_order_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    plan = DummyPlan(side="BUY")
    broker_id = adapter.place_order(plan, client_order_id="BUY_TAG_1")

    assert broker_id == "FYERS-ORDER-9999"
    assert len(transport.post_calls) == 1
    call = transport.post_calls[0]
    assert call["method"] == "POST"
    assert call["payload"]["side"] == 1
    assert call["payload"]["symbol"] == "NSE:SBIN-EQ"


# ── Scenario 2: SELL order mapping ─────────────────────────────────────


def test_sell_order_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    plan = DummyPlan(side="SELL")
    broker_id = adapter.place_order(plan, client_order_id="SELL_TAG_1")

    assert broker_id == "FYERS-ORDER-9999"
    assert len(transport.post_calls) == 1
    call = transport.post_calls[0]
    assert call["payload"]["side"] == -1


# ── Scenario 3: Quantity mapping ───────────────────────────────────────


def test_quantity_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    plan = DummyPlan(quantity=55.0)
    adapter.place_order(plan, client_order_id="QTY_TAG_1")
    assert transport.post_calls[0]["payload"]["qty"] == 55


def test_fractional_quantity_rejected(connected_adapter) -> None:
    adapter, _ = connected_adapter
    plan = DummyPlan(quantity=10.5)
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="FRAC_QTY")
    assert excinfo.value.code == ErrorCode.INVALID_REQUEST
    assert "whole units" in str(excinfo.value)


# ── Scenario 4: Price mapping ──────────────────────────────────────────


def test_limit_price_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    plan = DummyPlan(order_type="LIMIT", limit_price=785.50)
    adapter.place_order(plan, client_order_id="LMT_TAG_1")
    payload = transport.post_calls[0]["payload"]
    assert payload["type"] == 1  # 1 = Limit
    assert payload["limitPrice"] == 785.50
    assert payload["stopPrice"] == 0.0


# ── Scenario 5: Trigger / Stop mapping ─────────────────────────────────


def test_sl_market_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    plan = DummyPlan(order_type="SLM", trigger_price=750.25)
    adapter.place_order(plan, client_order_id="SLM_TAG_1")
    payload = transport.post_calls[0]["payload"]
    assert payload["type"] == 3  # 3 = Stop Market
    assert payload["stopPrice"] == 750.25
    assert payload["limitPrice"] == 0.0


def test_sl_limit_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    plan = DummyPlan(order_type="SLL", limit_price=751.00, trigger_price=750.00)
    adapter.place_order(plan, client_order_id="SLL_TAG_1")
    payload = transport.post_calls[0]["payload"]
    assert payload["type"] == 4  # 4 = Stop Limit
    assert payload["limitPrice"] == 751.00
    assert payload["stopPrice"] == 750.00


# ── Scenario 6: Order-type mapping ─────────────────────────────────────


def test_market_order_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    plan = DummyPlan(order_type="MARKET")
    adapter.place_order(plan, client_order_id="MKT_TAG_1")
    payload = transport.post_calls[0]["payload"]
    assert payload["type"] == 2  # 2 = Market
    assert payload["limitPrice"] == 0.0


# ── Scenario 7: Product-type mapping ───────────────────────────────────


def test_product_type_mapping(connected_adapter) -> None:
    adapter, transport = connected_adapter
    for prod_input, expected in [
        ("INTRADAY", "INTRADAY"),
        ("MIS", "INTRADAY"),
        ("CNC", "CNC"),
        ("DELIVERY", "CNC"),
        ("MARGIN", "MARGIN"),
        ("NRML", "MARGIN"),
    ]:
        transport.calls.clear()
        plan = DummyPlan(product=prod_input)
        adapter.place_order(plan, client_order_id=f"PROD_TAG_{prod_input}")
        assert transport.post_calls[0]["payload"]["productType"] == expected


# ── Scenario 8 & 9: Successful FYERS response & ID extraction ──────────


def test_successful_response_and_order_id_extraction(connected_adapter) -> None:
    adapter, transport = connected_adapter
    transport.post_response = {
        "s": "ok",
        "code": 1101,
        "message": "Order submitted successfully",
        "id": "241006000123456",
    }
    plan = DummyPlan()
    broker_id = adapter.place_order(plan, client_order_id="SUCCESS_TAG")
    assert broker_id == "241006000123456"

    # Repeated client_order_id returns cached broker ID without sending request
    transport.calls.clear()
    cached_id = adapter.place_order(plan, client_order_id="SUCCESS_TAG")
    assert cached_id == "241006000123456"
    assert len(transport.calls) == 0


# ── Scenario 10: Rejected order ────────────────────────────────────────


def test_rejected_order_raises_broker_error(connected_adapter) -> None:
    adapter, transport = connected_adapter
    transport.post_response = {
        "s": "error",
        "code": -300,
        "message": "Insufficient fund for order placement",
    }
    plan = DummyPlan()
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="REJECT_TAG")
    assert "insufficient funds" in str(excinfo.value).lower()
    assert excinfo.value.code == ErrorCode.INVALID_REQUEST


# ── Scenario 11: Authentication failure ────────────────────────────────


def test_authentication_failure(connected_adapter) -> None:
    adapter, transport = connected_adapter
    transport.post_response = {
        "s": "error",
        "code": -8,
        "message": "The token has expired or is invalid",
    }
    plan = DummyPlan()
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="AUTH_FAIL_TAG")
    assert excinfo.value.code == ErrorCode.AUTHENTICATION_FAILED


# ── Scenario 12: Timeout / network failure ─────────────────────────────


def test_timeout_network_failure(connected_adapter) -> None:
    adapter, transport = connected_adapter
    transport.post_exc = TimeoutError("Connection timed out to api-t1.fyers.in")
    plan = DummyPlan()
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="TIMEOUT_TAG")
    assert excinfo.value.code == ErrorCode.NETWORK_ERROR
    assert "network failure" in str(excinfo.value)


# ── Scenario 13: Invalid order (empty symbol, negative price, zero qty) ──


def test_invalid_symbol_rejected(connected_adapter) -> None:
    adapter, _ = connected_adapter
    plan = DummyPlan(symbol="")
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="NO_SYM")
    assert excinfo.value.code == ErrorCode.INVALID_SYMBOL


def test_zero_quantity_rejected(connected_adapter) -> None:
    adapter, _ = connected_adapter
    plan = DummyPlan(quantity=0.0)
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="ZERO_QTY")
    assert excinfo.value.code == ErrorCode.INVALID_REQUEST


def test_negative_limit_price_rejected(connected_adapter) -> None:
    adapter, _ = connected_adapter
    plan = DummyPlan(order_type="LIMIT", limit_price=-100.0)
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="NEG_PX")
    assert excinfo.value.code == ErrorCode.INVALID_REQUEST


# ── Scenario 14, 15, 16: Environment and Live Safety Gate ──────────────


def test_not_connected_fails_closed() -> None:
    transport = MockTransport()
    adapter = FyersSessionAdapter("APP_ID", "TOKEN", transport=transport)
    # Not connected
    plan = DummyPlan()
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="NOT_CONN")
    assert excinfo.value.code == ErrorCode.NOT_CONNECTED
    assert len(transport.calls) == 0


def test_missing_credentials_fails_closed() -> None:
    transport = MockTransport()
    adapter = FyersSessionAdapter("", "", transport=transport)
    adapter._connected = True
    plan = DummyPlan()
    with pytest.raises(BrokerError) as excinfo:
        adapter.place_order(plan, client_order_id="NO_CREDS")
    assert excinfo.value.code == ErrorCode.CREDENTIALS_NOT_READY
    assert len(transport.calls) == 0


def test_paper_cannot_call_live_endpoint() -> None:
    from execution.broker.paper import PaperBroker
    from execution.models.order import OrderPlan

    paper = PaperBroker()
    paper.connect()
    plan = OrderPlan(intent_id="test-paper-1", symbol="SBIN", side="BUY", quantity=10.0)
    broker_id = paper.place_order(plan, "PAPER_1")
    # Paper uses simulated order ids, never network transport
    assert broker_id.startswith("PAPER-")
    assert paper.name == "paper"


def test_sandbox_cannot_call_live_endpoint() -> None:
    from execution.broker.sandbox import SandboxBroker
    from execution.models.order import OrderPlan

    sandbox = SandboxBroker()
    sandbox.connect()
    plan = OrderPlan(intent_id="test-sandbox-1", symbol="SBIN", side="BUY", quantity=10.0)
    broker_id = sandbox.place_order(plan, "SANDBOX_1")
    # Sandbox uses simulated order ids, never network transport
    assert broker_id.startswith("SANDBOX-")
    assert sandbox.name == "sandbox"


def test_adapter_environment_is_live() -> None:
    adapter = FyersSessionAdapter("A", "B")
    assert adapter.environment == Environment.LIVE


# ── Scenario 17: Credential Secrecy in Logs ────────────────────────────


def test_no_sensitive_tokens_logged(connected_adapter, caplog) -> None:
    adapter, _ = connected_adapter
    token = "TOKEN_ABC_XYZ"
    app_id = "APP_ID_123"

    with caplog.at_level(logging.INFO):
        adapter.place_order(DummyPlan(), client_order_id="SEC_LOG_1")

    for record in caplog.records:
        assert token not in record.message
        assert app_id not in record.message
        # App ID / tokens should not appear in order logging
        assert "mode=LIVE" in record.message or "ORDER_RESPONSE" in record.message
