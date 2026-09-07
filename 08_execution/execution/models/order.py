"""Order, plan and fill models — the execution state machine vocabulary.

`OrderState` is the lifecycle vocabulary. The TRANSITION TABLE and terminal
set are owned by Rust (`rust/vayren-core`, `order_state` module) and
re-exported here from `execution.native_order_state` (a read-only projection
of the Rust authority — no independent Python table). Public import paths are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from execution.models.order_state import OrderState
from execution.native_order_state import TERMINAL_STATES, TRANSITIONS

__all__ = [
    "OrderState",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "OrderPlan",
    "BrokerOrder",
    "Fill",
]


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
