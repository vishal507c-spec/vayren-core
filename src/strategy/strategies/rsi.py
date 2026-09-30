"""RSI mean-reversion — native Python (built-in)."""

from __future__ import annotations

from strategy.models.parameters import ParameterSpec
from strategy.runtime import BarView
from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_rsi


class RsiStrategy(PythonStrategy):
    """RSI strategy — buys oversold, sells overbought (exits to flat)."""

    @staticmethod
    def param_specs() -> tuple[ParameterSpec, ...]:
        return (
            ParameterSpec(
                key="rsi_period",
                label="RSI period",
                default=14,
                minimum=2,
                maximum=50,
                decimals=0,
            ),
            ParameterSpec(
                key="oversold",
                label="Oversold level",
                default=30,
                minimum=5,
                maximum=45,
                decimals=0,
            ),
            ParameterSpec(
                key="overbought",
                label="Overbought level",
                default=70,
                minimum=55,
                maximum=95,
                decimals=0,
            ),
        )

    def on_bar_logic(self, view: BarView) -> None:
        period = int(self.params.get("rsi_period", 14))
        oversold = float(self.params.get("oversold", 30))
        overbought = float(self.params.get("overbought", 70))
        rsi = calc_rsi(self.closes, period)
        if rsi <= oversold and view.state.flat:
            self.buy()
        elif rsi >= overbought and not view.state.flat:
            self.close_position(view)
        return None
