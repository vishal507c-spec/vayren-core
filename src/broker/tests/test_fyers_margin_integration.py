"""Comprehensive verification for FYERS MIS Margin API integration.

Covers all 10 required conditions:
1. MARKET order sends limitPrice = 0.0.
2. LIMIT order sends its valid positive limit price.
3. STOP_MARKET and STOP_LIMIT payloads follow verified FYERS specifications.
4. BUY and SELL margin requests.
5. All 22 symbols use the corrected shared code path.
6. HTTP 400 does not unnecessarily open a global circuit breaker.
7. Margin API failure blocks only the affected stock.
8. Missing reference price never produces an artificial ₹1 margin estimate.
9. Valid margin response permits the normal risk checks to continue.
10. Per-stock isolation and fail-closed safety.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from broker.common.api_governance import CircuitBreakerState
from broker.providers.fyers.live_auth import AuthError
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.vocab import BrokerError, ErrorCode
from execution.models.intent import ExecutionIntent
from execution.models.order import OrderPlan
from execution.modes import ExecutionMode, LiveArm
from execution.runtime.lifecycle import LifecycleState
from execution.runtime.session import LiveSession, SessionConfig
from risk import RiskPolicy
from risk.sizing import READY, SizingVerdict

ALL_22_SYMBOLS = [
    "NSE:DREDGECORP",
    "NSE:WAAREERTL",
    "NSE:KEC",
    "NSE:KAYNES",
    "NSE:AVANTIFEED",
    "NSE:ROUTE",
    "NSE:RAILTEL",
    "NSE:SCI",
    "NSE:MOIL",
    "NSE:CRAMC",
    "NSE:LXCHEM",
    "NSE:NOCIL",
    "NSE:DAMCAPITAL",
    "NSE:JSWCEMENT",
    "NSE:TEXRAIL",
    "NSE:ZEEL",
    "NSE:VIKRAN",
    "NSE:IFCI",
    "NSE:DCW",
    "NSE:SAGILITY",
    "NSE:JINDWORLD",
    "NSE:NETWEB",
]


class _DummyTransport:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.last_payload: dict[str, Any] | None = None
        self.post_count = 0

    def post_json(
        self,
        url: str,  # noqa: ARG002
        payload: dict[str, Any],  # noqa: ARG002
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.post_count += 1
        self.last_payload = payload
        for key, resp in self.responses.items():
            if key in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return {"s": "ok", "code": 200, "data": {"margin_total": 50.0}}

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        if "funds" in url:
            return {
                "s": "ok",
                "code": 200,
                "fund_limit": [
                    {"id": 10, "title": "Available Balance", "equityAmount": 500.0},
                    {"id": 1, "title": "Total Balance", "equityAmount": 500.0},
                ],
            }
        return {"s": "ok", "code": 200, "data": []}

    def put_json(
        self,
        url: str,  # noqa: ARG002
        payload: dict[str, Any],  # noqa: ARG002
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {}

    def delete_json(
        self,
        url: str,  # noqa: ARG002
        payload: dict[str, Any],  # noqa: ARG002
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {}


def _make_adapter(transport: _DummyTransport) -> FyersSessionAdapter:
    adapter = FyersSessionAdapter(
        "APP_ID_123", "TOKEN_ABC_XYZ", transport=transport, enable_order_ws=False
    )
    adapter._token = "valid-token"
    adapter._app_id = "test-app-id"
    adapter._connected = True
    return adapter


def test_market_order_sends_limit_price_zero() -> None:
    """1. MARKET order (type=2) must ALWAYS send limitPrice = 0.0 and stopPrice = 0.0."""
    transport = _DummyTransport()
    adapter = _make_adapter(transport)

    plan = OrderPlan(
        intent_id="i1",
        symbol="NSE:DCW",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=45.69,
    )
    margin = adapter.order_margin(plan)
    assert margin == 50.0
    assert transport.last_payload is not None
    order_data = transport.last_payload["data"][0]
    assert order_data["type"] == 2
    assert order_data["limitPrice"] == 0.0
    assert order_data["stopPrice"] == 0.0
    assert order_data["qty"] == 1


def test_limit_order_sends_valid_positive_limit_price() -> None:
    """2. LIMIT order (type=1) must send its valid positive limit price."""
    transport = _DummyTransport()
    adapter = _make_adapter(transport)

    plan = OrderPlan(
        intent_id="i2",
        symbol="NSE:KAYNES",
        side="BUY",
        quantity=5.0,
        order_type="LIMIT",
        limit_price=3300.0,
    )
    margin = adapter.order_margin(plan)
    assert margin == 50.0
    assert transport.last_payload is not None
    order_data = transport.last_payload["data"][0]
    assert order_data["type"] == 1
    assert order_data["limitPrice"] == 3300.0
    assert order_data["stopPrice"] == 0.0
    assert order_data["qty"] == 5


def test_stop_market_and_stop_limit_follow_fyers_spec() -> None:
    """3. STOP_MARKET and STOP_LIMIT payloads follow verified FYERS API v3 specifications."""
    transport = _DummyTransport()
    adapter = _make_adapter(transport)

    # STOP_MARKET (type=3): limitPrice = 0.0, stopPrice = trigger
    plan_slm = OrderPlan(
        intent_id="i3",
        symbol="NSE:DCW",
        side="SELL",
        quantity=2.0,
        order_type="STOP_MARKET",
        stop_price=44.50,
    )
    adapter.order_margin(plan_slm)
    assert transport.last_payload is not None
    slm_data = transport.last_payload["data"][0]
    assert slm_data["type"] == 3
    assert slm_data["limitPrice"] == 0.0
    assert slm_data["stopPrice"] == 44.50

    # STOP_LIMIT (type=4): limitPrice = price, stopPrice = trigger
    plan_sll = OrderPlan(
        intent_id="i4",
        symbol="NSE:DCW",
        side="SELL",
        quantity=2.0,
        order_type="STOP_LIMIT",
        limit_price=44.00,
        stop_price=44.50,
    )
    adapter.order_margin(plan_sll)
    assert transport.last_payload is not None
    sll_data = transport.last_payload["data"][0]
    assert sll_data["type"] == 4
    assert sll_data["limitPrice"] == 44.00
    assert sll_data["stopPrice"] == 44.50


def test_buy_and_sell_margin_requests() -> None:
    """4. BUY and SELL margin requests format side correctly and preserve limitPrice rules."""
    transport = _DummyTransport()
    adapter = _make_adapter(transport)

    buy_plan = OrderPlan(
        intent_id="i_buy",
        symbol="NSE:DREDGECORP",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=872.90,
    )
    adapter.order_margin(buy_plan)
    assert transport.last_payload is not None
    assert transport.last_payload["data"][0]["side"] == 1
    assert transport.last_payload["data"][0]["limitPrice"] == 0.0

    sell_plan = OrderPlan(
        intent_id="i_sell",
        symbol="NSE:DREDGECORP",
        side="SELL",
        quantity=1.0,
        order_type="MARKET",
        price=872.90,
    )
    adapter.order_margin(sell_plan)
    assert transport.last_payload is not None
    assert transport.last_payload["data"][0]["side"] == -1
    assert transport.last_payload["data"][0]["limitPrice"] == 0.0


def test_all_22_symbols_use_corrected_shared_code_path() -> None:
    """5. All 22 configured symbols use the corrected shared code path."""
    transport = _DummyTransport()
    adapter = _make_adapter(transport)

    for symbol in ALL_22_SYMBOLS:
        plan = OrderPlan(
            intent_id=f"intent_{symbol}",
            symbol=symbol,
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
            price=100.0,
        )
        margin = adapter.order_margin(plan)
        assert margin == 50.0
        assert transport.last_payload is not None
        item = transport.last_payload["data"][0]
        assert item["type"] == 2
        assert item["limitPrice"] == 0.0
        assert item["stopPrice"] == 0.0
        assert item["symbol"] == f"{symbol}-EQ"


def test_http_400_does_not_unnecessarily_open_circuit_breaker() -> None:
    """6. HTTP 400 validation error does NOT trip or open the global read circuit breaker."""
    err = AuthError(
        "limitPrice does not match: 0 (HTTP 400)",
        code="INVALID_REQUEST",
        http_status=400,
    )
    transport = _DummyTransport(responses={"multiorder/margin": err})
    adapter = _make_adapter(transport)

    for i in range(10):
        plan = OrderPlan(
            intent_id=f"plan_{i}",
            symbol="NSE:DCW",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
            price=45.69,
        )
        margin = adapter.order_margin(plan)
        assert margin == 11.42

    governor = adapter._gateway._governor
    assert governor.read_circuit.state == CircuitBreakerState.CLOSED

    funds = adapter.funds()
    assert isinstance(funds, dict)


def test_missing_reference_price_never_produces_artificial_one_rupee_margin() -> None:
    """8. Missing reference price raises BrokerError and never defaults to artificial margin."""
    err = AuthError("venue down (HTTP 500)", code="VENUE", http_status=500)
    transport = _DummyTransport(responses={"multiorder/margin": err})
    adapter = _make_adapter(transport)
    adapter.last_price = MagicMock(return_value=None)

    plan_no_price = OrderPlan(
        intent_id="i_blind",
        symbol="NSE:DCW",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=None,
    )
    with pytest.raises(BrokerError) as exc_info:
        adapter.order_margin(plan_no_price)

    assert exc_info.value.code == ErrorCode.INVALID_REQUEST
    assert "unable to determine reference price" in str(exc_info.value)


def test_valid_margin_response_permits_normal_risk_checks_to_continue() -> None:
    """9. Valid margin response (e.g. from FYERS API) permits the normal execution checks."""
    transport = _DummyTransport(
        responses={"multiorder/margin": {"s": "ok", "code": 200, "data": {"margin_total": 11.42}}}
    )
    adapter = _make_adapter(transport)
    plan = OrderPlan(
        intent_id="i_ok",
        symbol="NSE:DCW",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=45.69,
    )
    margin = adapter.order_margin(plan)
    assert margin == 11.42


def test_margin_failure_blocks_only_affected_stock_per_stock_isolation() -> None:
    """7 & 10. Margin failure on Stock A blocks only Stock A; Stock B passes independently."""

    class _Provider:
        capabilities = ("candles",)

        def open(self, symbols, timeframe) -> None: ...  # noqa: ANN001

        def poll(self):  # noqa: ANN204
            return ()

    class _IsolatedBroker:
        name = "isolated-fyers"

        def __init__(self) -> None:
            self.placed: list[str] = []

        def health(self) -> tuple[bool, str]:
            return (True, "ok")

        def funds(self) -> dict[str, Any]:
            return {"available": 500.0}

        def order_margin(self, plan: Any) -> float:
            if plan.symbol == "NSE:BADSTOCK":
                raise BrokerError(
                    "unable to determine reference price for margin calculation: NSE:BADSTOCK",
                    code=ErrorCode.INVALID_REQUEST,
                )
            return 11.42

        def place_order(self, plan: Any, client_order_id: str) -> str:
            self.placed.append(f"{plan.symbol}:{client_order_id}")
            return "ORDER-1"

    broker = _IsolatedBroker()

    def _make_session() -> LiveSession:
        cfg = SessionConfig(
            mode=ExecutionMode.LIVE,
            journal_path=None,
            memory_path=None,
            kill_switch_path=None,
        )
        s = LiveSession(
            cfg,
            _Provider(),  # pyright: ignore[reportArgumentType]
            RiskPolicy(max_order_qty=10_000.0, max_position_qty=10_000.0),
            request_id="test-isolation",
        )
        s._mode = ExecutionMode.LIVE
        s._armed = LiveArm.ARMED
        s._broker = broker  # pyright: ignore[reportAttributeAccessIssue]
        for target in (
            LifecycleState.VALIDATING,
            LifecycleState.WARMING_UP,
            LifecycleState.READY,
            LifecycleState.RUNNING,
        ):
            s._lifecycle.transition(target, reason="test")
        return s

    session = _make_session()

    # Stock A: NSE:BADSTOCK -> margin check raises -> blocked
    bad_intent = ExecutionIntent(
        intent_id="intent_bad",
        strategy_id="strat",
        strategy_version="1",
        signal_id="sig1",
        timestamp="2026-11-04T09:15:00+05:30",
        event_seq=1,
        symbol="NSE:BADSTOCK",
        side="BUY",
        target_position_qty=1.0,
        quantity=1.0,
        urgency="normal",
        preferred_order_type="MARKET",
        reason="test",
        confidence=1.0,
    )
    bad_plan = OrderPlan(
        intent_id="intent_bad",
        symbol="NSE:BADSTOCK",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=None,
    )
    session._sized_risk["intent_bad"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=500.0,
        effective_capital=2000.0,
        max_risk_per_stock=3.0,
        entry_price=10.0,
        stop_price=9.0,
        risk_per_share=1.0,
        quantity=1,
        planned_risk=1.0,
    )
    session._submit(bad_intent, bad_plan, 1_700_000_000.0)

    # Verify Stock A was blocked and not placed
    assert broker.placed == []
    assert any(
        e.kind == "ORDER_BLOCKED"
        and "broker margin check failed" in str(e.payload.get("reason", ""))
        for e in session._journal.entries
    )

    # Stock B: NSE:DCW -> margin check passes (11.42 <= 500.0) -> APPROVED & PLACED
    good_intent = ExecutionIntent(
        intent_id="intent_good",
        strategy_id="strat",
        strategy_version="1",
        signal_id="sig2",
        timestamp="2026-11-04T09:15:00+05:30",
        event_seq=2,
        symbol="NSE:DCW",
        side="BUY",
        target_position_qty=1.0,
        quantity=1.0,
        urgency="normal",
        preferred_order_type="MARKET",
        reason="test",
        confidence=1.0,
    )
    good_plan = OrderPlan(
        intent_id="intent_good",
        symbol="NSE:DCW",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=45.69,
    )
    session._sized_risk["intent_good"] = SizingVerdict(
        ready=True,
        reason=READY,
        broker_capital=500.0,
        effective_capital=2000.0,
        max_risk_per_stock=3.0,
        entry_price=45.69,
        stop_price=45.25,
        risk_per_share=0.44,
        quantity=1,
        planned_risk=0.44,
    )
    session._submit(good_intent, good_plan, 1_700_000_000.0)

    # Verify Stock B was placed independently despite Stock A's failure
    assert broker.placed == ["NSE:DCW:intent_good:o1"]
