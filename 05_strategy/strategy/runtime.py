"""StrategyRuntime — executes registered strategy logic bar by bar.

The runtime owns no trading rules itself: it walks a window of bars, hands
each bar to a logic instance created from a registered factory, and collects
the signals. Position feedback flows through :class:`StrategyState` so a
logic can see its own open position without sharing mutable state.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from market import Bar

from strategy.models.parameters import StrategyParameters
from strategy.models.signal import Signal
from strategy.models.state import StrategyState


@dataclass(frozen=True)
class BarView:
    """Read-only per-bar context handed to strategy logic.

    Attributes:
        bars: The full replayed window (ascending by timestamp).
        index: Current bar index inside `bars`.
        params: Validated strategy parameters.
        state: The strategy's current position state.
    """

    bars: tuple[Bar, ...]
    index: int
    params: StrategyParameters
    state: StrategyState

    @property
    def bar(self) -> Bar:
        """The current bar."""
        return self.bars[self.index]


class StrategyLogic(Protocol):
    """Protocol every strategy implementation fulfils.

    A logic is created per run via its factory, keeps its own internal
    calculation state (indicators and so on) and stays single-threaded.
    """

    def warmup(self) -> int:
        """Number of leading bars the logic needs before it can decide."""
        ...

    def on_bar(self, view: BarView) -> Signal | None:
        """Decide for the current bar; return a Signal or None."""
        ...


class StrategyRuntime:
    """Drives one strategy logic over a bar window and collects signals.

    Pure computation: no bus, no SQL, no UI. The caller (backtest runner)
    owns execution and position management; the runtime only produces the
    signal stream in bar order.
    """

    def __init__(self, logic: StrategyLogic, params: StrategyParameters) -> None:
        self._logic = logic
        self._params = params

    def run(
        self,
        bars: tuple[Bar, ...],
        on_signal: Callable[[Signal, StrategyState], StrategyState] | None = None,
    ) -> tuple[Signal, ...]:
        """Generate all signals for `bars` in ascending order.

        `on_signal(signal, state)` is called after each emitted signal with
        the (possibly updated) position state, so callers can drive their
        simulator inline. Returns the full signal tuple.
        """
        if not bars:
            return ()
        warmup = max(0, self._logic.warmup())
        signals: list[Signal] = []
        state = StrategyState()
        for index in range(warmup, len(bars)):
            view = BarView(bars=bars, index=index, params=self._params, state=state)
            signal = self._logic.on_bar(view)
            if signal is None:
                continue
            signals.append(signal)
            if on_signal is not None:
                state = on_signal(signal, state)
        return tuple(signals)
