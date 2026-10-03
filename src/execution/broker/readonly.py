"""ReadOnlyBroker — safe read-only validation path over any adapter.

Wraps a real (or simulated) venue adapter: reads (health, account,
positions, open orders, capabilities, stream) pass through untouched;
every mutating call (place, cancel, modify) raises without touching the
venue. This is the FIRST integration test path for a real broker:
verify identity, state and market status with zero order risk.
"""

from __future__ import annotations

from typing import Any

from execution.broker.adapter import BrokerAdapter, BrokerError
from execution.models.order import OrderPlan


class ReadOnlyBroker:
    """Non-mutating facade. Mutations raise before reaching the venue."""

    def __init__(self, venue: BrokerAdapter) -> None:
        self._venue = venue

    @property
    def name(self) -> str:
        return f"{self._venue.name}+readonly"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self._venue.capabilities)

    @property
    def venue(self) -> BrokerAdapter:
        """The wrapped adapter (introspection only — never bypass through it)."""
        return self._venue

    def connect(self) -> None:
        self._venue.connect()

    def disconnect(self) -> None:
        self._venue.disconnect()

    def health(self) -> tuple[bool, str]:
        return self._venue.health()

    def account(self) -> dict[str, Any]:
        return self._venue.account()

    def positions(self) -> list[dict[str, Any]]:
        return self._venue.positions()

    def open_orders(self) -> list[dict[str, Any]]:
        return self._venue.open_orders()

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        return self._venue.stream_events()

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:  # noqa: ARG002
        """Deliberate no-op: read-only must never advance venue fills."""

    def reference_spread(self, symbol: str) -> float | None:
        return self._venue.reference_spread(symbol)

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:  # noqa: ARG002
        raise BrokerError("read-only connection: order submission blocked", code="READ_ONLY")

    def cancel_order(self, broker_order_id: str) -> bool:  # noqa: ARG002
        raise BrokerError("read-only connection: cancel blocked", code="READ_ONLY")

    def modify_order(
        self,
        broker_order_id: str,  # noqa: ARG002 - signature must match the protocol
        quantity: float | None,  # noqa: ARG002 - signature must match the protocol
        price: float | None,  # noqa: ARG002 - signature must match the protocol
    ) -> bool:
        raise BrokerError("read-only connection: modify blocked", code="READ_ONLY")
