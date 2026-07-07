from typing import Sequence

from portfolio.models.allocation import Allocation


class PortfolioOptimizer:
    def equal_weight(self, symbols: Sequence[str]) -> list[Allocation]:
        weight = 1.0 / len(symbols) if symbols else 0.0
        return [Allocation(symbol=s, target_weight=weight) for s in symbols]

    def risk_parity(self, symbols: Sequence[str], volatilities: dict[str, float]) -> list[Allocation]:
        if not symbols:
            return []
        total_inv_vol = sum(1.0 / volatilities.get(s, 1.0) for s in symbols)
        return [Allocation(symbol=s, target_weight=(1.0 / volatilities.get(s, 1.0)) / total_inv_vol) for s in symbols]
