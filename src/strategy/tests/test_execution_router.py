"""Phase 5 execution routing — failure injection without any network.

FakeBroker implements the router's structural adapter contract with
scripted accept/reject/timeout/disconnect/query behaviors and full call
logs (proving what the router sent, what it never sent, and the exact
venue symbols it used). Covers the §30 matrix (abstraction, selection,
per-order routing, UNKNOWN discipline, ownership, modes, isolation,
risk-first) plus the §29 static proofs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy.execution_router import (  # noqa: E402
    BrokerExecutionAdapter,
    ExecutionRouter,
    ExecutionRouterError,
)
from strategy.instrument_registry import reset_instrument_registry  # noqa: E402
from strategy.models.execution import (  # noqa: E402
    ATTENTION_REQUIRED,
    REJECTED_SAFE,
    ROUTED_PRIMARY,
    ROUTED_SECONDARY,
    OrderIntent,
    RouterExecutionConfig,
)
from strategy.provider_mapping import ProviderMappingRegistry  # noqa: E402

STRAT_ROOT = ROOT / "src" / "strategy"
RELIANCE = "NSE:EQUITY:RELIANCE"
TCS = "NSE:EQUITY:TCS"
SBIN = "NSE:EQUITY:SBIN"
KEC = "NSE:EQUITY:KEC"


class FakeBrokerError(Exception):
    """Venue failure with a duck-typed ``code`` (mirrors BrokerError)."""

    def __init__(self, message: str, code: str = "UNKNOWN") -> None:
        super().__init__(message)
        self.code = code


class FakeBroker:
    """Scripted execution adapter (accept/reject/timeout/disconnect/query)."""

    def __init__(self, name: str, healthy: bool = True) -> None:
        self.name = name
        self._healthy = healthy
        self._reason = "fake ready" if healthy else "fake down"
        self.calls: list[tuple[Any, ...]] = []
        self.behavior = "accept"
        self.reject_code = "INVALID_REQUEST"
        self.query_status: Any = "ACKNOWLEDGED"
        self.query_extra: dict[str, Any] = {}
        self._seq = 0

    def set_healthy(self, healthy: bool, reason: str = "") -> None:
        self._healthy = healthy
        self._reason = reason or ("fake ready" if healthy else "fake down")

    def health(self) -> tuple[bool, str]:
        return self._healthy, self._reason

    def place_order(self, plan: Any, client_order_id: str) -> str:
        self.calls.append(
            (
                "place",
                getattr(plan, "symbol", ""),
                getattr(plan, "quantity", 0.0),
                getattr(plan, "limit_price", None),
                getattr(plan, "stop_price", None),
                client_order_id,
            )
        )
        if self.behavior == "accept":
            self._seq += 1
            return f"{self.name}-OID-{self._seq:04d}"
        if self.behavior == "reject":
            raise FakeBrokerError(f"{self.name} refused order", self.reject_code)
        if self.behavior == "timeout":
            raise TimeoutError(f"{self.name} submission timed out")
        if self.behavior == "explode":
            raise ConnectionError(f"{self.name} connection reset")
        if self.behavior == "empty":
            return ""
        raise AssertionError(f"unknown behavior {self.behavior!r}")

    def cancel_order(self, broker_order_id: str) -> bool:
        self.calls.append(("cancel", broker_order_id))
        if self.behavior == "cancel-fail":
            return False
        if self.behavior == "cancel-explode":
            raise FakeBrokerError("cancel transport down", "NETWORK_ERROR")
        return True

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool:
        self.calls.append(("modify", broker_order_id, quantity, price))
        return self.behavior != "modify-fail"

    def query_order(self, broker_order_id: str) -> dict[str, Any]:
        self.calls.append(("query", broker_order_id))
        return {"status": self.query_status, **self.query_extra}


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    yield
    reset_instrument_registry()


def _mappings() -> ProviderMappingRegistry:
    registry = ProviderMappingRegistry()
    registry.ingest_master(
        "FYERS", ["NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:SBIN-EQ"], source="fake-fyers"
    )
    registry.ingest_master(
        "ZERODHA",
        [
            {
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 738561,
            },
            {
                "tradingsymbol": "TCS",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 2953216,
            },
        ],
        source="fake-kite",
    )
    return registry


def _router(
    mappings: ProviderMappingRegistry | None = None,
    config: RouterExecutionConfig | None = None,
) -> tuple[ExecutionRouter, FakeBroker, FakeBroker]:
    zerodha = FakeBroker("ZERODHA")
    fyers = FakeBroker("FYERS")
    router = ExecutionRouter(
        mappings or _mappings(),
        config=config or RouterExecutionConfig(),
        brokers={"ZERODHA": zerodha, "FYERS": fyers},
    )
    return router, zerodha, fyers


_CID = [0]


def _intent(
    instrument_id: str = RELIANCE,
    symbol: str = "NSE:RELIANCE",
    quantity: float = 10.0,
    **overrides: Any,
) -> OrderIntent:
    _CID[0] += 1
    params: dict[str, Any] = {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "side": "BUY",
        "quantity": quantity,
        "strategy_id": "Momentum",
        "client_order_id": f"cid-{_CID[0]:04d}",
        "mode": "LIVE",
        "risk_approved": True,
    }
    params.update(overrides)
    return OrderIntent(**params)


def test_01_broker_abstraction_contract() -> None:
    router, zerodha, _ = _router()
    assert isinstance(zerodha, BrokerExecutionAdapter)
    with pytest.raises(ExecutionRouterError):
        router.add_broker("", zerodha)
    with pytest.raises(ExecutionRouterError):
        router.add_broker("BROKEN", object())


def test_02_broker_registration_visible() -> None:
    router, _, _ = _router()
    assert router.diagnostics()["primary"] == "ZERODHA"
    assert set(router.broker_health()) >= {"ZERODHA", "FYERS"}


def test_03_broker_readiness_states() -> None:
    router, zerodha, _ = _router()
    assert router.broker_health("ZERODHA")["ZERODHA"]["state"] == "HEALTHY"
    zerodha.set_healthy(False, "not connected")
    assert router.broker_health("ZERODHA")["ZERODHA"]["state"] == "DISCONNECTED"
    zerodha.set_healthy(False, "venue error")
    assert router.broker_health("ZERODHA")["ZERODHA"]["state"] == "ERROR"
    assert router.broker_health("NOPE")["NOPE"]["state"] == "DISABLED"


def test_04_canonical_resolves_to_broker_mapping() -> None:
    router, zerodha, _ = _router()
    result = router.route(_intent())
    assert result.status == "SUBMITTED"
    assert result.broker == "ZERODHA"
    # Zerodha Kite needs the bare tradingsymbol (mapping record symbol).
    assert zerodha.calls[0][1] == "RELIANCE"
    assert zerodha.calls[0][2] == 10.0


def test_05_primary_broker_selected() -> None:
    router, _, _ = _router()
    result = router.route(_intent())
    assert result.routing == ROUTED_PRIMARY
    assert result.broker == "ZERODHA"


def test_06_secondary_broker_selected() -> None:
    router, zerodha, fyers = _router()
    zerodha.set_healthy(False, "down for maintenance")
    result = router.route(_intent())
    assert result.routing == ROUTED_SECONDARY
    assert result.broker == "FYERS"
    # FYERS addresses the mapping id itself.
    assert fyers.calls[0][1] == "NSE:RELIANCE-EQ"
    assert zerodha.calls == []


def test_07_per_order_routing() -> None:
    router, zerodha, fyers = _router()
    first = router.route(_intent())
    assert first.broker == "ZERODHA"
    zerodha.set_healthy(False, "blip")
    second = router.route(_intent(instrument_id=TCS, symbol="NSE:TCS"))
    assert second.broker == "FYERS"
    zerodha.set_healthy(True)
    third = router.route(_intent())
    assert third.broker == "ZERODHA"


def test_08_primary_rejection_routes_secondary() -> None:
    router, zerodha, fyers = _router()
    zerodha.behavior = "reject"
    zerodha.reject_code = "INVALID_REQUEST"
    result = router.route(_intent())
    assert result.routing == ROUTED_SECONDARY
    assert result.broker == "FYERS"
    assert result.status == "SUBMITTED"
    kinds = [event["type"] for event in router.events()]
    assert "ORDER_REJECTED" in kinds


def test_09_unknown_acceptance_never_retries() -> None:
    router, zerodha, fyers = _router()
    zerodha.behavior = "timeout"
    result = router.route(_intent())
    assert result.status == "UNKNOWN"
    assert result.broker == "ZERODHA"
    assert result.attention_required is True
    assert "unknown" in result.rejection_reason.lower()
    # The secondary MUST NOT receive an order of uncertain acceptance.
    assert fyers.calls == []
    kinds = [event["type"] for event in router.events()]
    assert "ORDER_STATUS_UNKNOWN" in kinds


def test_10_accepted_primary_never_duplicates() -> None:
    router, zerodha, fyers = _router()
    result = router.route(_intent())
    assert result.status == "SUBMITTED"
    assert fyers.calls == []
    assert len(zerodha.calls) == 1


def test_11_missing_mapping_safe_rejection() -> None:
    router, _, fyers = _router()
    # KEC is canonical but mapped nowhere in this fixture.
    result = router.route(_intent(instrument_id=KEC, symbol="NSE:KEC"))
    assert result.status == "REJECTED"
    assert result.routing == REJECTED_SAFE
    assert "mapping missing" in result.rejection_reason
    assert fyers.calls == []


def test_12_disabled_mapping_safe_rejection() -> None:
    mappings = ProviderMappingRegistry()
    mappings.ingest_master(
        "ZERODHA",
        [
            {
                "tradingsymbol": "SBIN",
                "exchange": "NSE",
                "segment": "NSE",
                "instrument_token": 779521,
            }
        ],
    )
    mappings.disable_mapping("ZERODHA", "779521")
    router, zerodha, fyers = _router(mappings)
    # SBIN's only mapping (primary Zerodha) is disabled; FYERS has none.
    result = router.route(_intent(instrument_id=SBIN, symbol="NSE:SBIN"))
    assert result.status == "REJECTED"
    assert "disabled" in result.rejection_reason
    assert zerodha.calls == [] and fyers.calls == []


def test_13_broker_unavailable_routes_secondary() -> None:
    router = ExecutionRouter(_mappings(), brokers={"FYERS": FakeBroker("FYERS")})
    result = router.route(_intent())
    assert result.broker == "FYERS"
    assert result.routing == ROUTED_SECONDARY


def test_14_no_broker_ready_safe_rejection() -> None:
    router, zerodha, fyers = _router()
    zerodha.set_healthy(False, "down")
    fyers.set_healthy(False, "down")
    result = router.route(_intent())
    assert result.status == "REJECTED"
    assert result.routing == REJECTED_SAFE
    assert zerodha.calls == [] and fyers.calls == []


def test_15_cancel_routes_to_owning_broker() -> None:
    router, zerodha, fyers = _router()
    placed = router.route(_intent())
    zerodha.set_healthy(False, "gone after accept")
    fyers.set_healthy(True)
    cancelled = router.cancel_order(placed.client_order_id)
    assert cancelled.status == "CANCELLED"
    # The owning (down) broker is still called — never failed over.
    assert ("cancel", placed.broker_order_id) in zerodha.calls
    assert all(call[0] != "cancel" for call in fyers.calls)


def test_16_modify_routes_to_owning_broker() -> None:
    router, zerodha, fyers = _router()
    placed = router.route(_intent())
    modified = router.modify_order(placed.client_order_id, quantity=5.0, price=2490.0)
    assert modified.status == "MODIFIED"
    assert ("modify", placed.broker_order_id, 5.0, 2490.0) in zerodha.calls
    assert fyers.calls == []


def test_17_query_routes_to_owning_broker() -> None:
    router, zerodha, _ = _router()
    placed = router.route(_intent())
    zerodha.query_status = "COMPLETE"
    zerodha.query_extra = {"filled_qty": 10.0, "average_price": 2501.5}
    queried = router.query_order(placed.client_order_id)
    assert queried.status == "FILLED"
    assert queried.filled_quantity == 10.0
    assert queried.average_price == 2501.5
    assert ("query", placed.broker_order_id) in zerodha.calls


def test_18_statuses_normalize() -> None:
    router, zerodha, _ = _router()
    placed = router.route(_intent())
    cases = [
        ("COMPLETE", "FILLED"),
        ("REJECTED", "REJECTED"),
        ("CANCELLED", "CANCELLED"),
        ("OPEN", "ACKNOWLEDGED"),
        ("TRIGGER PENDING", "ACKNOWLEDGED"),
        ("SUBMITTED", "SUBMITTED"),
        (5, "REJECTED"),
        (1, "CANCELLED"),
        (6, "CANCELLED"),
        ("SOMETHING_WEIRD", "UNKNOWN"),
        ("", "UNKNOWN"),
    ]
    for raw, expected in cases:
        zerodha.query_status = raw
        assert router.query_order(placed.client_order_id).status == expected, raw


def test_19_broker_order_ids_stay_isolated() -> None:
    router, _, _ = _router()
    first = router.route(_intent())
    second = router.route(_intent())
    assert first.broker_order_id != second.broker_order_id
    assert first.broker_order_id.startswith("ZERODHA-")
    owned = {row["client_order_id"]: row["broker"] for row in router.owned_orders()}
    assert owned[first.client_order_id] == "ZERODHA"
    assert owned[second.client_order_id] == "ZERODHA"


def test_20_client_order_ids_unique_and_stable() -> None:
    first = _intent()
    second = _intent()
    assert first.client_order_id != second.client_order_id
    explicit = _intent(client_order_id="my-order-1")
    assert explicit.client_order_id == "my-order-1"


def test_21_duplicate_protection_intact() -> None:
    router, zerodha, _ = _router()
    intent = _intent()
    first = router.route(intent)
    second = router.route(intent)
    assert [call for call in zerodha.calls if call[0] == "place"] == [zerodha.calls[0]]
    assert second.broker_order_id == first.broker_order_id
    assert "duplicate" in second.rejection_reason


def test_22_paper_stays_paper() -> None:
    paper = FakeBroker("PAPER")
    live = FakeBroker("ZERODHA")
    router = ExecutionRouter(_mappings(), brokers={"PAPER": paper, "ZERODHA": live})
    result = router.route(_intent(mode="PAPER"))
    assert result.broker == "PAPER"
    assert result.status == "SUBMITTED"
    assert live.calls == []


def test_23_sandbox_never_touches_live() -> None:
    sandbox = FakeBroker("SANDBOX")
    live = FakeBroker("FYERS")
    router = ExecutionRouter(_mappings(), brokers={"SANDBOX": sandbox, "FYERS": live})
    result = router.route(_intent(mode="SANDBOX"))
    assert result.broker == "SANDBOX"
    assert live.calls == []


def test_24_live_uses_configured_adapters() -> None:
    router, zerodha, fyers = _router()
    result = router.route(_intent(mode="LIVE"))
    assert result.broker in ("ZERODHA", "FYERS")
    assert zerodha.calls != [] or fyers.calls != []


def test_25_strategy_independent_of_broker() -> None:
    fields = set(OrderIntent.__dataclass_fields__)
    assert "fyers" not in str(fields).lower()
    assert "token" not in str(fields).lower()
    assert "broker_order_id" not in fields
    intent = _intent()
    assert intent.instrument_id == RELIANCE
    assert intent.symbol == "NSE:RELIANCE"


def test_26_multi_strategy_isolation() -> None:
    router, _, _ = _router()
    momentum = router.route(_intent(strategy_id="Momentum"))
    breakout = router.route(_intent(strategy_id="Breakout"))
    assert momentum.strategy_id == "Momentum"
    assert breakout.strategy_id == "Breakout"
    assert momentum.client_order_id != breakout.client_order_id
    assert momentum.broker_order_id != breakout.broker_order_id
    owned = {row["client_order_id"]: row["strategy_id"] for row in router.owned_orders()}
    assert owned[momentum.client_order_id] == "Momentum"
    assert owned[breakout.client_order_id] == "Breakout"


def test_27_risk_gates_execute_first() -> None:
    router, zerodha, fyers = _router()
    result = router.route(_intent(risk_approved=False))
    assert result.status == "REJECTED"
    assert "risk approval" in result.rejection_reason
    assert zerodha.calls == [] and fyers.calls == []


def test_28_risk_denied_never_reaches_broker() -> None:
    router, zerodha, fyers = _router()
    denied = _intent(risk_approved=False, quantity=100.0)
    result = router.route(denied)
    assert result.routing == REJECTED_SAFE
    assert result.broker == ""
    assert result.broker_order_id == ""
    assert zerodha.calls == [] and fyers.calls == []


def test_29_invalid_intents_refused_before_brokers() -> None:
    router, zerodha, _ = _router()
    assert router.route(_intent(side="HOLD")).status == "REJECTED"
    assert router.route(_intent(quantity=0)).status == "REJECTED"
    assert router.route(_intent(quantity=float("nan"))).status == "REJECTED"
    assert router.route(_intent(order_type="LIMIT")).status == "REJECTED"
    assert router.route(_intent(order_type="STOP_MARKET")).status == "REJECTED"
    assert router.route(_intent(mode="MARGIN")).status == "REJECTED"
    assert zerodha.calls == []


def test_30_limit_and_stop_prices_pass_through_exactly() -> None:
    router, zerodha, _ = _router()
    intent = _intent(order_type="STOP_LIMIT", limit_price=2510.25, stop_price=2490.5)
    result = router.route(intent)
    assert result.status == "SUBMITTED"
    _, _, qty, limit, stop, _ = zerodha.calls[0]
    assert qty == 10.0 and limit == 2510.25 and stop == 2490.5


def test_31_cancel_unknown_is_explicit() -> None:
    router, _, _ = _router()
    result = router.cancel_order("never-routed")
    assert "unknown client order" in result.rejection_reason


def test_32_owner_gone_needs_attention() -> None:
    router = ExecutionRouter(_mappings(), brokers={"ZERODHA": FakeBroker("ZERODHA")})
    placed = router.route(_intent())
    router._brokers.pop("ZERODHA")
    cancelled = router.cancel_order(placed.client_order_id)
    assert cancelled.routing == ATTENTION_REQUIRED
    assert cancelled.attention_required is True
    assert "unavailable" in cancelled.rejection_reason


def test_33_cancel_failure_needs_attention() -> None:
    router, zerodha, _ = _router()
    placed = router.route(_intent())
    zerodha.behavior = "cancel-fail"
    result = router.cancel_order(placed.client_order_id)
    assert result.attention_required is True
    assert result.status == "SUBMITTED"


def test_34_modify_validation_first() -> None:
    router, zerodha, _ = _router()
    placed = router.route(_intent())
    before = len(zerodha.calls)
    assert "nothing to modify" in router.modify_order(placed.client_order_id).rejection_reason
    assert "invalid" in router.modify_order(placed.client_order_id, quantity=-1).rejection_reason
    assert len(zerodha.calls) == before


def test_35_query_unknown_is_explicit() -> None:
    router, _, _ = _router()
    result = router.query_order("ghost")
    assert "unknown client order" in result.rejection_reason


def test_36_no_real_network_in_unit_tests() -> None:
    import strategy.execution_router as module

    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    assert "import socket" not in source
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "fyers_apiv3" not in source
    assert "kiteconnect" not in source
