"""Timeframe inference tests — dominant-gap → human-readable period."""

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
    assert infer_timeframe(()) == "1D"


def test_single_bar_returns_default() -> None:
    assert infer_timeframe((_bar("2026-04-08 09:15:00"),)) == "1D"


def test_daily_bars_return_1d() -> None:
    result = infer_timeframe(_daily(50))
    assert result == "1D"


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
    assert infer_timeframe(bars) == "1D"


def test_45min_bars_return_45m() -> None:
    result = infer_timeframe(_intraday(100, 45))
    assert result == "45m"


def test_2h_bars_return_2h() -> None:
    result = infer_timeframe(_intraday(100, 120))
    assert result == "2h"


def test_4h_bars_return_4h() -> None:
    # 4h has 2 bars/day, median would incorrectly give 1D for even samples
    from datetime import datetime, timedelta

    def _bar4(ts: str):
        from market.models.bar import Bar

        return Bar(symbol="SPY", open=100, high=105, low=95, close=102, volume=1000, timestamp=ts)

    bars = []
    start = datetime(2026, 4, 8, 9, 15, 0)
    for day in range(20):
        d = start + timedelta(days=day)
        bars.append(_bar4(datetime(d.year, d.month, d.day, 9, 15, 0).isoformat(sep=" ")))
        bars.append(_bar4(datetime(d.year, d.month, d.day, 13, 15, 0).isoformat(sep=" ")))
    # also test even count that previously bugged (21 bars =20 gaps even)
    assert infer_timeframe(tuple(bars[:21])) == "4h"
    assert infer_timeframe(tuple(bars)) == "4h"


def test_weekly_bars_return_1w() -> None:  # noqa: N802
    start = datetime(2026, 1, 6, 0, 0, 0)
    bars = tuple(_bar((start + timedelta(weeks=i)).isoformat(sep=" ")) for i in range(10))
    assert infer_timeframe(bars) == "1W"
