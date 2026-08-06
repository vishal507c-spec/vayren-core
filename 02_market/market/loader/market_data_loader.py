"""MarketDataLoader — event-driven candle loading."""

from logging import getLogger

from core.event_bus.event_bus import EventBus

from market.events.data_loaded import DataLoaded
from market.events.load_symbol import LoadSymbol
from market.repository.candle_repository import CandleRepository

logger = getLogger(__name__)


class MarketDataLoader:
    """Loads candles when LoadSymbol arrives and publishes DataLoaded.

    Never called directly by other modules — only via the EventBus.
    """

    def __init__(self, repository: CandleRepository, bus: EventBus) -> None:
        self._repository = repository
        self._bus = bus

    def on_load_symbol(self, event: LoadSymbol) -> None:
        try:
            bars = self._repository.get_candles(event.symbol, event.limit)
        except Exception:
            logger.exception("Failed to load candles for %s", event.symbol)
            return
        logger.info("Loaded %d candles for %s", len(bars), event.symbol)
        self._bus.publish(DataLoaded(symbol=event.symbol, bars=tuple(bars)))
