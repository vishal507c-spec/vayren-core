"""Market snapshot bridge tests: the strategy names the chart popup lists."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from app.headless import _empty_market_snapshot, _market_snapshot, _strategy_names  # noqa: E402
from app.services.market_data_service import MarketDataService  # noqa: E402


def _write_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL,"
        " high REAL, low REAL, close REAL, volume INTEGER)"
    )
    con.execute("CREATE TABLE non_trading(date_str TEXT, interval TEXT)")
    con.execute("CREATE TABLE history_boundaries(boundary_type TEXT)")
    con.execute(
        "INSERT INTO ohlcv(candle_time, open, high, low, close, volume)"
        " VALUES ('2026-01-05 09:15:00', 9.0, 11.0, 8.0, 10.0, 500),"
        " ('2026-01-05 09:30:00', 10.0, 12.0, 9.0, 11.0, 600)"
    )
    con.commit()
    con.close()


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    _write_db(tmp_path / "AAA.db")
    return tmp_path


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    (strategy_dir / "OBR.py").write_text("# opening breakout\n", encoding="utf-8")
    return strategy_dir


def test_strategy_names_match_the_lab_library(library: Path) -> None:
    names = _strategy_names(str(library))
    assert "OBR" in names


def test_strategy_names_are_honest_empty_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A broken strategy backend never raises into the snapshot and never
    # invents names: the popup shows no STRATEGIES section instead.
    def _boom(_strategy_dir: str) -> list[dict]:
        raise RuntimeError("lab backend unavailable")

    monkeypatch.setattr("app.headless._lab_library_rows", _boom)
    assert _strategy_names(str(tmp_path)) == []


def test_market_snapshot_carries_the_strategy_names(store: Path, library: Path) -> None:
    service = MarketDataService(store)
    snapshot = _market_snapshot(service, {}, str(library))
    assert snapshot["selected_symbol"] == "AAA"
    assert snapshot["bars"]
    assert "OBR" in snapshot["strategies"]


def test_every_market_snapshot_path_reports_the_strategies(store: Path, library: Path) -> None:
    service = MarketDataService(store)
    # Unknown symbol, unsupported timeframe and no repository at all each
    # keep the key present, so the popup's STRATEGIES section never silently
    # disappears with the bars.
    for snapshot in (
        _market_snapshot(service, {"symbol": "ZZZ"}, str(library)),
        _market_snapshot(service, {"timeframe": "9x"}, str(library)),
        _market_snapshot(None, {}, str(library)),
    ):
        assert "OBR" in snapshot["strategies"]
    assert _empty_market_snapshot("No symbols discovered", ["OBR"])["strategies"] == ["OBR"]
    assert _empty_market_snapshot()["strategies"] == []
