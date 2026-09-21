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


# ── streaming rules the live provider used to hold in Python ───────────────


def _ref_bucket_start(timestamp: str, size_s: int, anchor_s: int) -> str:
    """Old ``broker_feed._bucket_start``, verbatim."""
    day, clock = timestamp[:10], timestamp[11:19]
    parts = clock.split(":")
    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if size_s >= 86_400:
        return f"{day} 00:00:00"
    if seconds < anchor_s:
        anchor_s -= 86_400
    index = (seconds - anchor_s) // size_s
    start = anchor_s + index * size_s
    if start < 0:
        start += 86_400
    hh, rem = divmod(start % 86_400, 3600)
    mm, ss = divmod(rem, 60)
    return f"{day} {hh:02d}:{mm:02d}:{ss:02d}"


def _ref_parse_anchor(anchor: object) -> int:
    """Old ``broker_feed._parse_anchor``, verbatim."""
    try:
        hh, mm = anchor.split(":")[:2]  # type: ignore[union-attr]
        return int(hh) * 3600 + int(mm) * 60
    except (ValueError, AttributeError):
        return 9 * 3600 + 15 * 60


def _ref_fold(
    bucket: tuple[float, float, float, float, float] | None,
    price: float,
    dayvol: int,
    last_dayvol: int | None,
) -> tuple[float, float, float, float, float]:
    """Old ``BrokerFeedProvider._ingest`` OHLCV accumulation, verbatim."""
    delta = max(0, dayvol - (last_dayvol if last_dayvol is not None else dayvol))
    if bucket is None:
        return (price, price, price, price, delta)
    open_, high, low, _close, volume = bucket
    return (open_, max(high, price), min(low, price), price, volume + delta)


def _ref_closed_count(fresh: int, newest_is_forming: bool) -> int:
    """Old ``SqliteTailProvider`` withhold-the-forming-candle rule."""
    return max(0, fresh - 1) if newest_is_forming else fresh


def test_bucket_flooring_matches_the_retired_provider_rule() -> None:
    from market.native_aggregate import bucket_start

    anchors = [0, 33300, 34200, 86399]
    sizes = [60, 300, 900, 1800, 3600, 7200, 14400, 86400, 604800]
    stamps = [
        "2026-01-05 00:00:00",
        "2026-01-05 09:14:59",
        "2026-01-05 09:15:00",
        "2026-01-05 09:15:01",
        "2026-01-05 15:47:00",
        "2026-01-05 23:59:59",
        "2026-01-06 08:20:00",
    ]
    for stamp in stamps:
        for size_s in sizes:
            for anchor_s in anchors:
                assert bucket_start(stamp, size_s, anchor_s) == _ref_bucket_start(
                    stamp, size_s, anchor_s
                ), (stamp, size_s, anchor_s)


def test_bucket_flooring_survives_a_random_quote_stream() -> None:
    from market.native_aggregate import bucket_start

    rng = random.Random(20260920)
    for _ in range(400):
        stamp = (
            f"2026-01-{rng.randint(1, 28):02d} "
            f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
        )
        size_s = rng.choice([60, 300, 900, 1800, 3600, 86400])
        anchor_s = rng.choice([0, 33300, rng.randint(0, 86_399)])
        assert bucket_start(stamp, size_s, anchor_s) == _ref_bucket_start(stamp, size_s, anchor_s)


def test_session_anchors_read_like_the_retired_parser() -> None:
    from market.native_aggregate import session_anchor_seconds

    for label in ["09:15", "9:15", " 09 : 15 ", "00:00", "23:59", "", "junk", "09", "ab:cd"]:
        assert session_anchor_seconds(label) == _ref_parse_anchor(label), label
    for _ in range(200):
        hh, mm = random.Random().randrange(24), random.Random().randrange(60)
        label = f"{hh:02d}:{mm:02d}"
        assert session_anchor_seconds(label) == _ref_parse_anchor(label)


def test_quotes_fold_into_their_bucket_exactly_like_the_retired_rule() -> None:
    from market.native_aggregate import fold_tick

    rng = random.Random(20260921)
    for _ in range(300):
        bucket: tuple[float, float, float, float, float] | None = None
        dayvol = rng.randint(0, 500)
        last_dayvol: int | None = None
        for _ in range(rng.randint(1, 12)):
            price = round(rng.uniform(50.0, 500.0), 2)
            dayvol = rng.randint(0, 100_000)
            expected = _ref_fold(bucket, price, dayvol, last_dayvol)
            assert fold_tick(bucket, price, dayvol, last_dayvol) == expected, (
                bucket,
                price,
                dayvol,
                last_dayvol,
            )
            bucket = expected
            last_dayvol = dayvol


def test_the_forming_row_is_the_last_one_to_emit() -> None:
    from market.native_aggregate import closed_count

    for fresh in range(0, 12):
        for forming in (False, True):
            assert closed_count(fresh, forming) == _ref_closed_count(fresh, forming), (
                fresh,
                forming,
            )
