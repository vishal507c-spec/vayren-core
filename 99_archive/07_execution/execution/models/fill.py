from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    timestamp: str
    commission: float = 0.0
    exchange: str = ""

    @property
    def notional(self) -> float:
        return self.price * self.quantity

    @property
    def net_value(self) -> float:
        return self.notional - self.commission
