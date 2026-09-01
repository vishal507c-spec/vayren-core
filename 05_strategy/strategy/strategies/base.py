"""Base class for native Python strategies."""

from __future__ import annotations

from collections import deque
from typing import Any

from market.models.bar import Bar

from strategy.models.parameters import StrategyParameters
from strategy.models.signal import Signal, SignalKind
from strategy.runtime import BarView, StrategyLogic


class PythonStrategy(StrategyLogic):
    """Base for all native Python strategies — maintains indicator history."""

    def __init__(self, params: StrategyParameters | dict[str, float] | None = None) -> None:
        self.params: dict[str, float] = {}
        if params is not None:
            try:
                self.params = {k: float(v) for k, v in dict(params).items()}
            except Exception:
                self.params = {}
        self.closes: deque[float] = deque(maxlen=100)
        self.highs: deque[float] = deque(maxlen=100)
        self.lows: deque[float] = deque(maxlen=100)
        self.volumes: deque[int] = deque(maxlen=100)
        self._pending_kind: SignalKind | None = None
        self._pending_sl: float | None = None
        self._pending_tp: float | None = None
        self._pending_time_exit: str | None = None
        self._current_day: str | None = None
        self._prev_day_close: float = 0.0
        self._is_new_day: bool = False

    def warmup(self) -> int:
        return 20

    def on_bar(self, view: BarView) -> Signal | None:
        bar = view.bar
        # Update day tracking
        try:
            cur_day = str(bar.timestamp)[:10]
        except Exception:
            cur_day = ""
        is_new = self._current_day is None or cur_day != self._current_day
        if is_new and self._current_day is not None and self.closes:
            try:
                self._prev_day_close = float(self.closes[-1])
            except Exception:
                self._prev_day_close = 0.0
        elif self._current_day is None:
            self._prev_day_close = 0.0
        self._is_new_day = bool(is_new)
        if is_new:
            self._current_day = cur_day
        self.closes.append(bar.close)
        self.highs.append(bar.high)
        self.lows.append(bar.low)
        self.volumes.append(bar.volume)
        self._pending_kind = None
        self._pending_sl = None
        self._pending_tp = None
        self._pending_time_exit = None

        signal = self.on_bar_logic(view)

        # Handle time_exit if set
        if self._pending_time_exit:
            try:
                hhmm = bar.timestamp[11:16]
                if hhmm >= self._pending_time_exit and not view.state.flat:
                    self._pending_kind = (
                        SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL
                    )
            except Exception:
                pass

        if self._pending_kind is None:
            return signal

        # If logic already returned a signal, prefer pending from logic; else use time_exit signal
        if signal is not None:
            return signal
        return Signal(
            index=view.index,
            timestamp=bar.timestamp,
            kind=self._pending_kind,
            price=bar.close,
            stop_loss=self._pending_sl,
            take_profit=self._pending_tp,
        )

    def on_bar_logic(self, view: BarView) -> Signal | None:
        raise NotImplementedError

    # Helpers for strategies
    def buy(self) -> None:
        self._pending_kind = SignalKind.BUY

    def sell(self) -> None:
        self._pending_kind = SignalKind.SELL

    def close_position(self, view: BarView) -> None:
        if not view.state.flat:
            self._pending_kind = SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL

    def stop_loss(self, price: float) -> None:
        self._pending_sl = float(price)

    def take_profit(self, price: float) -> None:
        self._pending_tp = float(price)

    def time_exit(self, time_str: str) -> None:
        self._pending_time_exit = str(time_str)

    def is_new_day(self) -> bool:
        return self._is_new_day

    def prev_day_close(self) -> float:
        return float(self._prev_day_close)

    def after_time(self, time_str: str, view: BarView) -> bool:
        try:
            return str(view.bar.timestamp)[11:16] >= str(time_str)
        except Exception:
            return False

    def plot(self, value: float, title: str) -> None:
        # No-op for Python-native — chart series kept for compatibility, not required for signals
        try:
            if not hasattr(self, "_plot_series"):
                self._plot_series: dict[str, list] = {}  # type: ignore[attr-defined]
            self._plot_series.setdefault(title, []).append(float(value))  # type: ignore[attr-defined]
        except Exception:
            pass
