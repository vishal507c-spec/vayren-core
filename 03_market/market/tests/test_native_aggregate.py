"""Parity: Rust aggregation kernels vs frozen Python references.

The reference functions below are verbatim copies of the pre-migration
Python logic (kept in the TEST as oracles, never in production). The Rust
kernels must match exactly — same buckets, same OHLCV, same timestamps —
on deterministic fixtures and seeded random fuzz across intraday/daily/
weekly timeframes. Production holds no Python accumulation math (§12).
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from market.models.bar import Bar
from market.timeframe.aggregate import (
    _bar_timestamp,
    aggregate_bars,
    detect_bar_duration,
    detect_session_start,
)

_DAY_SECONDS = 86400
_FMT = "%Y-%m-%d %H:%M:%S"


def _ref_bucket_key(dt: datetime, tf: int, session_start: int) -> tuple[date, int]:
    if tf < _DAY_SECONDS:
        sec = dt.hour * 3600 + dt.minute * 60 + dt.second
        index = (sec - session_start) // tf if sec >= session_start else sec // tf
        return dt.date(), index
    if tf == _DAY_SECONDS:
        return dt.date(), 0
    monday = dt.date() - timedelta(days=dt.weekday())
    return monday, 0


def _ref_aggregate(rows: list[Any], tf: int, label: str, session_start: int) -> list[Bar]:
    if not rows:
        return []
    buckets: dict[tuple[date, int], list[float]] = {}
    symbols: dict[tuple[date, int], str] = {}
    for row in rows:
        dt = datetime.fromisoformat(row["timestamp"])
        key = _ref_bucket_key(dt, tf, session_start)
        acc = buckets.get(key)
        if acc is None:
            buckets[key] = [row["open"], row["high"], row["low"], row["close"], row["volume"]]
            symbols[key] = row["symbol"]
        else:
            if row["high"] > acc[1]:
                acc[1] = row["high"]
            if row["low"] < acc[2]:
                acc[2] = row["low"]
            acc[3] = row["close"]
            acc[4] += row["volume"]
    bars = [
        Bar(
            symbol=symbols[(day, index)],
            open=acc[0],
            high=acc[1],
            low=acc[2],
            close=acc[3],
            volume=int(acc[4]),
            timestamp=_bar_timestamp(day, tf, index, session_start),
            bar_size=label,
        )
        for (day, index), acc in buckets.items()
    ]
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


def _ref_mode(values: list[int]) -> int | None:
    if not values:
        return None
    counts: Counter[int] = Counter()
    for value in values:
        counts[value] += 1
    return counts.most_common(1)[0][0]


def _rows(symbol: str, start: datetime, step_seconds: int, count: int) -> list[Any]:
    rows = []
    price = 100.0
    for i in range(count):
        moment = start + timedelta(seconds=i * step_seconds)
        drift = (i % 7) - 3.0
        rows.append(
            {
                "symbol": symbol,
                "timestamp": moment.strftime(_FMT),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + drift * 0.1,
                "volume": 1000 + i,
            }
        )
        price += drift * 0.1
    return rows


def _as_bar_tuples(bars: list[Bar]) -> list[tuple]:
    return [
        (b.symbol, b.open, b.high, b.low, b.close, b.volume, b.timestamp, b.bar_size) for b in bars
    ]


def test_aggregate_deterministic() -> None:
    rows = _rows("TEST", datetime(2026, 1, 5, 9, 15), 900, 10)
    for tf, label in ((900, "15m"), (1800, "30m"), (3600, "1h")):
        assert _as_bar_tuples(aggregate_bars(rows, tf, label, 33300)) == _as_bar_tuples(
            _ref_aggregate(rows, tf, label, 33300)
        )
    assert aggregate_bars([], 900, "15m", 33300) == []


def test_aggregate_daily_weekly() -> None:
    rows = _rows("TEST", datetime(2026, 1, 5, 9, 15), 900, 200)
    for tf, label in ((86400, "1D"), (604800, "1W")):
        assert _as_bar_tuples(aggregate_bars(rows, tf, label, 33300)) == _as_bar_tuples(
            _ref_aggregate(rows, tf, label, 33300)
        )


def test_aggregate_fuzz() -> None:
    rng = random.Random(20260907)
    for trial in range(60):
        days = rng.randint(1, 6)
        step = rng.choice([60, 300, 900])
        count = rng.randint(1, days * 25 + 3)
        start = datetime(2026, 1, 5, 9, 15) + timedelta(days=rng.randint(0, 3))
        rows = _rows("FZ", start, step, count)
        tf = rng.choice([60, 300, 900, 1800, 3600, 7200, 14400, 86400, 604800])
        session = rng.choice([0, 33300])
        actual = _as_bar_tuples(aggregate_bars(rows, tf, "t", session))
        expected = _as_bar_tuples(_ref_aggregate(rows, tf, "t", session))
        assert actual == expected, (trial, tf, session, count)


def test_detectors_match_counter() -> None:
    rows = _rows("TEST", datetime(2026, 1, 5, 9, 15), 900, 50)
    assert detect_bar_duration([r["timestamp"] for r in rows]) == 900
    assert detect_session_start(rows) == 33300
    assert detect_bar_duration([]) is None
    assert detect_bar_duration(["2026-01-05 09:15:00"]) is None


def test_mode_fuzz_vs_counter() -> None:
    from market.native_aggregate import mode

    rng = random.Random(99)
    for _ in range(300):
        values = [rng.randint(0, 12) for _ in range(rng.randint(0, 40))]
        assert mode(values) == _ref_mode(values), values
    assert mode([]) is None
