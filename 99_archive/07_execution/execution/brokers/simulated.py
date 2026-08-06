from datetime import datetime
from typing import Any

from execution.models.order import Order
from execution.models.fill import Fill
from execution.events.order_submitted import OrderSubmitted
from execution.events.order_filled import OrderFilled


class SimulatedBroker:
    """Simulated broker for backtesting and paper trading."""

    def __init__(self, slippage: float = 0.0, fill_probability: float = 1.0) -> None:
        self._slippage = slippage
        self._fill_probability = fill_probability

    def submit_order(self, order: Order) -> OrderSubmitted:
        order.status = "submitted"
        return OrderSubmitted(order=order)

    def simulate_fill(self, order: Order, current_price: float) -> OrderFilled:
        import random
        if random.random() > self._fill_probability:
            order.status = "rejected"
            from execution.events.order_rejected import OrderRejected
            return OrderFilled(order=order)

        fill_price = current_price * (1 + self._slippage) if order.is_buy else current_price * (1 - self._slippage)
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            timestamp=datetime.utcnow().isoformat(),
            exchange="simulated",
        )
        return OrderFilled(order=order, fill=fill)

    def get_positions(self) -> list[Any]:
        return []
