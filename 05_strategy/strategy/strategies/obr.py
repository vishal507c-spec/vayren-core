"""OBR strategies — native Python."""

from __future__ import annotations

from market import Bar

from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.runtime import BarView, StrategyLogic
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


class Obr(PythonStrategy):
    """OBR — breakout strategy."""

    @staticmethod
    def param_specs() -> tuple[ParameterSpec, ...]:
        return (
            ParameterSpec(
                key="ref_index",
                label="Reference Candle Index",
                default=3,
                minimum=1,
                maximum=10,
                decimals=0,
            ),
            ParameterSpec(
                key="exit_hour", label="Exit Hour", default=15, minimum=0, maximum=23, decimals=0
            ),
            ParameterSpec(
                key="exit_min", label="Exit Minute", default=15, minimum=0, maximum=59, decimals=0
            ),
        )

    def on_bar_logic(self, view: BarView) -> None:
        bar: Bar = view.bar
        ref_range = calc_range(self.highs, self.lows, 20)
        rsi_val = calc_rsi(self.closes, 14)
        is_up_break = bar.close > bar.high - ref_range * 0.10
        is_down_break = bar.close < bar.low + ref_range * 0.10
        if is_up_break and rsi_val > 55:
            self.buy()
            self.stop_loss(bar.low)
            self.take_profit(bar.close + ref_range)
            return None
        if is_down_break and rsi_val < 45:
            self.sell()
            self.stop_loss(bar.high)
            self.take_profit(bar.close - ref_range)
            return None
        self.time_exit("15:15")
        return None


def create_obr_sell_v10(params: StrategyParameters) -> StrategyLogic:
    s = ObrSellV10(params)
    # map label keys to internal keys if needed
    # params may be keyed by label; normalize
    norm: dict[str, float] = {}
    for k, v in dict(params).items():
        if k == "C1 Range":
            norm["c1_thresh"] = float(v)
        elif k == "C4 Range":
            norm["c4_thresh"] = float(v)
        elif k == "RSI Threshold":
            norm["rsi_thr"] = float(v)
        else:
            norm[k] = float(v)
    s.params = norm
    return s


def create_obr(params: StrategyParameters) -> StrategyLogic:
    s = Obr(params)
    norm: dict[str, float] = {}
    for k, v in dict(params).items():
        if k == "Reference Candle Index":
            norm["ref_index"] = float(v)
        elif k == "Exit Hour":
            norm["exit_hour"] = float(v)
        elif k == "Exit Minute":
            norm["exit_min"] = float(v)
        else:
            norm[k] = float(v)
    s.params = norm
    return s
