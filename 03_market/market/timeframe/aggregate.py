"""Timeframe aggregation — merges base bars into higher timeframes.

Pure math over database rows. No SQL, no events. Session-anchored for
intraday buckets (detected from the data), calendar-aligned for daily
(00:00) and weekly (Monday 00:00) buckets. Only real rows are merged —
never fabricated values.

The bucket-grouping and single-pass OHLCV accumulation loop is owned by
Rust (`rust/vayren-core`, `aggregate` module); Python keeps timestamp
parsing and bar formatting (domain/IO). The detectors keep their parsing
and route the frequency vote (mode) through the Rust `stats` kernel.
"""

import sqlite3
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from market.models.bar import Bar
from market.native_aggregate import aggregate as _native_aggregate
from market.native_aggregate import mode as _native_mode

_DAY_SECONDS = 86400
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_timestamp(value: str) -> datetime:
    """Parse ``YYYY-MM-DD HH:MM:SS`` or ``YYYY-MM-DD`` into a datetime.

    `datetime.fromisoformat` accepts both (any single date/time separator)
    and does no locale lookup, unlike strptime.
    """
    return datetime.fromisoformat(value)


def detect_bar_duration(timestamps: Sequence[str]) -> int | None:
    """Dominant bar duration in seconds from actual rows, or None if < 2 rows."""
    if len(timestamps) < 2:
        return None
    previous = _parse_timestamp(timestamps[0])
    deltas: list[int] = []
    for raw in timestamps[1:]:
        current = _parse_timestamp(raw)
        deltas.append(round((current - previous).total_seconds()))
        previous = current
    return _native_mode(deltas)


def detect_session_start(rows: Sequence[sqlite3.Row]) -> int:
    """Mode of the first bar's seconds-since-midnight per calendar day.

    Robust against partial windows: a window starting mid-day still contains
    complete later days whose first bar is the true session start.
    """
    first_of_day: dict[date, datetime] = {}
    for row in rows:
        dt = _parse_timestamp(row["timestamp"])
        current = first_of_day.get(dt.date())
        if current is None or dt < current:
            first_of_day[dt.date()] = dt
    starts = [dt.hour * 3600 + dt.minute * 60 + dt.second for dt in first_of_day.values()]
    return _native_mode(starts) or 0


def _bar_timestamp(
    bucket_start: date, timeframe_seconds: int, index: int, session_start: int
) -> str:
    if timeframe_seconds < _DAY_SECONDS:
        bar_time = datetime.combine(bucket_start, datetime.min.time()) + timedelta(
            seconds=session_start + index * timeframe_seconds
        )
    else:
        bar_time = datetime.combine(bucket_start, datetime.min.time())
    return bar_time.strftime(_TIMESTAMP_FORMAT)


def aggregate_bars(
    rows: Sequence[sqlite3.Row], timeframe_seconds: int, label: str, session_start: int
) -> list[Bar]:
    """Merge ascending base rows into `timeframe_seconds` bars, ascending.

    Rows are grouped by bucket; OHLCV comes from real rows only: open = first
    row's open, high = max, low = min, close = last row's close, volume = sum.
    Accumulators run in a single pass — no per-bucket re-scans.
    """
    if not rows:
        return []
    days: list[int] = []
    secs: list[int] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    symbols: list[str] = []
    for row in rows:
        dt = _parse_timestamp(row["timestamp"])
        days.append(dt.date().toordinal())
        secs.append(dt.hour * 3600 + dt.minute * 60 + dt.second)
        opens.append(row["open"])
        highs.append(row["high"])
        lows.append(row["low"])
        closes.append(row["close"])
        volumes.append(row["volume"])
        symbols.append(row["symbol"])
    buckets = _native_aggregate(
        days, secs, opens, highs, lows, closes, volumes, timeframe_seconds, session_start
    )
    bars: list[Bar] = []
    for day, index, o, h, low, close, volume in buckets:
        bars.append(
            Bar(
                symbol=symbols[0],
                open=o,
                high=h,
                low=low,
                close=close,
                volume=int(volume),
                timestamp=_bar_timestamp(
                    date.fromordinal(day), timeframe_seconds, index, session_start
                ),
                bar_size=label,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars
