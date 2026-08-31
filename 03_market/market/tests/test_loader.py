"""MarketDataLoader tests."""

from pathlib import Path

from core.event_bus.event_bus import EventBus

from market.database.sqlite import SqliteCandleDatabase
from market.events.data_loaded import DataLoaded
from market.events.load_symbol import LoadSymbol
from market.loader.market_data_loader import MarketDataLoader
from market.repository.candle_repository import CandleRepository


def test_on_load_symbol_publishes_data_loaded(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        bus = EventBus()
        loader = MarketDataLoader(CandleRepository(database), bus)
        bus.subscribe(LoadSymbol, loader.on_load_symbol)
        received: list[DataLoaded] = []
        bus.subscribe(DataLoaded, received.append)
        bus.publish(LoadSymbol(symbol="SPY", limit=10))
    finally:
        database.close()
    assert len(received) == 1
    assert received[0].symbol == "SPY"
    assert len(received[0].bars) == 10
    assert received[0].bars[0].timestamp == "2026-01-01"


def test_on_load_symbol_all_history_when_limit_none(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        bus = EventBus()
        loader = MarketDataLoader(CandleRepository(database), bus)
        bus.subscribe(LoadSymbol, loader.on_load_symbol)
        received: list[DataLoaded] = []
        bus.subscribe(DataLoaded, received.append)
        bus.publish(LoadSymbol(symbol="SPY"))
    finally:
        database.close()
    assert len(received) == 1
    assert received[0].symbol == "SPY"
    assert [bar.timestamp for bar in received[0].bars] == [
        f"2026-01-{day:02d}" for day in range(1, 11)
    ]


def test_failed_load_publishes_nothing(tmp_path: Path) -> None:
    database = SqliteCandleDatabase(tmp_path / "nonexistent.db")
    bus = EventBus()
    loader = MarketDataLoader(CandleRepository(database), bus)
    bus.subscribe(LoadSymbol, loader.on_load_symbol)
    received: list[DataLoaded] = []
    bus.subscribe(DataLoaded, received.append)
    bus.publish(LoadSymbol(symbol="SPY", limit=10))
    assert received == []
