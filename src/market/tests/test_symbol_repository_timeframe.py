"""`get_candles_timeframe` serves only what the base rows can produce.

Pins the 2026-10-18 relabel defect class: a request finer than the store's
base bar (the drifted "3m" against 30m stores) came back as base rows wearing
the requested label, and non-aligned multiples were bucket-approximated. The
Rust ladder (`available_timeframes`) is the authority — the base duration and
its whole multiples are served; everything else returns no candles.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from market.repository.symbol_repository import SymbolRepository  # noqa: E402


def _write_store(directory: Path, symbol: str = "TESTCO", bars: int = 8) -> None:
    """A real store file shaped like the downloader writes (30m bars, 09:15+)."""
    connection = sqlite3.connect(directory / f"{symbol}.db")
    try:
        connection.execute(
            "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL, high REAL,"
            " low REAL, close REAL, volume INTEGER)"
        )
        rows = []
        for index in range(bars):
            minutes = 9 * 60 + 15 + 30 * index
            stamp = f"2026-10-16 {minutes // 60:02d}:{minutes % 60:02d}:00"
            price = 100.0 + index
            rows.append((stamp, price, price + 1.0, price - 1.0, price + 0.5, 10 * (index + 1)))
        connection.executemany("INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()


def test_finer_than_base_returns_no_relabeled_rows(tmp_path: Path) -> None:
    _write_store(tmp_path)
    repository = SymbolRepository(str(tmp_path))
    assert repository.get_candles_timeframe("TESTCO", "3m", 5) == []


def test_non_aligned_multiple_returns_no_bucket_approximation(tmp_path: Path) -> None:
    _write_store(tmp_path)
    repository = SymbolRepository(str(tmp_path))
    assert repository.get_candles_timeframe("TESTCO", "45m", 4) == []


def test_unknown_label_still_returns_no_rows(tmp_path: Path) -> None:
    _write_store(tmp_path)
    repository = SymbolRepository(str(tmp_path))
    assert repository.get_candles_timeframe("TESTCO", "3x", 5) == []


def test_base_timeframe_reads_back_with_its_own_label(tmp_path: Path) -> None:
    _write_store(tmp_path)
    repository = SymbolRepository(str(tmp_path))
    bars = repository.get_candles_timeframe("TESTCO", "30m", 3)
    assert [bar.timestamp for bar in bars] == [
        "2026-10-16 11:45:00",
        "2026-10-16 12:15:00",
        "2026-10-16 12:45:00",
    ]
    assert all(bar.bar_size == "30m" for bar in bars)
    assert bars[-1].close == 107.5
    assert bars[-1].volume == 80


def test_whole_multiple_aggregates(tmp_path: Path) -> None:
    _write_store(tmp_path)
    repository = SymbolRepository(str(tmp_path))
    bars = repository.get_candles_timeframe("TESTCO", "1h", 2)
    assert [bar.timestamp for bar in bars] == [
        "2026-10-16 11:15:00",
        "2026-10-16 12:15:00",
    ]
    assert all(bar.bar_size == "1h" for bar in bars)
    assert (bars[0].open, bars[0].close, bars[0].volume) == (104.0, 105.5, 110)
    assert (bars[1].open, bars[1].close, bars[1].volume) == (106.0, 107.5, 150)


def test_detected_ladder_is_the_serving_contract(tmp_path: Path) -> None:
    _write_store(tmp_path)
    repository = SymbolRepository(str(tmp_path))
    frames = repository.detect_timeframes("TESTCO")
    assert "30m" in frames and "1h" in frames
    assert "3m" not in frames and "45m" not in frames
    for frame in frames:
        assert repository.get_candles_timeframe("TESTCO", frame, 1), frame


def test_store_fingerprint_tracks_absence_and_change(tmp_path: Path) -> None:
    repository = SymbolRepository(str(tmp_path))
    assert repository.store_fingerprint("TESTCO") is None
    _write_store(tmp_path)
    first = repository.store_fingerprint("TESTCO")
    assert first is not None
    connection = sqlite3.connect(tmp_path / "TESTCO.db")
    try:
        connection.execute(
            "INSERT INTO ohlcv VALUES ('2026-10-16 13:15:00', 108.0, 109.0, 107.0, 108.5, 90)"
        )
        connection.commit()
    finally:
        connection.close()
    second = repository.store_fingerprint("TESTCO")
    assert second is not None and second != first
