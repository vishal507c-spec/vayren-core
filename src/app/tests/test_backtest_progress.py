"""Progress reporting and cancellation must not change a single result.

The whole point of the progress system is that it is observation: the same
universe, window, capital and strategy must produce identical trades whether
the UI is watching or not. These tests run both ways and compare.
"""

from __future__ import annotations

import inspect
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from app.progress import RunProgress  # noqa: E402
from app.services.backtest_service import run_backtest  # noqa: E402


def _store(tmp_path: Path, symbols: tuple[str, ...], bars: int = 400) -> None:
    from datetime import date, timedelta

    for name in symbols:
        con = sqlite3.connect(str(tmp_path / f"{name}.db"))
        con.execute(
            "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL,"
            " high REAL, low REAL, close REAL, volume INTEGER)"
        )
        con.execute("CREATE TABLE non_trading(date_str TEXT, interval TEXT)")
        con.execute("CREATE TABLE history_boundaries(boundary_type TEXT)")
        rows = []
        price = 100.0
        for day in range(20):
            day_label = (date(2026, 1, 1) + timedelta(days=day)).strftime("%Y-%m-%d")
            for slot in range(min(25, bars // 20)):
                wave = (day + slot) % 20
                price = 100.0 + (wave if wave < 10 else 20 - wave)
                hour = 9 + (15 * (slot + 1) + 15) // 60
                minute = (15 * (slot + 1) + 15) % 60
                rows.append(
                    (
                        f"{day_label} {hour:02d}:{minute:02d}:00",
                        price - 0.5,
                        price + 0.5,
                        price - 1.0,
                        price,
                        5000,
                    )
                )
        con.executemany(
            "INSERT INTO ohlcv(candle_time, open, high, low, close, volume)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.commit()
        con.close()


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    # No strategy file: the built-in SMA Crossover is the strategy of record
    # for these tests, exactly as the backtest suite uses it.
    _store(tmp_path, ("AAA", "BBB", "CCC"))
    return tmp_path


def _run(tmp_path: Path, **kwargs):
    return run_backtest(
        "SMA Crossover",
        ["AAA", "BBB", "CCC"],
        "15m",
        None,
        None,
        1000000.0,
        "buy",
        tmp_path,
        tmp_path,
        **kwargs,
    )


def test_progress_reporting_changes_no_result(store: Path) -> None:
    quiet = _run(store)
    events: list[dict] = []
    loud = _run(store, progress=RunProgress(3, events.append))
    # Identical execution, byte for byte.
    assert quiet["trades"] == loud["trades"]
    assert quiet["metrics"] == loud["metrics"]
    assert quiet["ranking"] == loud["ranking"]
    # And the watcher really did see the run happen.
    assert events
    assert events[-1]["completed"] == 3


def test_events_only_ever_claim_finished_work(store: Path) -> None:
    events: list[dict] = []
    _run(store, progress=RunProgress(3, events.append))
    for event in events:
        assert event["completed"] <= event["done"] <= event["total"]
        assert event["completed"] >= 0
        assert 0.0 <= event["pct"] <= 100.0
        assert event["elapsed_secs"] >= 0.0
    # The run reaches 100% exactly once the last symbol is done.
    assert any(e["completed"] == 3 and e["pct"] == 100.0 for e in events)


def test_cancelling_stops_early_and_is_not_a_complete_run(store: Path) -> None:
    seen: list[dict] = []
    progress = RunProgress(3, seen.append, lambda: progress.completed >= 1)
    _run(store, progress=progress)
    # Cancelled after the first symbol: the rest never ran.
    assert progress.completed == 1
    assert progress.cancelled is True
    assert seen[-1]["cancelled"] is True


def test_a_serial_and_a_parallel_load_produce_the_same_bars(store: Path) -> None:
    from app.services.backtest_service import _prefetch_bars
    from app.services.market_data_service import MarketDataService

    market = MarketDataService(store)
    serial, _ = _prefetch_bars(market, ["AAA", "BBB", "CCC"], "15m", None, None, workers=1)
    parallel, _ = _prefetch_bars(market, ["AAA", "BBB", "CCC"], "15m", None, None, workers=3)
    assert serial.keys() == parallel.keys()
    for symbol in serial:
        assert [b.timestamp for b in serial[symbol]] == [b.timestamp for b in parallel[symbol]]


def test_the_default_worker_count_is_the_measured_one() -> None:
    """Threads measured SLOWER on this workload, so the default stays at 1."""
    from app.services import backtest_service

    source = inspect.getsource(backtest_service._prefetch_bars)
    # The documented measurement must stay attached to the default.
    assert "workers = 1" in source
    assert "0.78x" in source, "the benchmark that justifies the bound is part of the code"
