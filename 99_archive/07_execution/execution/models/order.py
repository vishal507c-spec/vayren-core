from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Order:
    symbol: str
    side: str
    quantity: int
    order_type: str = "market"
    price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "day"
    strategy: str = ""
    id: str = ""
    status: str = "pending"
    created_at: str = ""
    filled_quantity: int = 0
    avg_fill_price: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()

    @property
    def is_buy(self) -> bool:
        return self.side.lower() == "buy"

    @property
    def is_sell(self) -> bool:
        return self.side.lower() == "sell"

    @property
    def is_filled(self) -> bool:
        return self.status == "filled"

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity
