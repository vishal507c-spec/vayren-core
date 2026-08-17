"""CandleRepository tests."""

from pathlib import Path

from market.database.sqlite import SqliteCandleDatabase
from market.repository.candle_repository import CandleRepository


def test_get_candles_maps_rows_to_bars(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        repository = CandleRepository(database)
        bars = repository.get_candles("SPY", 10)
    finally:
        database.close()
    assert len(bars) == 10
    first = bars[0]
    assert first.symbol == "SPY"
    assert first.timestamp == "2026-01-01"
    assert first.open == 101.0
    assert first.high == 103.0
    assert first.low == 100.0
    assert first.close == 102.0
    assert first.volume == 1001


def test_get_candles_respects_limit(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        repository = CandleRepository(database)
        bars = repository.get_candles("SPY", 3)
    finally:
        database.close()
    assert [bar.timestamp for bar in bars] == ["2026-01-08", "2026-01-09", "2026-01-10"]


def test_get_candles_all_history_when_limit_none(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        repository = CandleRepository(database)
        bars = repository.get_candles("SPY", None)
    finally:
        database.close()
    assert [bar.timestamp for bar in bars] == [f"2026-01-{day:02d}" for day in range(1, 11)]
