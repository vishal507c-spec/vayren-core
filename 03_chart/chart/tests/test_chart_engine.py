"""ChartEngine tests."""

from core.event_bus.event_bus import EventBus
from market.events.data_loaded import DataLoaded
from market.models.bar import Bar

from chart.engine.chart_engine import ChartEngine
from chart.events.chart_ready import ChartReady


def _bar(symbol: str, timestamp: str) -> Bar:
    return Bar(
        symbol=symbol,
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
        volume=1000,
        timestamp=timestamp,
    )


def test_on_data_loaded_publishes_sorted_chart_ready() -> None:
    bus = EventBus()
    engine = ChartEngine(bus)
    bus.subscribe(DataLoaded, engine.on_data_loaded)
    received: list[ChartReady] = []
    bus.subscribe(ChartReady, received.append)
    bars = (_bar("SPY", "2026-01-03"), _bar("SPY", "2026-01-01"), _bar("SPY", "2026-01-02"))
    bus.publish(DataLoaded(symbol="SPY", bars=bars))
    assert len(received) == 1
    model = received[0].model
    assert model.symbol == "SPY"
    assert [bar.timestamp for bar in model.bars] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_empty_data_publishes_nothing() -> None:
    bus = EventBus()
    engine = ChartEngine(bus)
    bus.subscribe(DataLoaded, engine.on_data_loaded)
    received: list[ChartReady] = []
    bus.subscribe(ChartReady, received.append)
    bus.publish(DataLoaded(symbol="SPY", bars=()))
    assert received == []
