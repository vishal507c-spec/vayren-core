"""Parity: Rust download kernel vs the retired Python rules.

The oracles below are the implementations that were deleted from
``data/calendar.py``, ``data/storage/scanner.py``, ``data/storage/candle_db.py``
and the downloader's sweep/engine/queue modules. They exist only here, so the
kernel can be fuzzed against the behaviour it had to preserve.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Any

from data.models import DLState
from data.native_download import (
    JobWindow,
    candle_time,
    candle_time_for,
    chunk_count,
    chunk_windows,
    count_trading_days,
    coverage_pct,
    decide_coverage,
    filename_token,
    head_sweep_eligible,
    is_trading_day,
    job_rank,
    job_window,
    market_open,
    target_start,
    today_end,
    validate_range,
)


def _ref_candle_time(raw: str) -> str:
    """`candle_db.normalise_ts_str`, exactly as it was written."""
    s = raw[:19]
    s = s.replace("T", " ")
    if len(s) == 19:
        s = s[:17] + "00"
    return s


def _ref_candle_time_for(moment: datetime) -> str:
    """`candle_db.normalise_ts_dt`, exactly as it was written."""
    return moment.strftime("%Y-%m-%d %H:%M:00")


def _ref_filename(symbol: str) -> str:
    """`candle_db.db_path`'s inline sanitiser."""
    return re.sub(r"[^A-Za-z0-9_]", "", symbol)


def _ref_chunk_windows(
    start: datetime, end: datetime, chunk_days: int
) -> list[tuple[datetime, datetime]]:
    """`sweep.forward_sweep`'s walk over the sweep range."""
    windows: list[tuple[datetime, datetime]] = []
    chunk = start
    while chunk <= end:
        stop = min(chunk + timedelta(days=chunk_days), end)
        windows.append((chunk, stop))
        chunk = stop + timedelta(minutes=1)
    return windows


def _ref_chunk_count(start: datetime, end: datetime, chunk_days: int) -> int:
    """`engine._count_chunks`'s own loop."""
    return max(len(_ref_chunk_windows(start, end, chunk_days)), 1)


def _ref_head_sweep_eligible(
    total_new: int, before: datetime | None, after: datetime | None
) -> bool:
    """`sweep.forward_sweep`'s LISTING_START eligibility rule."""
    if total_new != 0:
        return False
    if before is None:
        return after is None
    return after is not None and after.date() == before.date()


def _ref_job_window(
    kind: str, bound: datetime | None, target: datetime, end_of_today: datetime
) -> JobWindow:
    """`queue.build_from_scan`'s gap ranges, before the kernel owned them."""
    if kind == "full":
        return JobWindow(target, end_of_today, "NOT_STARTED: full history")
    if kind == "head":
        if bound is None:
            return JobWindow(target, end_of_today, "PARTIAL: missing head (no earliest)")
        return JobWindow(target, bound - timedelta(minutes=1), "PARTIAL: missing head coverage")
    if bound is None:
        return JobWindow(target, end_of_today, "PARTIAL: missing tail (no latest)")
    return JobWindow(bound + timedelta(minutes=1), end_of_today, "PARTIAL: missing tail coverage")


def _ref_is_trading_day(d: datetime, holidays: frozenset[str]) -> bool:
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in holidays


def _ref_count(d1: datetime, d2: datetime, holidays: frozenset[str]) -> int:
    count, cur = 0, d1
    while cur < d2:
        if _ref_is_trading_day(cur, holidays):
            count += 1
        cur += timedelta(days=1)
    return count


def _ref_market_open(n: datetime, open_h: int, open_m: int, close_h: int, close_m: int) -> bool:
    if n.weekday() >= 5:
        return False
    mo = n.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    mc = n.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return mo <= n <= mc


def _ref_scan(
    row_count: int,
    trading_days: int,
    corrupt: int,
    earliest: datetime | None,
    latest: datetime | None,
    boundary: dict[str, object] | None,
    target: datetime,
    end_of_today: datetime,
    max_history_years: int,
    head_tolerance: int,
    tail_lag: int,
    holidays: frozenset[str],
) -> dict[str, Any]:
    """`DatabaseScanner._scan_one`'s decision, exactly as it was written."""
    if earliest is None or latest is None or row_count == 0:
        return {
            "state": DLState.NOT_STARTED,
            "missing_head": False,
            "missing_tail": False,
            "coverage_pct": 0.0,
            "listing_start_verified": False,
        }
    target_td = max_history_years * 252
    cov_pct = min(100.0, trading_days / target_td * 100) if target_td else 0.0
    missing_head = _ref_count(target, earliest, holidays) > head_tolerance
    listing_start_verified = False
    if missing_head and boundary:
        try:
            boundary_dt = datetime.strptime(str(boundary["boundary_date"]), "%Y-%m-%d").date()
        except Exception:
            boundary_dt = None
        if boundary["verified"] == 1 and boundary_dt is not None and boundary_dt <= earliest.date():
            missing_head = False
            listing_start_verified = True
    days_tail = (end_of_today.date() - latest.date()).days
    missing_tail = days_tail > tail_lag
    state = (
        DLState.PARTIAL_DOWNLOAD
        if missing_head or missing_tail or corrupt > 0
        else DLState.DOWNLOAD_COMPLETE
    )
    return {
        "state": state,
        "missing_head": missing_head,
        "missing_tail": missing_tail,
        "coverage_pct": cov_pct,
        "listing_start_verified": listing_start_verified,
    }


def _verdict(**given: object):
    return decide_coverage(**given)  # type: ignore[arg-type]


def test_the_closing_minute_seconds_still_close_the_window() -> None:
    monday = datetime(2026, 8, 10, 12, 40, 0)
    assert market_open(monday, 9, 15, 12, 40)
    assert not market_open(monday.replace(second=1), 9, 15, 12, 40)
    assert not market_open(datetime(2026, 8, 15, 10, 0), 9, 15, 12, 40)


def test_the_history_window_is_midnight_years_back() -> None:
    now = datetime(2026, 8, 10, 14, 30, 15)
    assert target_start(now, 2) == now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=2 * 365)
    assert target_start(now, 10).hour == 0 and target_start(now, 10).minute == 0
    assert today_end(now) == now.replace(hour=23, minute=59, second=59, microsecond=0)


def test_calendar_fuzz_against_the_retired_rules() -> None:
    rng = random.Random(20260920)
    base = datetime(2016, 1, 1)
    holidays = frozenset(
        (base + timedelta(days=rng.randrange(0, 4000))).strftime("%Y-%m-%d") for _ in range(40)
    )
    for _ in range(500):
        day = base + timedelta(days=rng.randrange(0, 4000))
        now = day + timedelta(hours=rng.randrange(0, 24), minutes=rng.randrange(0, 60), seconds=30)
        assert market_open(now, 9, 15, 12, 40) == _ref_market_open(now, 9, 15, 12, 40), now
        assert is_trading_day(now, holidays) == _ref_is_trading_day(now, holidays), now
        other = base + timedelta(days=rng.randrange(0, 4000))
        assert count_trading_days(day, other, holidays) == _ref_count(day, other, holidays), (
            day,
            other,
        )


def test_coverage_verdict_fuzz_against_the_scanner() -> None:
    rng = random.Random(20260921)
    holidays = frozenset(f"2026-0{rng.randrange(1, 9)}-0{rng.randrange(1, 9)}" for _ in range(6))
    for _ in range(300):
        years = rng.choice([0, 1, 5, 10])
        row_count = rng.choice([0, 1, 250, 5000])
        trading_days = rng.randrange(0, 3000)
        corrupt = rng.choice([0, 0, 1, 4])
        earliest = rng.choice([None, datetime(2026, 1, 5, 9, 15), datetime(2014, 3, 2, 10, 0)])
        latest = rng.choice([None, datetime(2026, 9, 18, 15, 30), datetime(2026, 3, 2, 15, 30)])
        boundary = rng.choice(
            [
                None,
                {"boundary_date": "2026-01-05", "verified": 1},
                {"boundary_date": "2026-01-05", "verified": 0},
                {"boundary_date": "not-a-date", "verified": 1},
            ]
        )
        target = datetime(2016, 9, 20)
        end_of_today = datetime(2026, 9, 20, 23, 59, 59)
        head = rng.choice([0, 5, 40])
        tail = rng.choice([0, 5, 90])
        given = {
            "row_count": row_count,
            "trading_days": trading_days,
            "corrupt": corrupt,
            "earliest": earliest,
            "latest": latest,
            "boundary": boundary,
            "target_start_at": target,
            "today_end_at": end_of_today,
            "max_history_years": years,
            "head_tolerance_trading_days": head,
            "tail_lag_tolerance_days": tail,
            "holidays": holidays,
        }
        verdict = _verdict(**given)
        expected = _ref_scan(
            row_count,
            trading_days,
            corrupt,
            earliest,
            latest,
            boundary,
            target,
            end_of_today,
            years,
            head,
            tail,
            holidays,
        )
        assert verdict.state == expected["state"].name, (given, verdict, expected)
        assert verdict.missing_head == expected["missing_head"], (given, verdict)
        assert verdict.missing_tail == expected["missing_tail"], (given, verdict)
        assert verdict.coverage_pct == expected["coverage_pct"], (given, verdict)
        assert verdict.listing_start_verified == expected["listing_start_verified"], (given,)


def test_coverage_percent_matches_the_retired_formula() -> None:
    rng = random.Random(20260922)
    for _ in range(200):
        days = rng.randrange(0, 6000)
        years = rng.randrange(0, 30)
        target = years * 252
        expected = min(100.0, days / target * 100) if target else 0.0
        assert coverage_pct(days, years) == expected, (days, years)


def test_the_candle_key_shape_is_the_retired_one() -> None:
    for raw in (
        "2026-01-05T09:15:33",
        "2026-01-05 09:15:33",
        "2026-01-05 09:15:00",
        "2026-01-05",
        "2026",
        "",
        "not-a-date",
        "2026-01-05T09:15:33.000Z",
        "TrailingT Ts everywhere xx",
    ):
        assert candle_time(raw) == _ref_candle_time(raw), raw


def test_storage_normalisation_fuzz() -> None:
    rng = random.Random(20260923)
    alphabet = "0123456789-: T.XxZz"
    for _ in range(400):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 26)))
        assert candle_time(raw) == _ref_candle_time(raw), repr(raw)
    base = datetime(2016, 1, 1)
    for _ in range(400):
        moment = base + timedelta(
            days=rng.randrange(0, 4000),
            hours=rng.randrange(0, 24),
            minutes=rng.randrange(0, 60),
            seconds=rng.randrange(0, 60),
        )
        assert candle_time_for(moment) == _ref_candle_time_for(moment), moment


def test_filename_token_matches_the_retired_sanitiser() -> None:
    rng = random.Random(20260924)
    symbols = ["RELIANCE", "A.B-C", "", "HDFC Bank", "^NSE:TATAMOTTR", "µnicode✕", "NTPC_EQ"]
    for symbol in symbols:
        assert filename_token(symbol) == _ref_filename(symbol), symbol
    alphabet = "ABCxyz019_-.#: µ"
    for _ in range(300):
        symbol = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 18)))
        assert filename_token(symbol) == _ref_filename(symbol), repr(symbol)


def test_chunk_plan_matches_the_retired_sweep_loops() -> None:
    rng = random.Random(20260925)
    base = datetime(2020, 1, 1)
    for _ in range(300):
        start = base + timedelta(days=rng.randrange(0, 2000))
        end = start + timedelta(days=rng.randrange(-5, 900), minutes=rng.randrange(0, 60))
        days = rng.choice([1, 2, 20, 90, 200, 365])
        assert chunk_windows(start, end, days) == _ref_chunk_windows(start, end, days), (
            start,
            end,
            days,
        )
        assert chunk_count(start, end, days) == _ref_chunk_count(start, end, days), (
            start,
            end,
            days,
        )


def test_head_sweep_boundary_rule_matches_the_retired_sweep() -> None:
    rng = random.Random(20260926)
    base = datetime(2018, 3, 1)
    for _ in range(300):
        new_rows = rng.choice([0, 0, 1, 27])
        before = rng.choice(
            [None, base + timedelta(days=rng.randrange(0, 900)), base + timedelta(days=400)]
        )
        if before is None:
            after = rng.choice([None, base + timedelta(days=12)])
        else:
            after = rng.choice(
                [None, before, before + timedelta(hours=20), before + timedelta(days=1)]
            )
        assert head_sweep_eligible(new_rows, before, after) == _ref_head_sweep_eligible(
            new_rows, before, after
        ), (new_rows, before, after)


def test_queued_job_ranges_match_the_retired_queue() -> None:
    target = datetime(2016, 9, 20)
    end_of_today = datetime(2026, 9, 20, 23, 59, 59)
    bounds = [None, datetime(2020, 6, 1, 9, 15), datetime(2026, 9, 18, 15, 30, 45)]
    for kind in ("full", "head", "tail"):
        for bound in bounds:
            assert job_window(kind, bound, target, end_of_today) == _ref_job_window(
                kind, bound, target, end_of_today
            ), (kind, bound)


def _ref_validate_range(
    from_date: str, to_date: str
) -> tuple[datetime | None, datetime | None, str]:
    """Old ``DownloadWorker._run_download`` date handling, verbatim."""
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        return None, None, "invalid date range"
    if from_dt > to_dt:
        return None, None, "from date after to date"
    return from_dt, to_dt, ""


def _ref_job_rank(state: DLState) -> int:
    """Old ``DownloadQueue.build_from_scan`` priority map, verbatim."""
    priority_map = {
        DLState.PARTIAL_DOWNLOAD: 1,
        DLState.NOT_STARTED: 2,
        DLState.DOWNLOAD_COMPLETE: 3,
    }
    return priority_map.get(state, 9)


def test_the_download_window_and_its_failures_match_the_worker() -> None:
    pairs = [
        ("2026-01-05", "2026-02-05"),
        ("2026-01-05", "2026-01-05"),
        ("2026-02-05", "2026-01-05"),
        ("2026-01-05", "junk"),
        ("", "2026-01-05"),
        ("2026-13-45", "2026-01-05"),
        ("2016-09-20", "2026-09-20"),
    ]
    for from_date, to_date in pairs:
        window = validate_range(from_date, to_date)
        from_dt, to_dt, reason = _ref_validate_range(from_date, to_date)
        assert (window.from_dt, window.to_dt, window.reason) == (from_dt, to_dt, reason), (
            from_date,
            to_date,
        )


def test_queue_ranks_come_from_the_kernel_in_the_retired_order() -> None:
    for state in DLState:
        assert job_rank(state.name) == _ref_job_rank(state), state
    assert job_rank("SOMETHING_NEW") == 9
    assert job_rank("PARTIAL_DOWNLOAD") < job_rank("NOT_STARTED") < job_rank("DOWNLOAD_COMPLETE")
