"""Parity: Rust replay window vs the frozen Python range rule.

The reference below is a verbatim copy of the pre-migration `slice_bars`
filter (kept in the TEST as an oracle, never in production). The kernel must
agree on which bar indices survive a date window, including open-ended and
degenerate bounds.
"""

from __future__ import annotations

import random

from backtest.native_replay import window


def _ref_window(timestamps: list[str], start: str | None, end: str | None) -> list[int]:
    if not timestamps:
        return []
    if start is None and end is None:
        return list(range(len(timestamps)))
    kept: list[int] = []
    for index, timestamp in enumerate(timestamps):
        day = timestamp[:10]
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        kept.append(index)
    return kept


def test_open_ended_window_keeps_everything_in_order() -> None:
    stamps = ["2026-01-02T09:15:00+00:00", "2026-01-05T09:15:00+00:00"]
    assert window(stamps, None, None) == (0, 1)
    assert window([], None, None) == ()


def test_bounds_are_inclusive_on_the_date_prefix() -> None:
    stamps = ["2026-01-01T00:00:00+00:00", "2026-01-02T23:59:00+00:00", "2026-01-03T00:00:00+00:00"]
    assert window(stamps, "2026-01-02", "2026-01-02") == (1,)
    assert window(stamps, "2026-01-02", None) == (1, 2)
    assert window(stamps, None, "2026-01-02") == (0, 1)
    assert window(stamps, "2026-01-04", "2026-01-05") == ()


def test_short_timestamps_compare_by_whole_value() -> None:
    # A bar stamped without a time component is its own date prefix.
    assert window(["2026-01-02", "2026-01"], "2026-01", "2026-01-02") == (0, 1)


def test_window_fuzz() -> None:
    rng = random.Random(20260923)
    days = [f"2026-{month:02d}-{day:02d}" for month in range(1, 13) for day in (1, 15, 28)]
    for _ in range(300):
        stamps = [
            f"{rng.choice(days)}T{rng.randint(0, 23):02d}:00:00+00:00"
            for _ in range(rng.randint(0, 30))
        ]
        start = rng.choice([None, rng.choice(days), "2026-01-01", "2025-12-31"])
        end = rng.choice([None, rng.choice(days), "2026-12-31", "2027-01-01"])
        assert window(stamps, start, end) == tuple(_ref_window(stamps, start, end)), (
            start,
            end,
            stamps,
        )
