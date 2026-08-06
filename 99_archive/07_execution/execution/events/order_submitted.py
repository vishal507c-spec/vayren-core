from dataclasses import dataclass
from execution.models.order import Order


@dataclass(frozen=True)
class OrderSubmitted:
    order: Order
