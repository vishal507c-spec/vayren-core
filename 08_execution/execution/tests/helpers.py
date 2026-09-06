"""Shared fixtures for execution tests: synthetic bars and candles."""

from market import Bar

from execution.events import CandleEvent
from execution.market_data.replay import bars_to_candles


def make_bars(symbol: str = "TEST", count: int = 40, start: float = 100.0) -> tuple[Bar, ...]:
    """Deterministic ascending bars with a gentle up-drift."""
    bars = []
    for i in range(count):
        close = start + (i % 10) - 3.0 + i * 0.05
        bars.append(
            Bar(
                symbol=symbol,
                open=close - 0.5,
                high=close + 0.5,
                low=close - 1.0,
                close=close,
                volume=1000 + i,
                timestamp=f"2026-01-{(i % 28) + 1:02d} 09:{15 + (i % 45):02d}:00",
                bar_size="15m",
            )
        )
    return tuple(bars)


def make_candles(symbol: str = "TEST", count: int = 40) -> tuple[CandleEvent, ...]:
    return bars_to_candles(make_bars(symbol, count), "15m")
