"""OBR SELL v1.0 — single-strategy focus for optimization workflow."""

from collections import deque
from dataclasses import dataclass

from market.models.bar import Bar

from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.models.signal import Signal, SignalKind
from strategy.runtime import BarView, StrategyLogic

PARAM_SPECS: tuple[ParameterSpec, ...] = (
    ParameterSpec(key="rsi_threshold", label="RSI", default=65, minimum=50, maximum=80, decimals=0),
)

KIND = "obr_sell"


def _rsi(closes: deque[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    vals = list(closes)[-period - 1 :]
    gains = 0.0
    losses = 0.0
    for i in range(1, len(vals)):
        diff = vals[i] - vals[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += -diff
    if losses == 0:
        return 100.0
    rs = gains / losses if losses != 0 else 0
    return 100 - (100 / (1 + rs))


@dataclass
class _Channel:
    window: int = 20
    highs: deque[float] = None  # type: ignore[assignment]
    lows: deque[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.highs is None:
            self.highs = deque(maxlen=self.window)
        if self.lows is None:
            self.lows = deque(maxlen=self.window)

    def push(self, high: float, low: float) -> tuple[float, float] | None:
        self.highs.append(high)
        self.lows.append(low)
        if len(self.highs) < self.window:
            return None
        return (max(self.highs), min(self.lows))


class ObrSellLogic:
    """OBR SELL logic — short rejection, optimizable via RSI.

    Emits SELL (short entry) when flat, BUY (cover) when short.
    """

    def __init__(self, params: StrategyParameters) -> None:
        v = params.validated(PARAM_SPECS)
        self._rsi_thr = float(v["rsi_threshold"])
        self._closes: deque[float] = deque(maxlen=30)
        self._vols: deque[int] = deque(maxlen=20)
        self._channel = _Channel(window=20)

    def warmup(self) -> int:
        return 20

    def on_bar(self, view: BarView) -> Signal | None:
        bar: Bar = view.bar
        self._closes.append(bar.close)
        self._vols.append(bar.volume)
        ch = self._channel.push(bar.high, bar.low)
        if ch is None:
            return None
        ch_high, ch_low = ch
        ch_range = ch_high - ch_low
        if ch_range <= 0:
            return None
        rsi = _rsi(self._closes, 14)
        if rsi is None:
            return None
        avg_vol = sum(self._vols) / len(self._vols) if self._vols else 0
        vol_ok = bar.volume >= avg_vol if avg_vol > 0 else True
        distance_to_top = ch_high - bar.close
        threshold = ch_range * 0.15
        near_top = distance_to_top <= threshold
        mom = self._closes[-1] - self._closes[-4] if len(self._closes) >= 4 else 0
        # Short entry
        if view.state.flat and near_top and rsi >= self._rsi_thr and vol_ok and mom > 0:
            bar_range = bar.high - bar.low
            sl = bar.close + bar_range if bar_range > 0 else None
            tp = bar.close - 2 * bar_range if bar_range > 0 else None
            return Signal(
                index=view.index,
                timestamp=bar.timestamp,
                kind=SignalKind.SELL,
                price=bar.close,
                stop_loss=sl,
                take_profit=tp,
            )
        # Cover short
        if not view.state.flat and view.state.side == "SHORT":
            mid = (ch_high + ch_low) / 2
            if bar.close < mid and rsi < 50:
                return Signal(
                    index=view.index, timestamp=bar.timestamp, kind=SignalKind.BUY, price=bar.close
                )
        return None


def obr_sell_factory(params: StrategyParameters) -> StrategyLogic:
    return ObrSellLogic(params)
