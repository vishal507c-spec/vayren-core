"""SMA Crossover — native Python."""

from __future__ import annotations

from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.runtime import BarView
from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_sma


class SmaCrossover(PythonStrategy):
    @staticmethod
    def param_specs() -> tuple[ParameterSpec, ...]:
        return (
            ParameterSpec(
                key="fast_period",
                label="Fast period",
                default=10,
                minimum=2,
                maximum=50,
                decimals=0,
            ),
            ParameterSpec(
                key="slow_period",
                label="Slow period",
                default=30,
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
        self.prev_fast: float | None = None
        self.prev_slow: float | None = None

    def on_bar_logic(self, view: BarView) -> None:
        fast_period = int(self.params.get("fast_period", 10))
        slow_period = int(self.params.get("slow_period", 30))
        min_volume = float(self.params.get("min_volume", 0))
        if min_volume > 0 and view.bar.volume < min_volume:
            # Skip thin bars: history (base) still records them, prev state untouched.
            return None
        fast = calc_sma(self.closes, fast_period)
        slow = calc_sma(self.closes, slow_period)
        prev_fast = self.prev_fast
        prev_slow = self.prev_slow
        # First bar: init prev (both are always set together)
        if prev_fast is None or prev_slow is None:
            self.prev_fast = fast
            self.prev_slow = slow
            return None
        if fast > slow and prev_fast <= prev_slow:
            self.buy()
        elif fast < slow and prev_fast >= prev_slow:
            self.sell()
        self.prev_fast = fast
        self.prev_slow = slow
        return None
