"""ReadOnlyBroker contract: reads pass through, mutations never do.

The funds passthrough is the load-bearing part: sizing runs on the venue's
own reported capital, so hiding it behind the read-only facade silently
fails every LIVE order closed at ``RISK_DENIED``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from execution.broker.adapter import BrokerError  # noqa: E402
from execution.broker.readonly import ReadOnlyBroker  # noqa: E402
from execution.models.order import OrderPlan  # noqa: E402

CAPITAL = {"available": 100_000.0, "used": 0.0, "total": 100_000.0, "equity": 100_000.0}


class _Venue:
    """Minimal adapter: reports funds, records nothing mutated."""

    name = "dry-venue"

    def __init__(self, funds: dict | None = None, *, has_funds: bool = True) -> None:
        self._funds = CAPITAL if funds is None else funds
        self._has_funds = has_funds

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("orders.market", "account.funds")

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def account(self) -> dict:
        return {"account_id": "ACC1", "mode": "LIVE"}

    def funds(self) -> dict:
        return dict(self._funds)

    def positions(self) -> list[dict]:
        return []

    def open_orders(self) -> list[dict]:
        return []

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:  # noqa: ARG002
        raise AssertionError("read-only facade must never reach the venue")

    def cancel_order(self, broker_order_id: str) -> bool:  # noqa: ARG002
        raise AssertionError("read-only facade must never reach the venue")

    def modify_order(
        self,
        broker_order_id: str,  # noqa: ARG002
        quantity: float | None,  # noqa: ARG002
        price: float | None,  # noqa: ARG002
    ) -> bool:
        raise AssertionError("read-only facade must never reach the venue")

    def stream_events(self) -> tuple[dict, ...]:
        return ()

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        """Part of the BrokerAdapter protocol (read-only never uses it)."""

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        return None


def test_funds_passthrough_returns_verified_broker_numbers() -> None:
    venue = ReadOnlyBroker(_Venue())
    funds = venue.funds()
    assert funds["available"] == 100_000.0
    assert funds["total"] == 100_000.0


def test_funds_passthrough_is_a_pure_read() -> None:
    """No invented capital: a venue that raises must keep raising."""
    ro = ReadOnlyBroker(_Venue(funds={"available": 0.0, "used": 0.0, "total": 0.0}))
    assert ro.funds()["available"] == 0.0, "a real zero must survive, never be faked up"


def test_funds_missing_from_venue_fails_closed() -> None:
    class _Bare(_Venue):
        def funds(self) -> dict:  # type: ignore[override]
            raise AttributeError("no funds method")

    ro = ReadOnlyBroker(_Bare())
    with pytest.raises(BrokerError) as exc:
        ro.funds()
    assert exc.value.code == "NO_FUNDS"


def test_mutations_still_blocked_with_funds_available() -> None:
    ro = ReadOnlyBroker(_Venue())
    plan = OrderPlan(
        intent_id="i1",
        symbol="NSE:AAA",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        limit_price=None,
        stop_price=None,
        time_in_force="DAY",
        bracket=(),
    )
    with pytest.raises(BrokerError) as exc:
        ro.place_order(plan, "cid-1")
    assert exc.value.code == "READ_ONLY"
    with pytest.raises(BrokerError) as exc:
        ro.cancel_order("VENUE-1")
    assert exc.value.code == "READ_ONLY"


def test_reads_still_pass_through() -> None:
    ro = ReadOnlyBroker(_Venue())
    assert ro.health() == (True, "ok")
    assert ro.account()["account_id"] == "ACC1"
    assert ro.positions() == []
    assert ro.open_orders() == []
