"""Lab coverage probe tests: the counts are real, and unmeasured is None.

The §02 `DATA COMPLETENESS` strip exists only when this command returned
counts. These tests pin that contract: the probe reads the store, reports what
it actually saw, and refuses to answer at all when there is nothing to
measure — because an empty answer is what keeps a fake `0%` bar off screen.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in ("00_app", "01_core", "02_data", "03_market", "05_strategy", "09_broker"):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pytest  # noqa: E402

from app.headless import _lab_coverage  # noqa: E402
from app.services.market_data_service import MarketDataService  # noqa: E402


def _write_db(path: Path, stamps: list[str]) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL,"
        " high REAL, low REAL, close REAL, volume INTEGER)"
    )
    con.execute("CREATE TABLE non_trading(date_str TEXT, interval TEXT)")
    con.execute("CREATE TABLE history_boundaries(boundary_type TEXT)")
    con.executemany(
        "INSERT INTO ohlcv(candle_time, open, high, low, close, volume)"
        " VALUES (?, 10.0, 11.0, 9.0, 10.5, 100)",
        [(stamp,) for stamp in stamps],
    )
    con.commit()
    con.close()


@pytest.fixture()
def store(tmp_path: Path) -> MarketDataService:
    _write_db(
        tmp_path / "AAA.db",
        ["2019-09-19 09:15:00", "2019-09-19 09:30:00", "2026-08-18 09:15:00"],
    )
    # A second symbol that only starts in 2024 — it cannot cover a 2019 window.
    _write_db(tmp_path / "BBB.db", ["2024-01-02 09:15:00", "2024-01-02 09:30:00"])
    return MarketDataService(tmp_path)


def test_the_probe_reports_the_window_and_the_counts_it_measured(
    store: MarketDataService,
) -> None:
    result = _lab_coverage(
        store,
        ["AAA", "BBB"],
        "2019-09-19",
        "2026-08-18",
        "15m",
    )
    assert result is not None
    # The window travels with the counts: the Rust kernel derives the expected
    # bar count from it, so the two can never describe different ranges.
    assert result["start"] == "2019-09-19"
    assert result["end"] == "2026-08-18"
    assert result["timeframe"] == "15m"
    # The bar count belongs to ONE symbol, so the payload must name it — the
    # screen can never print it as a total for the whole selection.
    assert result["anchor"] == "AAA"
    assert result["symbols_total"] == 2
    # BBB starts in 2024: it still has bars inside a 2019–2026 window, so it
    # COVERS the window. A later listing is not a data defect.
    assert result["symbols_covering"] == 2
    # AAA really holds three bars in that window.
    assert result["bars_present"] == 3
    assert result["sampled"] is False


def test_a_symbol_outside_the_window_does_not_cover_it(store: MarketDataService) -> None:
    # BBB's only bars are in 2024, so a 2020 window excludes it entirely.
    result = _lab_coverage(store, ["AAA", "BBB"], "2019-09-19", "2020-01-01", "15m")
    assert result is not None
    assert result["symbols_covering"] == 1, "BBB has no bars before 2024"


def test_a_bounded_probe_says_so(store: MarketDataService) -> None:
    symbols = [f"S{index:03d}" for index in range(30)]
    result = _lab_coverage(store, symbols, "2019-09-19", "2026-08-18", "15m")
    assert result is not None
    # The selection is 30, the probe read 24 — and the answer must SAY that
    # rather than passing a partial count off as a complete one.
    assert result["symbols_total"] == 30
    assert result["symbols_probed"] == 24
    assert result["sampled"] is True


def test_an_explicit_full_scan_is_unbounded(store: MarketDataService) -> None:
    symbols = [f"S{index:03d}" for index in range(30)]
    result = _lab_coverage(store, symbols, "2019-09-19", "2026-08-18", "15m", full=True)
    assert result is not None
    assert result["symbols_probed"] == 30
    assert result["sampled"] is False


@pytest.mark.parametrize(
    ("symbols", "start", "end"),
    [
        ([], "2019-09-19", "2026-08-18"),
        (["AAA"], "", "2026-08-18"),
        (["AAA"], "2019-09-19", ""),
    ],
)
def test_nothing_to_measure_returns_nothing(
    store: MarketDataService, symbols: list[str], start: str, end: str
) -> None:
    assert _lab_coverage(store, symbols, start, end, "15m") is None


def test_no_repository_means_no_measurement() -> None:
    assert _lab_coverage(None, ["AAA"], "2019-09-19", "2026-08-18", "15m") is None


def test_a_symbol_with_no_database_is_skipped_not_counted(store: MarketDataService) -> None:
    result = _lab_coverage(store, ["AAA", "NOPE"], "2019-09-19", "2026-08-18", "15m")
    assert result is not None
    # The selection still counts as two; only AAA is known to cover the window.
    assert result["symbols_total"] == 2
    assert result["symbols_covering"] == 1
