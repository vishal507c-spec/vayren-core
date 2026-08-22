"""SmaCrossover — a real, fully defined moving-average crossover strategy.

Long/flat trend-following: enter long when the fast SMA crosses above the
slow SMA, exit when it crosses back below. Deterministic, parameterised and
completely implemented — no mock or sample behaviour. Serves as the first
production strategy kind; further kinds register through the same factory
API without touching this file.
"""

from dataclasses import dataclass

from market.models.bar import Bar

from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.models.signal import Signal, SignalKind
from strategy.runtime import BarView, StrategyLogic

PARAM_SPECS: tuple[ParameterSpec, ...] = (
    ParameterSpec(
        key="fast_period",
        label="Fast period",
        default=10,
        minimum=2,
        maximum=200,
        decimals=0,
    ),
    ParameterSpec(
        key="slow_period",
        label="Slow period",
        default=30,
        minimum=3,
        maximum=400,
        decimals=0,
    ),
)

KIND = "sma_crossover"


@dataclass
class _RollingMean:
    """Incremental rolling mean over a fixed window."""

    window: int
    total: float = 0.0
    values: list[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.values is None:
            self.values = []

    def push(self, value: float) -> float:
        """Add one value and return the current mean (needs `window` points)."""
        self.values.append(value)
        self.total += value
        if len(self.values) > self.window:
            self.total -= self.values.pop(0)
        return self.total / len(self.values)


class SmaCrossoverLogic:
    """Crossover logic: rolling fast/slow means with cross detection.

    Emits BUY when the fast mean crosses above the slow mean while flat,
    and SELL when it crosses below while long. One signal maximum per bar.
    """

    def __init__(self, params: StrategyParameters) -> None:
        validated = params.validated(PARAM_SPECS)
        self._fast_period = int(validated["fast_period"])
        self._slow_period = int(validated["slow_period"])
        if self._fast_period >= self._slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        self._fast = _RollingMean(self._fast_period)
        self._slow = _RollingMean(self._slow_period)
        self._fast_ready = False
        self._slow_ready = False
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def warmup(self) -> int:
        """Bars needed before both means are defined."""
        return self._slow_period

    def on_bar(self, view: BarView) -> Signal | None:
        bar: Bar = view.bar
        fast = self._fast.push(bar.close)
        slow = self._slow.push(bar.close)
        self._fast_ready = self._fast_ready or len(self._fast.values) >= self._fast_period
        self._slow_ready = self._slow_ready or len(self._slow.values) >= self._slow_period
        if not (self._fast_ready and self._slow_ready):
            return None
        previous_fast = self._prev_fast
        previous_slow = self._prev_slow
        self._prev_fast = fast
        self._prev_slow = slow
        if previous_fast is None or previous_slow is None:
            return None
        crossed_up = previous_fast <= previous_slow < fast
        crossed_down = previous_fast >= previous_slow > fast
        if crossed_up and view.state.flat:
            return Signal(
                index=view.index,
                timestamp=bar.timestamp,
                kind=SignalKind.BUY,
                price=bar.close,
            )
        if crossed_down and not view.state.flat and view.state.side == "LONG":
            return Signal(
                index=view.index,
                timestamp=bar.timestamp,
                kind=SignalKind.SELL,
                price=bar.close,
            )
        return None


def sma_crossover_factory(params: StrategyParameters) -> StrategyLogic:
    """Factory registered in the strategy registry under :data:`KIND`."""
    return SmaCrossoverLogic(params)
