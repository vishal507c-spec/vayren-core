"""TimeframeListLoader — event-driven timeframe detection for a symbol."""

from logging import getLogger

from core.event_bus.event_bus import EventBus

from market.events.list_timeframes import ListTimeframes
from market.events.timeframes_listed import TimeframesListed
from market.repository.candle_repository import CandleRepository
from market.repository.symbol_repository import SymbolRepository

logger = getLogger(__name__)


class TimeframeListLoader:
    """Detects available timeframes when ListTimeframes arrives.

    Detection always runs against the SQLite database — never a cached list.
    Never called directly by other modules — only via the EventBus.
    """

    def __init__(self, repository: CandleRepository | SymbolRepository, bus: EventBus) -> None:
        self._repository = repository
        self._bus = bus

    def on_list_timeframes(self, event: ListTimeframes) -> None:
        try:
            timeframes = self._repository.detect_timeframes(event.symbol)
        except Exception:
            logger.exception("Failed to detect timeframes for %s", event.symbol)
            return
        logger.info("Detected timeframes %s for %s", timeframes, event.symbol)
        self._bus.publish(TimeframesListed(symbol=event.symbol, timeframes=timeframes))
