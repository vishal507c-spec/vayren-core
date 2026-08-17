"""MarketDataLoader — event-driven candle loading."""

from logging import getLogger

from core.event_bus.event_bus import EventBus

from market.events.data_loaded import DataLoaded
from market.events.load_symbol import LoadSymbol
from market.events.timeframe_changed import TimeframeChanged
from market.repository.candle_repository import CandleRepository
from market.repository.symbol_repository import SymbolRepository

logger = getLogger(__name__)


class MarketDataLoader:
    """Loads candles when LoadSymbol arrives and publishes DataLoaded.

    Never called directly by other modules — only via the EventBus.
    """

    def __init__(self, repository: CandleRepository | SymbolRepository, bus: EventBus) -> None:
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

    def on_timeframe_changed(self, event: TimeframeChanged) -> None:
        """Load candles at the requested timeframe — queries only when it changes."""
        try:
            bars = self._repository.get_candles_timeframe(
                event.symbol, event.timeframe, event.limit
            )
        except Exception:
            logger.exception("Failed to load %s candles for %s", event.timeframe, event.symbol)
            return
        logger.info("Loaded %d %s candles for %s", len(bars), event.timeframe, event.symbol)
        self._bus.publish(DataLoaded(symbol=event.symbol, bars=tuple(bars)))
