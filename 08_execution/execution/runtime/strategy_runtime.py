"""Live strategy driver — reuses backtest-compatible strategy logic.

Parity by construction: evaluation calls ``logic.on_bar(BarView(...))``,
exactly like :class:`StrategyRuntime`. Only the environment differs (live
event stream instead of a historical window). Warmup feeds history through
the logic and DISCARDS signals — pre-live bars must never trade.
"""

from __future__ import annotations

from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field

from market import Bar
from strategy import BarView, StrategyLogic, StrategyParameters, StrategyState

from execution.events import CandleEvent
from execution.market_data.native_normalizer import watermark
from execution.models.contract import StrategyRuntimeContract
from execution.models.intent import StrategySignal
from execution.runtime.lifecycle import StrategyLifecycle


@dataclass
class StrategyContext:
    """Isolated per-instance state. Never shared between strategies."""

    strategy_id: str
    strategy_version: str
    logic: StrategyLogic
    params: StrategyParameters
    contract: StrategyRuntimeContract
    lifecycle: StrategyLifecycle = field(default_factory=StrategyLifecycle)
    bars: deque[Bar] = field(default_factory=lambda: deque(maxlen=512))
    state: StrategyState = field(default_factory=StrategyState)
    event_seq: int = 0
    intent_seq: int = 0
    signal_seq: int = 0


def candle_to_bar(event: CandleEvent) -> Bar:
    """Normalized candle → market Bar (the strategy's native input)."""
    return Bar(
        symbol=event.symbol,
        open=event.open,
        high=event.high,
        low=event.low,
        close=event.close,
        volume=event.volume,
        timestamp=event.timestamp,
        bar_size=event.timeframe or "live",
        source=event.source or "live",
    )


class LiveStrategyDriver:
    """Feeds one strategy context from candle events. Deterministic, sync."""

    def __init__(self, context: StrategyContext) -> None:
        self._ctx = context

    @property
    def context(self) -> StrategyContext:
        return self._ctx

    def warmup(self, bars: tuple[Bar, ...]) -> int:
        """Feed history through the logic, discarding signals. Returns fed count."""
        fed = 0
        for bar in bars:
            self._ctx.bars.append(bar)
            view = BarView(
                bars=tuple(self._ctx.bars),
                index=len(self._ctx.bars) - 1,
                params=self._ctx.params,
                state=self._ctx.state,
            )
            with suppress(Exception):
                self._ctx.logic.on_bar(view)
            fed += 1
        return fed

    def on_candle(self, event: CandleEvent) -> StrategySignal | None:
        """Evaluate one closed candle. Returns a signal or None.

        Non-closed candles update nothing (no lookahead on partial bars).
        """
        ctx = self._ctx
        ctx.event_seq = watermark(ctx.event_seq, event.seq)
        if not event.is_closed:
            return None
        bar = candle_to_bar(event)
        ctx.bars.append(bar)
        if not ctx.lifecycle.live:
            return None
        view = BarView(
            bars=tuple(ctx.bars),
            index=len(ctx.bars) - 1,
            params=ctx.params,
            state=ctx.state,
        )
        signal = ctx.logic.on_bar(view)
        if signal is None:
            return None
        ctx.signal_seq += 1
        return StrategySignal(
            signal_id=f"{ctx.strategy_id}:{ctx.strategy_version}:{ctx.signal_seq}",
            strategy_id=ctx.strategy_id,
            strategy_version=ctx.strategy_version,
            timestamp=event.timestamp,
            event_seq=event.seq,
            symbol=event.symbol,
            side=signal.kind.value,
            price=signal.price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            reason="strategy signal",
        )

    def export_window(self) -> tuple[Bar, ...]:
        """Persistable warmup window for restart recovery (re-warm, no pickling)."""
        return tuple(self._ctx.bars)

    def restore_window(self, bars: tuple[Bar, ...]) -> int:
        """Recovery path: re-feed persisted bars through warmup."""
        self._ctx.bars.clear()
        return self.warmup(bars)
