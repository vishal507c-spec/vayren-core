from logging import getLogger
from typing import Optional

from execution.models.order import Order
from execution.models.fill import Fill
from execution.models.position import Position
from execution.events.order_submitted import OrderSubmitted
from execution.events.order_filled import OrderFilled
from execution.events.order_rejected import OrderRejected

logger = getLogger(__name__)


class OrderManager:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}

    def submit(self, order: Order) -> OrderSubmitted:
        order.status = "submitted"
        self._orders[order.id] = order
        logger.info("Order submitted: %s %s %d %s", order.side, order.symbol, order.quantity, order.order_type)
        return OrderSubmitted(order=order)

    def fill(self, fill: Fill) -> OrderFilled:
        order = self._orders.get(fill.order_id)
        if order:
            order.filled_quantity += fill.quantity
            order.status = "filled" if order.filled_quantity >= order.quantity else "partially_filled"
        self._update_position(fill)
        logger.info("Order filled: %s %d @ %.2f", fill.symbol, fill.quantity, fill.price)
        return OrderFilled(order=order, fill=fill)

    def reject(self, order_id: str, reason: str) -> OrderRejected:
        order = self._orders.get(order_id)
        if order:
            order.status = "rejected"
        logger.warning("Order rejected: %s - %s", order_id, reason)
        return OrderRejected(order=order, reason=reason)

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[Position]:
        return list(self._positions.values())

    def _update_position(self, fill: Fill) -> None:
        if fill.symbol not in self._positions:
            self._positions[fill.symbol] = Position(symbol=fill.symbol)
        pos = self._positions[fill.symbol]
        prev_qty = pos.quantity
        direction = 1 if fill.side == "buy" else -1
        new_qty = prev_qty + (direction * fill.quantity)

        if prev_qty * new_qty < 0:
            pos.realized_pnl += abs(prev_qty) * (fill.price - pos.avg_cost) * (1 if prev_qty > 0 else -1)
            pos.avg_cost = fill.price
        elif prev_qty != 0:
            pos.avg_cost = ((prev_qty * pos.avg_cost) + (direction * fill.quantity * fill.price)) / new_qty
        else:
            pos.avg_cost = fill.price
        pos.quantity = new_qty
