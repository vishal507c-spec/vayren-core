from dataclasses import dataclass
from execution.models.order import Order
from execution.models.fill import Fill


@dataclass(frozen=True)
class OrderFilled:
    order: Order | None = None
    fill: Fill | None = None
