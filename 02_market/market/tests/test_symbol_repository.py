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


def test_get_candles_missing_directory_raises(tmp_path: Path) -> None:
    repository = SymbolRepository(tmp_path / "nowhere")
    assert repository.list_symbols() == ()
    with pytest.raises(FileNotFoundError):
        repository.get_candles("AMBUJACEM", 10)
