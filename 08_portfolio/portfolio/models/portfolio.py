from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Portfolio:
    name: str = "default"
    total_capital: float = 100000.0
    cash: float = 100000.0
    allocated_capital: float = 0.0
    strategies: list[str] = field(default_factory=list)
    total_pnl: float = 0.0

    @property
    def equity(self) -> float:
        return self.cash + self.allocated_capital

    @property
    def allocation_pct(self) -> float:
        if self.total_capital == 0:
            return 0.0
        return (self.allocated_capital / self.total_capital) * 100

    @property
    def return_pct(self) -> float:
        if self.total_capital == 0:
            return 0.0
        return (self.total_pnl / self.total_capital) * 100

    def add_capital(self, amount: float) -> None:
        self.total_capital += amount
        self.cash += amount
