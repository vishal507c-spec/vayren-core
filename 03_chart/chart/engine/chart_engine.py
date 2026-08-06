"""ChartEngine — prepares the chart model from loaded candles."""

from logging import getLogger

from core.event_bus.event_bus import EventBus
from market.events.data_loaded import DataLoaded

from chart.events.chart_ready import ChartReady
from chart.models.chart_model import ChartModel

logger = getLogger(__name__)


class ChartEngine:
    """Builds an immutable ChartModel from DataLoaded and publishes ChartReady.

    No SQL, no UI. Guarantees the model contract: ascending bars, non-empty.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def on_data_loaded(self, event: DataLoaded) -> None:
        if not event.bars:
            logger.warning("No candles to chart for %s", event.symbol)
            return
        bars = tuple(sorted(event.bars, key=lambda bar: bar.timestamp))
        model = ChartModel(symbol=event.symbol, bars=bars)
        logger.info("Chart ready for %s (%d bars)", event.symbol, len(bars))
        self._bus.publish(ChartReady(model=model))
