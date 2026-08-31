"""ChartEngine — prepares the chart model from loaded candles."""

from logging import getLogger

from core.event_bus.event_bus import EventBus
from market.events.data_loaded import DataLoaded
from market.models.bar import Bar

from chart.events.chart_ready import ChartReady
from chart.models.chart_model import ChartModel
from chart.models.timeframe import infer_timeframe

logger = getLogger(__name__)

_DEFAULT_EXCHANGE = "NSE"


def _ascending(bars: tuple[Bar, ...]) -> bool:
    """True when bars are already ascending by timestamp (no copy needed)."""
    return all(bars[index - 1].timestamp <= bars[index].timestamp for index in range(1, len(bars)))


class ChartEngine:
    """Builds an immutable ChartModel from DataLoaded and publishes ChartReady.

    No SQL, no UI. Guarantees the model contract: ascending bars, non-empty,
    with timeframe and exchange populated for display.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def on_data_loaded(self, event: DataLoaded) -> None:
        if not event.bars:
            logger.warning("No candles to chart for %s", event.symbol)
            return
        bars = (
            event.bars
            if _ascending(event.bars)
            else tuple(sorted(event.bars, key=lambda bar: bar.timestamp))
        )
        model = ChartModel(
            symbol=event.symbol,
            bars=bars,
            timeframe=infer_timeframe(bars),
            exchange=_DEFAULT_EXCHANGE,
        )
        logger.info("Chart ready for %s (%d bars)", event.symbol, len(bars))
        self._bus.publish(ChartReady(model=model))
