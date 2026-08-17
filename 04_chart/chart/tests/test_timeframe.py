"""Timeframe inference tests — median-gap → human-readable period."""

from datetime import datetime, timedelta

from market.models.bar import Bar

from chart.models.timeframe import infer_timeframe


def _bar(timestamp: str) -> Bar:
    return Bar(
        symbol="SPY",
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
        volume=1000,
        timestamp=timestamp,
    )


def _intraday(count: int, step_minutes: int) -> tuple[Bar, ...]:
    start = datetime(2026, 4, 8, 9, 15, 0)
    return tuple(
        _bar((start + timedelta(minutes=step_minutes * i)).isoformat(sep=" ")) for i in range(count)
    )


def _daily(count: int) -> tuple[Bar, ...]:
    start = datetime(2026, 4, 8, 9, 15, 0)
    return tuple(_bar((start + timedelta(days=i)).isoformat(sep=" ")) for i in range(count))


def test_empty_bars_returns_default() -> None:
    assert infer_timeframe(()) == "1d"


def test_single_bar_returns_default() -> None:
    assert infer_timeframe((_bar("2026-04-08 09:15:00"),)) == "1d"


def test_daily_bars_return_1d() -> None:
    result = infer_timeframe(_daily(50))
    assert result == "1d"


def test_15min_bars_return_15m() -> None:
    result = infer_timeframe(_intraday(100, 15))
    assert result == "15m"


def test_30min_bars_return_30m() -> None:
    result = infer_timeframe(_intraday(100, 30))
    assert result == "30m"


def test_1h_bars_return_1h() -> None:
    result = infer_timeframe(_intraday(100, 60))
    assert result == "1h"


def test_garbage_timestamps_return_default() -> None:
    bars = (_bar("garbage"), _bar("also garbage"))
    assert infer_timeframe(bars) == "1d"
