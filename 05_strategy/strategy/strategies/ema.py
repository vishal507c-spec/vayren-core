"""EMA Crossover — native Python (built-in)."""

from __future__ import annotations

from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.runtime import BarView
from strategy.strategies.base import PythonStrategy


def calc_ema(previous: float | None, close: float, period: int) -> float:
    """One EMA step (pure arithmetic helper for the strategy below)."""
    alpha = 2.0 / (float(period) + 1.0)
    if previous is None:
        return float(close)
    return alpha * float(close) + (1.0 - alpha) * float(previous)


class EmaCrossover(PythonStrategy):
    """EMA crossover — buys when fast crosses above slow, sells on cross under."""

    @staticmethod
    def param_specs() -> tuple[ParameterSpec, ...]:
        return (
            ParameterSpec(
                key="fast_period",
                label="Fast period",
                default=12,
                minimum=2,
                maximum=50,
                decimals=0,
            ),
            ParameterSpec(
                key="slow_period",
                label="Slow period",
                default=26,
                minimum=5,
                maximum=100,
                decimals=0,
            ),
            ParameterSpec(
                key="min_volume",
                label="Min volume",
                default=0,
                minimum=0,
                maximum=1000000000,
                decimals=0,
            ),
        )

    def __init__(self, params: StrategyParameters | dict[str, float] | None = None) -> None:
        super().__init__(params)
        self._fast: float | None = None
        self._slow: float | None = None
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def on_bar_logic(self, view: BarView) -> None:
        fast_period = int(self.params.get("fast_period", 12))
        slow_period = int(self.params.get("slow_period", 26))
        min_volume = float(self.params.get("min_volume", 0))
        if min_volume > 0 and view.bar.volume < min_volume:
            return None
        self._fast = calc_ema(self._fast, view.bar.close, fast_period)
        self._slow = calc_ema(self._slow, view.bar.close, slow_period)
        prev_fast, prev_slow = self._prev_fast, self._prev_slow
        self._prev_fast, self._prev_slow = self._fast, self._slow
        if prev_fast is None or prev_slow is None:
            return None
        if self._fast > self._slow and prev_fast <= prev_slow:
            self.buy()
        elif self._fast < self._slow and prev_fast >= prev_slow:
            self.sell()
        return None
