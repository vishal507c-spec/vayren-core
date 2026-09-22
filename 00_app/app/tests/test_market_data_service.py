"""Canonical market-data adapter tests (tmp SQLite fixtures, real schema)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in (
    "00_app",
    "01_core",
    "03_market",
    "05_strategy",
    "06_backtest",
):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pytest  # noqa: E402

from app.services.market_data_service import (  # noqa: E402
    MarketDataError,
    MarketDataService,
    Quote,
    resolve_data_dir,
)


def _write_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL,"
        " high REAL, low REAL, close REAL, volume INTEGER)"
    )
    con.execute("CREATE TABLE non_trading(date_str TEXT, interval TEXT)")
    con.execute("CREATE TABLE history_boundaries(boundary_type TEXT)")
    con.executemany(
        "INSERT INTO ohlcv(candle_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def _bars_15m(day: str, closes: list[float]) -> list[tuple]:
    rows = []
    for index, close in enumerate(closes):
        hour = 9 + (15 * (index + 1)) // 60
        minute = (15 * (index + 1)) % 60
        stamp = f"{day} {hour:02d}:{minute:02d}:00"
        rows.append((stamp, close - 1.0, close + 1.0, close - 2.0, close, 1000 + index))
    return rows


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    _write_db(
        tmp_path / "AAA.db",
        _bars_15m("2026-01-05", [10.0, 11.0, 12.0, 13.0]),
    )
    _write_db(
        tmp_path / "BBB.db",
        _bars_15m("2026-01-05", [20.0, 21.0]),
    )
    (tmp_path / "notes.txt").write_text("not a database", encoding="utf-8")
    broken = tmp_path / "BROKEN.db"
    broken.write_text("not sqlite", encoding="utf-8")
    return tmp_path


def test_resolve_prefers_env_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VAYREN_DATA_DIR", str(tmp_path))
    assert resolve_data_dir() == tmp_path
    assert resolve_data_dir(tmp_path) == tmp_path
    # A missing explicit dir never crashes resolution: the next usable
    # candidate (env, then legacy, then default) wins instead.
    monkeypatch.delenv("VAYREN_DATA_DIR")
    assert resolve_data_dir(tmp_path / "missing-dir-xyz") != (tmp_path / "missing-dir-xyz")


def test_discovery_lists_real_symbols_and_skips_invalid(store: Path) -> None:
    service = MarketDataService(store)
    assert service.list_symbols() == ["AAA", "BBB"]
    report = service.discovery_report()
    assert report.valid_sources == 2
    assert any(name == "BROKEN.db" for name, _ in report.skipped)


def test_base_bars_normalize_to_canonical_shape(store: Path) -> None:
    service = MarketDataService(store)
    bars = service.get_bars("AAA")
    assert len(bars) == 4
    assert [b.timestamp for b in bars] == sorted(b.timestamp for b in bars)
    first = bars[0]
    assert first.symbol == "AAA"
    assert first.open == 9.0
    assert first.close == 10.0
    assert first.volume == 1000


def test_invalid_rows_are_skipped_not_fatal(tmp_path: Path) -> None:
    _write_db(
        tmp_path / "AAA.db",
        [
            ("2026-01-05 09:30:00", 10.0, 11.0, 9.0, 10.5, 100),
            ("bad-stamp", 10.0, 11.0, 9.0, 10.5, 100),
            ("2026-01-05 09:45:00", -1.0, 11.0, 9.0, 10.5, 100),
            ("2026-01-05 10:00:00", 10.0, 9.0, 11.0, 10.5, 100),
            ("2026-01-05 10:15:00", 10.0, 11.0, 9.0, 10.5, None),
        ],
    )
    service = MarketDataService(tmp_path)
    bars = service.get_bars("AAA")
    assert [b.timestamp for b in bars] == [
        "2026-01-05 09:30:00",
        "2026-01-05 10:15:00",
    ]
    assert bars[1].volume == 0


def test_timeframe_aggregation_uses_kernel(store: Path) -> None:
    service = MarketDataService(store)
    assert service.base_timeframe("AAA") == "15m"
    assert "30m" in service.available_timeframes("AAA")
    bars = service.get_bars("AAA", "30m")
    assert len(bars) == 2
    assert bars[0].open == 9.0
    assert bars[0].close == 11.0
    assert bars[0].high == 12.0
    assert bars[0].low == 8.0
    assert bars[0].volume == 2001


def test_unsupported_timeframe_is_actionable(store: Path) -> None:
    service = MarketDataService(store)
    with pytest.raises(MarketDataError, match="Unsupported timeframe"):
        service.get_bars("AAA", "9x")


def test_unknown_symbol_is_actionable(store: Path) -> None:
    service = MarketDataService(store)
    with pytest.raises(MarketDataError, match="No database found"):
        service.get_bars("ZZZ")


def test_date_filtering(store: Path) -> None:
    service = MarketDataService(store)
    bars = service.get_bars("AAA", start="2026-01-05", end="2026-01-05")
    assert len(bars) == 4
    assert service.get_bars("AAA", start="2026-01-06", end="2026-01-07") == ()
    with pytest.raises(MarketDataError, match="after"):
        service.get_bars("AAA", start="2026-01-07", end="2026-01-06")


def test_quotes_and_ranges(store: Path) -> None:
    service = MarketDataService(store)
    quotes = {q.symbol: q for q in service.get_quotes(["AAA", "BBB", "ZZZ"])}
    assert quotes["AAA"].price == 13.0
    assert quotes["AAA"].change_pct == pytest.approx(100.0 * (13.0 - 12.0) / 12.0)
    assert isinstance(quotes["ZZZ"], Quote)
    assert quotes["ZZZ"].price is None
    assert service.date_range("AAA") == ("2026-01-05", "2026-01-05")
