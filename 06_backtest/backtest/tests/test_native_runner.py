"""Parity: Rust runner kernels vs the frozen Python rules.

The references below are verbatim copies of the pre-migration
`_window_bounds` margin arithmetic and `default_batch_workers` clamp, kept in
the TEST as oracles. The kernel must agree on every valid date pair —
including month/year/leap boundaries — and reject the same impossible dates
the standard-library ISO parser rejected.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from backtest.native_runner import default_workers, window_bounds


def _ref_window_bounds(start_date: str, end_date: str) -> tuple[str, str]:
    margin = timedelta(days=7)
    lower = date.fromisoformat(start_date) - margin
    upper = date.fromisoformat(end_date) + margin
    return f"{lower.isoformat()} 00:00:00", f"{upper.isoformat()} 23:59:59"


def _ref_default_workers(cpu: int) -> int:
    return max(2, min(6, cpu or 4))


def test_weekly_margin_brackets_the_requested_range() -> None:
    assert window_bounds("2026-01-08", "2026-01-10") == (
        "2026-01-01 00:00:00",
        "2026-01-17 23:59:59",
    )


def test_month_year_and_leap_boundaries() -> None:
    assert window_bounds("2026-01-03", "2026-01-03") == (
        "2025-12-27 00:00:00",
        "2026-01-10 23:59:59",
    )
    assert window_bounds("2024-02-25", "2024-02-29") == (
        "2024-02-18 00:00:00",
        "2024-03-07 23:59:59",
    )
    assert window_bounds("2023-02-25", "2023-02-25") == (
        "2023-02-18 00:00:00",
        "2023-03-04 23:59:59",
    )


@pytest.mark.parametrize(
    "pair",
    [("2026-02-30", "2026-03-01"), ("2026-04-31", "2026-04-30"), ("junk", "2026-01-01")],
)
def test_impossible_dates_stay_rejected(pair: tuple[str, str]) -> None:
    start, end = pair
    with pytest.raises(ValueError):
        _ref_window_bounds(start, end)
    with pytest.raises(ValueError):
        window_bounds(start, end)


def test_window_bounds_fuzz() -> None:
    rng = random.Random(20260924)
    anchor = date(2024, 2, 1)
    for _ in range(400):
        start = anchor + timedelta(days=rng.randint(0, 900))
        end = start + timedelta(days=rng.randint(0, 400))
        pair = (start.isoformat(), end.isoformat())
        assert window_bounds(*pair) == _ref_window_bounds(*pair), pair


def test_worker_clamp_never_goes_below_two_or_above_six() -> None:
    for cpu in range(0, 65):
        assert default_workers(cpu) == _ref_default_workers(cpu), cpu
    assert default_workers(0) == 4
    assert default_workers(1) == 2
    assert default_workers(32) == 6
