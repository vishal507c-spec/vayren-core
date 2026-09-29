"""StrategyRuntime — executes registered strategy logic bar by bar.

The runtime owns no trading rules itself: it walks a window of bars, hands
each bar to a logic instance created from a registered factory, and collects
the signals. Position feedback flows through :class:`StrategyState` so a
logic can see its own open position without sharing mutable state.
"""

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol, overload

from market import Bar

from strategy.models.parameters import StrategyParameters
from strategy.models.signal import Signal
from strategy.models.state import StrategyState


@dataclass(frozen=True)
class BarView:
    """Read-only per-bar context handed to strategy logic.

    LOOKAHEAD BAN: strategy logic must only read the current bar
    (:attr:`bar`), the collapsed :attr:`window` (bars ``[0..index]``) or
    indexes ``<= index`` via ``view[i]``. Accessing any bar after
    ``index`` raises :class:`IndexError` — future bars are never visible
    to logic, so backtests cannot peek ahead.

    Attributes:
        bars: The full replayed window (ascending by timestamp). Kept for
            runner bookkeeping; logic must use :attr:`window` instead.
        index: Current bar index inside `bars`.
        params: Validated strategy parameters.
        state: The strategy's current position state.
    """

    bars: tuple[Bar, ...]
    index: int
    params: StrategyParameters
    state: StrategyState

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool):
            raise ValueError(f"BarView index must be an int, got {self.index!r}")
        if self.index < 0 or self.index >= len(self.bars):
            raise ValueError(f"BarView index {self.index} out of range for {len(self.bars)} bars")

    @property
    def bar(self) -> Bar:
        """The current bar."""
        return self.bars[self.index]

    @property
    def window(self) -> tuple[Bar, ...]:
        """Bars ``[0..index]`` — everything the logic may legally see."""
        return self.bars[: self.index + 1]

    @overload
    def __getitem__(self, item: int) -> Bar: ...

    @overload
    def __getitem__(self, item: slice) -> tuple[Bar, ...]: ...

    def __getitem__(self, item: int | slice) -> Bar | tuple[Bar, ...]:
        """Index into the visible window; lookahead raises :class:`IndexError`."""
        if isinstance(item, slice):
            return self.window[item]
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError(f"bar index must be an int, got {item!r}")
        resolved = item if item >= 0 else len(self.window) + item
        if resolved < 0 or resolved > self.index:
            raise IndexError(
                f"bar index {item} outside visible window [0..{self.index}] — "
                "lookahead is forbidden"
            )
        return self.bars[resolved]

    def __len__(self) -> int:
        return self.index + 1

    def __iter__(self) -> Iterator[Bar]:
        return iter(self.window)


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
        on_signal: Callable[[Signal, StrategyState], StrategyState | None] | None = None,
    ) -> tuple[Signal, ...]:
        """Generate all signals for `bars` in ascending order.

        The logic sees every bar from index 0 (warmup bars warm indicator
        state through ``on_bar``, mirroring the backtest runner) but signals
        from bars below ``warmup`` are discarded, never returned. A ``None``
        return from `on_signal(signal, state)` leaves the state unchanged.
        Every emitted signal is validated (index matches the current bar,
        price is finite) — violations raise :class:`ValueError` instead of
        silently entering the stream. Returns the full signal tuple.
        """
        if not bars:
            return ()
        warmup = max(0, self._logic.warmup())
        signals: list[Signal] = []
        state = StrategyState()
        for index in range(len(bars)):
            view = BarView(bars=bars, index=index, params=self._params, state=state)
            signal = self._logic.on_bar(view)
            if signal is None or index < warmup:
                continue
            if signal.index != view.index:
                raise ValueError(
                    f"signal index {signal.index} does not match current bar {view.index}"
                )
            if (
                not isinstance(signal.price, (int, float))
                or isinstance(signal.price, bool)
                or not math.isfinite(float(signal.price))
            ):
                raise ValueError(f"signal price must be finite, got {signal.price!r}")
            signals.append(signal)
            if on_signal is not None:
                updated = on_signal(signal, state)
                if updated is not None:
                    state = updated
        return tuple(signals)
