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
from datetime import datetime

from execution.events import CandleEvent
from execution.market_data.native_normalizer import watermark
from execution.models.contract import StrategyRuntimeContract
from execution.models.intent import StrategySignal
from execution.runtime.lifecycle import StrategyLifecycle
from market import Bar
from strategy import BarView, StrategyLogic, StrategyParameters, StrategyState


def parse_iso_epoch(ts: str) -> float:
    """Extract epoch timestamp from ISO or wall-clock string."""
    try:
        dt = datetime.fromisoformat(str(ts).strip())
        return dt.timestamp()
    except Exception:
        return 0.0


def normalize_stamp(ts: str) -> str:
    """Normalize timestamp string for stable identity matching."""
    try:
        dt = datetime.fromisoformat(str(ts).strip())
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts).strip()


def is_older_or_equal(ts1: str, ts2: str) -> bool:
    """Return True if ts1 <= ts2 chronologically."""
    if ts1 == ts2:
        return True
    try:
        dt1 = datetime.fromisoformat(str(ts1).strip())
        dt2 = datetime.fromisoformat(str(ts2).strip())
        if dt1.tzinfo is None and dt2.tzinfo is not None:
            dt1 = dt1.replace(tzinfo=dt2.tzinfo)
        elif dt2.tzinfo is None and dt1.tzinfo is not None:
            dt2 = dt2.replace(tzinfo=dt1.tzinfo)
        return dt1 <= dt2
    except Exception:
        return str(ts1) <= str(ts2)


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
        """Feed history through the logic, discarding signals. Returns fed count.

        Deduplicates against existing bars and enforces strict chronological order.
        """
        fed = 0
        seen_stamps = {normalize_stamp(b.timestamp) for b in self._ctx.bars}
        last_ts = self._ctx.bars[-1].timestamp if self._ctx.bars else None

        for bar in bars:
            norm = normalize_stamp(bar.timestamp)
            if norm in seen_stamps:
                continue
            if last_ts is not None and is_older_or_equal(bar.timestamp, last_ts):
                continue
            self._ctx.bars.append(bar)
            seen_stamps.add(norm)
            last_ts = bar.timestamp
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
        Rejects duplicate or out-of-order candles safely.
        """
        ctx = self._ctx
        ctx.event_seq = watermark(ctx.event_seq, event.seq)
        if not event.is_closed:
            return None

        norm_event = normalize_stamp(event.timestamp)
        if any(normalize_stamp(b.timestamp) == norm_event for b in ctx.bars):
            return None
        last_ts = ctx.bars[-1].timestamp if ctx.bars else None
        if last_ts is not None and is_older_or_equal(event.timestamp, last_ts):
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
        """Recovery path: re-feed persisted bars through warmup.

        Cleans, sorts chronologically, and deduplicates bars before restoring.
        Idempotent: calling multiple times leaves the exact same window.
        """
        self._ctx.bars.clear()
        sorted_bars = sorted(bars, key=lambda b: (parse_iso_epoch(b.timestamp), str(b.timestamp)))
        seen: set[str] = set()
        clean_bars: list[Bar] = []
        for b in sorted_bars:
            norm = normalize_stamp(b.timestamp)
            if norm in seen:
                continue
            seen.add(norm)
            clean_bars.append(b)
        return self.warmup(tuple(clean_bars))
