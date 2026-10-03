"""BrokerAdapter — transport boundary with capability discovery.

Strategy, risk, planner and engine code never touch this module's
implementations; they program against the protocol. Broker SDKs, sessions,
tokens and wire formats stay inside concrete adapters.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from execution.models.order import OrderPlan


class BrokerCapabilities:
    """Well-known capability ids (adapters advertise a subset)."""

    MARKET_ORDERS = "orders.market"
    LIMIT_ORDERS = "orders.limit"
    CANCEL = "orders.cancel"
    MODIFY = "orders.modify"
    POSITIONS = "account.positions"
    OPEN_ORDERS = "account.open_orders"
    STREAMING = "stream.events"


@runtime_checkable
class BrokerAdapter(Protocol):
    """The full surface a venue must provide for live execution.

    ``runtime_checkable`` (Phase 20 M3): the UBL delegation shim verifies a
    resolved trading face satisfies this protocol before connecting —
    fail-closed against mis-registered venues. Method-presence checking
    only (data attributes like ``name`` are not probed).
    """

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> tuple[str, ...]: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]: ...

    def account(self) -> dict[str, Any]: ...

    def positions(self) -> list[dict[str, Any]]: ...

    def open_orders(self) -> list[dict[str, Any]]: ...

    def place_order(self, plan: OrderPlan, client_order_id: str) -> str:
        """Submit; returns the broker order id. Raises BrokerError on transport failure."""
        ...

    def cancel_order(self, broker_order_id: str) -> bool: ...

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool: ...

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        """Drain broker-side events (fills, rejects, cancels); never blocks."""
        ...

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        """Advance pending venue state against a reference price.

        Default: no-op. Simulated venues override this to settle pending
        orders deterministically; live venues ignore it (fills arrive via
        their own transport and appear in :meth:`stream_events`).
        """

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        """Current spread in percent, or None when the venue has no book."""
        return None


class BrokerError(RuntimeError):
    """Transport or venue failure, normalized for the engine."""

    def __init__(self, message: str, code: str = "BROKER_ERROR") -> None:
        super().__init__(message)
        self.code = code


class NotConfiguredError(RuntimeError):
    """Raised when live/sandbox transport is requested without configuration.

    There is deliberately no live adapter implementation in this repository
    (LIVE_BROKER_INTEGRATION = NOT_CONFIGURED): inventing a broker
    integration would be fake trading infrastructure.
    """


def adapter_supports(adapter: Any, capability: str) -> bool:
    try:
        return capability in tuple(adapter.capabilities)
    except Exception:
        return False
