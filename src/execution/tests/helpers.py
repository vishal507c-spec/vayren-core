"""Shared fixtures for execution tests: synthetic bars and candles."""

from __future__ import annotations

from datetime import datetime, timedelta

from execution.events import CandleEvent
from execution.market_data.replay import bars_to_candles
from market import Bar


def make_bars(symbol: str = "TEST", count: int = 40, start: float = 100.0) -> tuple[Bar, ...]:
    """Deterministic ascending bars with a gentle up-drift and strictly monotonic timestamps."""
    bars = []
    base_dt = datetime(2026, 1, 1, 9, 15)
    for i in range(count):
        close = start + (i % 10) - 3.0 + i * 0.05
        bar_dt = base_dt + timedelta(minutes=15 * i)
        bars.append(
            Bar(
                symbol=symbol,
                open=close - 0.5,
                high=close + 0.5,
                low=close - 1.0,
                close=close,
                volume=1000 + i,
                timestamp=bar_dt.strftime("%Y-%m-%d %H:%M:%S"),
                bar_size="15m",
            )
        )
    return tuple(bars)


def make_candles(symbol: str = "TEST", count: int = 40) -> tuple[CandleEvent, ...]:
    return bars_to_candles(make_bars(symbol, count), "15m")
