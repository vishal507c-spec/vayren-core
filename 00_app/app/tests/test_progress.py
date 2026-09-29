"""The run-progress contract: measured numbers, no fake timers.

These tests exist to stop the three ways a progress bar lies: inventing a
rate, counting a symbol before it finished, and reporting a partial run as a
complete one.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in (
    "00_app",
    "01_core",
    "02_data",
    "03_market",
    "05_strategy",
    "06_backtest",
    "09_broker",
):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


from app.progress import MIN_ETA_SAMPLES, RunProgress  # noqa: E402


def test_there_is_no_rate_until_real_samples_exist() -> None:
    run = RunProgress(527)
    run.symbol_started("AAA")
    # One symbol started, none finished: an ETA here would be a fiction.
    assert run.mean_seconds is None
    assert run.eta_seconds is None
    assert run.throughput == 0.0
    assert run.completed == 0
    assert run.remaining == 527


def test_progress_only_moves_when_a_symbol_finishes() -> None:
    run = RunProgress(3)
    run.symbol_started("AAA")
    assert run.completed == 0
    run.symbol_done([], 100, 0.0)
    run.symbol_started("BBB")
    assert run.completed == 1
    assert run.current == "BBB"
    run.symbol_done([], 100, 0.0)
    run.symbol_done([], 100, 0.0)
    assert run.completed == 3
    assert run.remaining == 0
    assert run.pct == 100.0


def test_failures_and_skips_are_counted_and_named() -> None:
    run = RunProgress(5)
    run.symbol_done([], 10, 1.0)
    run.symbol_started("BAD")
    run.symbol_failed("BAD")
    run.symbol_started("EMPTY")
    run.symbol_skipped("EMPTY")
    run.symbol_done([], 10, 2.0)
    snap = run.snapshot()
    assert snap["failed"] == ["BAD"]
    assert snap["skipped"] == ["EMPTY"]
    assert snap["completed"] == 2
    # 5 total − 2 done − 1 failed − 1 skipped = 1 still to go.
    assert snap["remaining"] == 1
    assert snap["pct"] == 80.0


def test_eta_shrinks_as_work_lands() -> None:
    run = RunProgress(100)
    for i in range(10):
        run.symbol_started(f"S{i}")
        time.sleep(0.01)
        run.symbol_done([], 10, 0.0)
    first = run.eta_seconds
    assert first is not None and first > 0.0
    # Thirty more symbols at the same real rate must leave strictly less work,
    # so the ETA must shrink — this is the whole point of measuring it.
    for i in range(10, 40):
        run.symbol_started(f"S{i}")
        time.sleep(0.01)
        run.symbol_done([], 10, 0.0)
    later = run.eta_seconds
    assert later is not None and 0.0 < later < first


def test_throughput_counts_every_finished_symbol() -> None:
    run = RunProgress(10)
    for i in range(4):
        run.symbol_started(f"S{i}")
        time.sleep(0.05)
        run.symbol_done([], 10, 0.0)
    snap = run.snapshot()
    assert snap["throughput"] > 0.0
    assert snap["elapsed_secs"] > 0.0
    assert snap["trades"] == 0
    assert snap["bars"] == 40


def test_cancellation_is_cooperative_and_latches() -> None:
    flag = {"cancel": False}
    run = RunProgress(10, None, lambda: flag["cancel"])
    assert run.cancel_requested() is False
    flag["cancel"] = True
    assert run.cancel_requested() is True
    assert run.cancelled is True
    assert run.snapshot()["cancelled"] is True


def test_a_slow_symbol_is_flagged_without_advancing_progress() -> None:
    run = RunProgress(5)
    run.symbol_started("SLOW")
    run.current = "SLOW"
    # Age the in-flight symbol past the long-running threshold.
    run._symbol_started -= run.current_elapsed + 60.0
    assert run.long_running is True
    assert run.quiet is True
    assert run.completed == 0, "a slow symbol must never be counted as done"
    assert run.snapshot()["long_running"] is True


def test_the_load_stage_reports_its_own_real_counters() -> None:
    run = RunProgress(527)
    run.load_progress(
        done=100,
        total=527,
        symbol="AAA",
        mean_secs=0.5,
        eta_secs=213.5,
        elapsed_secs=50.0,
    )
    snap = run.snapshot()
    # The bar must not read "0 / 527" while a third of the load is done.
    assert snap["done"] == 100
    assert snap["headline_total"] == 527
    assert snap["stage"] == "data"
    assert snap["eta_secs"] == 213.5
    assert snap["elapsed_secs"] == 50.0


def test_every_snapshot_field_is_present_for_the_ui() -> None:
    run = RunProgress(2)
    run.symbol_started("AAA")
    snap = run.snapshot()
    for field in (
        "stage",
        "stage_pct",
        "total",
        "done",
        "headline_total",
        "completed",
        "failed",
        "skipped",
        "remaining",
        "pct",
        "current",
        "current_secs",
        "elapsed_secs",
        "eta_secs",
        "mean_secs",
        "throughput",
        "trades",
        "bars",
        "net_pnl",
        "long_running",
        "quiet",
        "cancelled",
    ):
        assert field in snap, field


def test_minimum_samples_is_not_one() -> None:
    # A single sample is one symbol's luck, not a rate.
    assert MIN_ETA_SAMPLES >= 3
