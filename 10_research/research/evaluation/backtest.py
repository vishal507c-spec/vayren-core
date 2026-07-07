from typing import Any

from analytics.models.metrics import PerformanceMetrics
from analytics.services.performance import PerformanceAnalyzer


class BacktestEvaluator:
    def __init__(self) -> None:
        self._performance = PerformanceAnalyzer()

    def evaluate(self, equity_curve: list[float]) -> PerformanceMetrics:
        returns: list[float] = []
        for i in range(1, len(equity_curve)):
            if equity_curve[i - 1] != 0:
                returns.append((equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])
        return self._performance.calculate(returns)
