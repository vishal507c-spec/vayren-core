"""Contract tests for market.native_aggregate (anchors, buckets, aggregation)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from market import native_aggregate as na  # noqa: E402

NativeBridgeError = na.NativeBridgeError


def test_mode() -> None:
    intervals = [60, 60, 60, 120, 60, 300]
    assert na.mode(intervals) == 60

    empty: list[int] = []
    assert na.mode(empty) is None


def test_session_anchor_seconds() -> None:
    # Default intraday anchor is 09:15 = 33300 seconds
    assert na.session_anchor_seconds("15m") == 33300
    assert na.session_anchor_seconds("1D") == 33300


def test_bucket_start_calculation() -> None:
    # 2026-01-05 09:20:00 with 15m (900s) timeframe and 09:15 (33300s) anchor
    start = na.bucket_start("2026-01-05T09:20:00", size_s=900, anchor_s=33300)
    assert start == "2026-01-05 09:15:00"

    # Next bucket: 09:30
    start2 = na.bucket_start("2026-01-05T09:32:00", size_s=900, anchor_s=33300)
    assert start2 == "2026-01-05 09:30:00"


def test_closed_count() -> None:
    # 4 rows when newest is still forming -> 3 closed
    assert na.closed_count(fresh_rows=4, newest_is_forming=True) == 3
    # 4 rows when newest is not forming -> 4 closed
    assert na.closed_count(fresh_rows=4, newest_is_forming=False) == 4


def test_aggregate_ascending_rows() -> None:
    days = [100, 100, 100, 100]
    secs = [33300, 33360, 33420, 34200]  # First three in bucket 0, last in bucket 1
    opens = [100.0, 101.0, 102.0, 105.0]
    highs = [102.0, 103.0, 104.0, 107.0]
    lows = [99.0, 100.5, 101.5, 104.5]
    closes = [101.0, 102.0, 103.5, 106.0]
    volumes = [10.0, 20.0, 30.0, 40.0]

    buckets = na.aggregate(
        days=days,
        secs=secs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        timeframe_seconds=900,
        session_start=33300,
    )

    assert len(buckets) == 2
    # Bucket 0: open=100.0, high=104.0, low=99.0, close=103.5, vol=60.0
    day0, idx0, o0, h0, l0, c0, v0 = buckets[0]
    assert day0 == 100
    assert idx0 == 0
    assert o0 == 100.0
    assert h0 == 104.0
    assert l0 == 99.0
    assert c0 == 103.5
    assert v0 == 60.0


def test_aggregate_empty_returns_empty() -> None:
    buckets = na.aggregate(
        days=[],
        secs=[],
        opens=[],
        highs=[],
        lows=[],
        closes=[],
        volumes=[],
        timeframe_seconds=900,
        session_start=33300,
    )
    assert buckets == []
