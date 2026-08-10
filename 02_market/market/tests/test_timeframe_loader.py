"""Timeframe loader tests — TimeframeChanged and ListTimeframes flows."""

from pathlib import Path

from core.event_bus.event_bus import EventBus

from market.database.ohlcv import OhlcvCandleDatabase
from market.events.data_loaded import DataLoaded
from market.events.list_timeframes import ListTimeframes
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from market.loader.market_data_loader import MarketDataLoader
from market.loader.timeframe_list_loader import TimeframeListLoader
from market.repository.candle_repository import CandleRepository


def test_timeframe_changed_publishes_data_loaded(ohlcv_intraday_path: Path) -> None:
    database = OhlcvCandleDatabase(ohlcv_intraday_path)
    database.connect()
    try:
        bus = EventBus()
        loader = MarketDataLoader(CandleRepository(database), bus)
        bus.subscribe(TimeframeChanged, loader.on_timeframe_changed)
        received: list[DataLoaded] = []
        bus.subscribe(DataLoaded, received.append)
        bus.publish(TimeframeChanged(symbol="TATASTEEL", timeframe="30m", limit=10))
    finally:
        database.close()
    assert len(received) == 1
    assert received[0].symbol == "TATASTEEL"
    assert received[0].bars[0].timestamp == "2026-01-02 10:45:00"
    assert received[0].bars[0].bar_size == "30m"


def test_timeframe_changed_failure_publishes_nothing(tmp_path: Path) -> None:
    database = OhlcvCandleDatabase(tmp_path / "nonexistent.db")
    bus = EventBus()
    loader = MarketDataLoader(CandleRepository(database), bus)
    bus.subscribe(TimeframeChanged, loader.on_timeframe_changed)
    received: list[DataLoaded] = []
    bus.subscribe(DataLoaded, received.append)
    bus.publish(TimeframeChanged(symbol="TATASTEEL", timeframe="30m", limit=10))
    assert received == []


def test_list_timeframes_publishes_timeframes_listed(ohlcv_intraday_path: Path) -> None:
    database = OhlcvCandleDatabase(ohlcv_intraday_path)
    database.connect()
    try:
        bus = EventBus()
        loader = TimeframeListLoader(CandleRepository(database), bus)
        bus.subscribe(ListTimeframes, loader.on_list_timeframes)
        received: list[TimeframesListed] = []
        bus.subscribe(TimeframesListed, received.append)
        bus.publish(ListTimeframes(symbol="TATASTEEL"))
    finally:
        database.close()
    assert len(received) == 1
    assert received[0].symbol == "TATASTEEL"
    assert received[0].timeframes == (
        "15m",
        "30m",
        "45m",
        "1h",
        "2h",
        "4h",
        "1D",
        "1W",
    )


def test_list_timeframes_failure_publishes_nothing(tmp_path: Path) -> None:
    database = OhlcvCandleDatabase(tmp_path / "nonexistent.db")
    bus = EventBus()
    loader = TimeframeListLoader(CandleRepository(database), bus)
    bus.subscribe(ListTimeframes, loader.on_list_timeframes)
    received: list[TimeframesListed] = []
    bus.subscribe(TimeframesListed, received.append)
    bus.publish(ListTimeframes(symbol="TATASTEEL"))
    assert received == []
