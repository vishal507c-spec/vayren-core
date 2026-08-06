from dataclasses import dataclass
from execution.models.order import Order


@dataclass(frozen=True)
class OrderRejected:
    order: Order | None = None
    reason: str = ""
