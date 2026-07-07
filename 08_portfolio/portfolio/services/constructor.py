from portfolio.models.portfolio import Portfolio
from portfolio.models.allocation import Allocation


class PortfolioConstructor:
    def create(self, name: str = "default", capital: float = 100000.0, strategies: list[str] | None = None) -> Portfolio:
        return Portfolio(name=name, total_capital=capital, cash=capital, strategies=strategies or [])

    def allocate(self, portfolio: Portfolio, allocations: list[Allocation]) -> None:
        total_weight = sum(a.target_weight for a in allocations)
        if total_weight > 1.0:
            msg = f"Total allocation weight {total_weight} exceeds 1.0"
            raise ValueError(msg)
        total_amount = portfolio.cash
        for alloc in allocations:
            amount = total_amount * alloc.target_weight
            alloc.current_value = amount
            alloc.current_weight = alloc.target_weight
        portfolio.allocated_capital = total_amount * sum(a.target_weight for a in allocations)
        portfolio.cash = total_amount * (1 - sum(a.target_weight for a in allocations))
