"""Order Execution Domain.

Manages order lifecycle, broker connectivity, and fill reconciliation.
Supports multiple brokers and simulated execution for backtesting.

Public API:
    models: Order, Fill, Position
    services: OrderManager
    events: OrderSubmitted, OrderFilled, OrderRejected
"""

from execution.models.order import Order
from execution.models.fill import Fill
from execution.models.position import Position
from execution.services.order_manager import OrderManager
from execution.events.order_submitted import OrderSubmitted
from execution.events.order_filled import OrderFilled
from execution.events.order_rejected import OrderRejected

__all__ = [
    "Order",
    "Fill",
    "Position",
    "OrderManager",
    "OrderSubmitted",
    "OrderFilled",
    "OrderRejected",
]
