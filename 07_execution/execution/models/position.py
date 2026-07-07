from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    strategy: str = ""

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_cost

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def return_pct(self) -> float:
        if self.cost_basis == 0:
            return 0.0
        return (self.total_pnl / self.cost_basis) * 100

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0
