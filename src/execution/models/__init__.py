"""Execution models — intents, orders, positions, contracts.

`OrderState` is the Rust-owned lifecycle vocabulary (bridged through
`execution.models.order_state`, the `execution.native_order_state` kernel);
the intent/order/position/contract models are the restored legacy slice and
are tracked debt (see `docs/language_retention.json`).
"""

from execution.models.contract import StrategyRuntimeContract
from execution.models.intent import ExecutionIntent, StrategySignal, make_intent_id
from execution.models.order import (
    TERMINAL_STATES,
    TRANSITIONS,
    BrokerOrder,
    Fill,
    OrderPlan,
)
from execution.models.order_state import OrderState
from execution.models.position import AccountSnapshot, Position

__all__ = [
    "StrategySignal",
    "ExecutionIntent",
    "make_intent_id",
    "OrderState",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "OrderPlan",
    "BrokerOrder",
    "Fill",
    "Position",
    "AccountSnapshot",
    "StrategyRuntimeContract",
]
