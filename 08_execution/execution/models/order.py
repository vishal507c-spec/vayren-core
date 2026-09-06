"""Order, plan and fill models — the execution state machine vocabulary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OrderState(Enum):
    """Broker-independent order lifecycle (§12 mission spec)."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = frozenset(
    {
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    }
)

# Allowed transitions (UNKNOWN may resolve to any state only via reconcile).
TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.VALIDATED, OrderState.REJECTED}),
    OrderState.VALIDATED: frozenset(
        {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.EXPIRED}
    ),
    OrderState.SUBMITTED: frozenset(
        {OrderState.ACKNOWLEDGED, OrderState.REJECTED, OrderState.EXPIRED, OrderState.UNKNOWN}
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCEL_PENDING,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.CANCEL_PENDING: frozenset(
        {OrderState.CANCELLED, OrderState.FILLED, OrderState.UNKNOWN}
    ),
    OrderState.UNKNOWN: frozenset(),  # reconcile-only exits, handled explicitly
    OrderState.FILLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}


@dataclass(frozen=True)
class OrderPlan:
    """Deterministic planner output: one or more executable order specs."""

    intent_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"  # MARKET | LIMIT
    limit_price: float | None = None
    time_in_force: str = "DAY"
    bracket: tuple[dict[str, object], ...] = ()  # future: stop/TP legs as data


@dataclass(frozen=True)
class BrokerOrder:
    """An order as tracked by the execution engine (mutable lifecycle holder)."""

    client_order_id: str
    intent_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"
    limit_price: float | None = None
    state: OrderState = OrderState.CREATED
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    broker_order_id: str | None = None
    reason: str = ""
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (state, timestamp)

    def with_state(self, state: OrderState, timestamp: str, reason: str = "") -> BrokerOrder:
        """Return a copy advanced to `state` with the transition appended."""
        return BrokerOrder(
            client_order_id=self.client_order_id,
            intent_id=self.intent_id,
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            order_type=self.order_type,
            limit_price=self.limit_price,
            state=state,
            filled_qty=self.filled_qty,
            avg_fill_price=self.avg_fill_price,
            broker_order_id=self.broker_order_id,
            reason=reason or self.reason,
            history=self.history + ((state.value, timestamp),),
        )


@dataclass(frozen=True)
class Fill:
    """One fill report from the broker (or paper) adapter."""

    client_order_id: str
    broker_order_id: str | None
    symbol: str
    side: str
    fill_qty: float
    fill_price: float
    commission: float
    timestamp: str
    partial: bool = False
