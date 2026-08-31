"""SMA Crossover — native Python."""

from __future__ import annotations

from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.runtime import BarView, StrategyLogic
from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_sma


class SmaCrossover(PythonStrategy):
    @staticmethod
    def param_specs() -> tuple[ParameterSpec, ...]:
        return (
            ParameterSpec(key="fast_period", label="Fast period", default=10, minimum=2, maximum=50, decimals=0),
            ParameterSpec(key="slow_period", label="Slow period", default=30, minimum=5, maximum=100, decimals=0),
        )

    def __init__(self, params: StrategyParameters | dict[str, float] | None = None) -> None:
        super().__init__(params)
        self.prev_fast: float | None = None
        self.prev_slow: float | None = None

    def on_bar_logic(self, view: BarView) -> None:
        fast_period = int(self.params.get("fast_period", 10))
        slow_period = int(self.params.get("slow_period", 30))
        fast = calc_sma(self.closes, fast_period)
        slow = calc_sma(self.closes, slow_period)
        # First bar: init prev
        if self.prev_fast is None:
            self.prev_fast = fast
            self.prev_slow = slow
            return None
        if fast > slow and self.prev_fast is not None and self.prev_fast <= self.prev_slow:
            self.buy()
        elif fast < slow and self.prev_fast is not None and self.prev_fast >= self.prev_slow:
            self.sell()
        self.prev_fast = fast
        self.prev_slow = slow
        return None


def create_sma_crossover(params: StrategyParameters) -> StrategyLogic:
    s = SmaCrossover(params)
    norm: dict[str, float] = {}
    for k, v in dict(params).items():
        if k == "Fast period":
            norm["fast_period"] = float(v)
        elif k == "Slow period":
            norm["slow_period"] = float(v)
        else:
            norm[k] = float(v)
    s.params = norm
    # reset prev to None for fresh run (factory creates new instance each run, so fine)
    return s
