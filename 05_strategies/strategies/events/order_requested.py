from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OrderRequested:
    """Emitted when a strategy requests an order."""

    symbol: str
    side: str
    quantity: int
    order_type: str = "market"
    price: Optional[float] = None
    strategy: str = ""
    reason: str = ""
