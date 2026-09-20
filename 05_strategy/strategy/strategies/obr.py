"""OBR strategies — native Python."""

from __future__ import annotations

from market import Bar

from strategy.models.parameters import ParameterSpec
from strategy.runtime import BarView
from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_range, calc_rsi


class ObrSellV10(PythonStrategy):
    """OBR SELL v1.0 — Python native."""

    @staticmethod
    def param_specs() -> tuple[ParameterSpec, ...]:
        return (
            ParameterSpec(
                key="c1_thresh",
                label="C1 Range",
                default=1.25,
                minimum=0.1,
                maximum=5.0,
                decimals=2,
            ),
            ParameterSpec(
                key="c4_thresh",
                label="C4 Range",
                default=0.56,
                minimum=0.1,
                maximum=5.0,
                decimals=2,
            ),
            ParameterSpec(
                key="rsi_thr", label="RSI Threshold", default=65, minimum=30, maximum=90, decimals=0
            ),
        )

    def on_bar_logic(self, view: BarView) -> None:
        bar: Bar = view.bar
        c1_thresh = float(self.params.get("c1_thresh", 1.25))
        rsi_thr = float(self.params.get("rsi_thr", 65))
        rsi = calc_rsi(self.closes, 14)
        ch_range = calc_range(self.highs, self.lows, 20)
        sell_condition = (
            bar.close >= (bar.high - ch_range * 0.15 * c1_thresh)
            and rsi >= rsi_thr
            and bar.volume >= 1000
        )
        if sell_condition:
            self.sell()
            self.stop_loss(bar.close + c1_thresh * (bar.high - bar.low))
            self.take_profit(bar.close - 2 * c1_thresh * (bar.high - bar.low))
            return None
        if bar.close < (bar.high + bar.low) / 2 and rsi < 50:
            self.close_position(view)
            return None
        self.time_exit("15:15")
        return None

    def warmup(self) -> int:
        return 20
