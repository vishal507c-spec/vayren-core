"""SymbolRepository tests — discovery and per-stock candle access."""

from pathlib import Path

import pytest

from market.repository.symbol_repository import SymbolRepository


def test_list_symbols_sorted(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    assert repository.list_symbols() == ("AMBUJACEM", "BPCL", "RELIANCE")


def test_list_symbols_ignores_non_db_files(tmp_path: Path) -> None:
    tmp_path.joinpath("README.md").write_text("not a db", encoding="utf-8")
    repository = SymbolRepository(tmp_path)
    assert repository.list_symbols() == ()


def test_get_candles_returns_bars(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    bars = repository.get_candles("BPCL", 8)
    assert len(bars) == 8
    assert bars[0].symbol == "BPCL"
    assert bars[0].timestamp == "2026-01-01 09:15:00"
    assert bars[-1].timestamp == "2026-01-08 09:15:00"
    assert bars[0].open == 101.0


def test_get_candles_unknown_symbol_raises(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    with pytest.raises(FileNotFoundError):
        repository.get_candles("NOTALISTED", 10)


def test_get_quotes_returns_latest_real_quote(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    quotes = repository.get_quotes(("AMBUJACEM", "BPCL", "RELIANCE"))
    assert [quote.symbol for quote in quotes] == ["AMBUJACEM", "BPCL", "RELIANCE"]
    ambu = quotes[0]  # 12 bars: latest open 112.0, close 113.0
    assert ambu.price == 113.0
    assert ambu.timestamp == "2026-01-12 09:15:00"
    assert ambu.change_pct == pytest.approx(100.0 * (113.0 - 112.0) / 112.0)
    reliance = quotes[2]  # 5 bars: latest open 105.0, close 106.0
    assert reliance.price == 106.0
    assert reliance.change_pct == pytest.approx(100.0 * (106.0 - 105.0) / 105.0)


def test_get_quotes_skips_missing_files(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    quotes = repository.get_quotes(("AMBUJACEM", "GHOST"))
    assert [quote.symbol for quote in quotes] == ["AMBUJACEM"]


def test_get_quotes_empty_universe(symbol_directory: Path) -> None:
    repository = SymbolRepository(symbol_directory)
    assert repository.get_quotes(()) == ()


def test_get_candles_missing_directory_raises(tmp_path: Path) -> None:
    repository = SymbolRepository(tmp_path / "nowhere")
    assert repository.list_symbols() == ()
    with pytest.raises(FileNotFoundError):
        repository.get_candles("AMBUJACEM", 10)
