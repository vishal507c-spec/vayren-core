"""Timeframe aggregation — merges base bars into higher timeframes.

Pure math over database rows. No SQL, no events. Session-anchored for
intraday buckets (detected from the data), calendar-aligned for daily
(00:00) and weekly (Monday 00:00) buckets. Only real rows are merged —
never fabricated values.
"""

import sqlite3
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from market.models.bar import Bar

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_DATE_FORMAT = "%Y-%m-%d"
_DAY_SECONDS = 86400


def _parse_timestamp(value: str) -> datetime:
    for fmt in (_TIMESTAMP_FORMAT, _DATE_FORMAT):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized candle timestamp: {value!r}")


def detect_bar_duration(timestamps: Sequence[str]) -> int | None:
    """Dominant bar duration in seconds from actual rows, or None if < 2 rows."""
    if len(timestamps) < 2:
        return None
    counts: Counter[int] = Counter()
    previous = _parse_timestamp(timestamps[0])
    for raw in timestamps[1:]:
        current = _parse_timestamp(raw)
        counts[round((current - previous).total_seconds())] += 1
        previous = current
    return counts.most_common(1)[0][0]


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
    counts: Counter[int] = Counter(
        dt.hour * 3600 + dt.minute * 60 + dt.second for dt in first_of_day.values()
    )
    if not counts:
        return 0
    return counts.most_common(1)[0][0]


def _bucket_key(dt: datetime, timeframe_seconds: int, session_start: int) -> tuple[date, int]:
    if timeframe_seconds < _DAY_SECONDS:
        sec_of_day = dt.hour * 3600 + dt.minute * 60 + dt.second
        if sec_of_day >= session_start:
            index = (sec_of_day - session_start) // timeframe_seconds
        else:
            index = sec_of_day // timeframe_seconds
        return dt.date(), index
    if timeframe_seconds == _DAY_SECONDS:
        return dt.date(), 0
    monday = dt.date() - timedelta(days=dt.weekday())
    return monday, 0


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
    """
    if not rows:
        return []
    buckets: dict[tuple[date, int], list[tuple[sqlite3.Row, datetime]]] = {}
    for row in rows:
        dt = _parse_timestamp(row["timestamp"])
        key = _bucket_key(dt, timeframe_seconds, session_start)
        buckets.setdefault(key, []).append((row, dt))

    bars: list[Bar] = []
    for key, bucket_rows in buckets.items():
        bucket_start, index = key
        first_row = bucket_rows[0][0]
        last_row = bucket_rows[-1][0]
        bars.append(
            Bar(
                symbol=first_row["symbol"],
                open=first_row["open"],
                high=max(row["high"] for row, _ in bucket_rows),
                low=min(row["low"] for row, _ in bucket_rows),
                close=last_row["close"],
                volume=sum(row["volume"] for row, _ in bucket_rows),
                timestamp=_bar_timestamp(bucket_start, timeframe_seconds, index, session_start),
                bar_size=label,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars
