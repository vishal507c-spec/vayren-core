"""Symbols — CSV source with database-derived fallback."""

from datetime import datetime

import pytest

from data.storage.candle_db import CandleDB, db_path
from data.symbols import discover_symbols, load_symbols, resolve_symbols
from data.tests.conftest import candle, make_settings


def test_load_symbols_reads_csv(tmp_path) -> None:
    csv_path = tmp_path / "symbols.csv"
    csv_path.write_text("trading_symbol,interval\nRELIANCE,15m\nTCS,5minute\n", encoding="utf-8")
    symbols = load_symbols(csv_path)
    assert symbols == [
        {"trading_symbol": "RELIANCE", "interval": "15m"},
        {"trading_symbol": "TCS", "interval": "5minute"},
    ]


def test_load_symbols_missing_csv_raises(tmp_path) -> None:
    # Preserved original behaviour: the CSV is opened directly.
    with pytest.raises(FileNotFoundError):
        load_symbols(tmp_path / "nope.csv")


def test_discover_symbols_falls_back_to_databases(tmp_path) -> None:
    for symbol in ("RELIANCE", "TCS"):
        cdb = CandleDB(db_path(tmp_path, symbol, "15m"))
        cdb.connect()
        cdb.upsert([candle(datetime(2026, 1, 1, 9, 15))])
        cdb.close()
    symbols = discover_symbols(tmp_path)
    assert {s["trading_symbol"] for s in symbols} == {"RELIANCE", "TCS"}
    assert all(s["interval"] == "15m" for s in symbols)


def test_resolve_symbols_prefers_csv(tmp_path) -> None:
    csv_path = tmp_path / "symbols.csv"
    csv_path.write_text("trading_symbol,interval\nCSVONLY,15m\n", encoding="utf-8")
    settings = make_settings(tmp_path, symbols_csv=csv_path)
    symbols = resolve_symbols(settings)
    assert symbols == [{"trading_symbol": "CSVONLY", "interval": "15m"}]


def test_resolve_symbols_falls_back_when_no_csv(tmp_path) -> None:
    settings = make_settings(tmp_path)
    assert resolve_symbols(settings) == []  # empty data dir → no symbols
