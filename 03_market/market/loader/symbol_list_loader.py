"""SymbolListLoader — event-driven symbol discovery."""

from logging import getLogger

from core.event_bus.event_bus import EventBus

from market.events.list_symbols import ListSymbols
from market.events.symbols_listed import SymbolsListed
from market.repository.symbol_repository import SymbolRepository

logger = getLogger(__name__)


class SymbolListLoader:
    """Scans the data directory when ListSymbols arrives and publishes SymbolsListed.

    Never called directly by other modules — only via the EventBus.
    """

    def __init__(self, repository: SymbolRepository, bus: EventBus) -> None:
        self._repository = repository
        self._bus = bus

    def on_list_symbols(self, _event: ListSymbols) -> None:
        try:
            symbols = self._repository.list_symbols()
        except Exception:
            logger.exception("Failed to scan the data directory")
            return
        logger.info("Discovered %d symbols", len(symbols))
        self._bus.publish(SymbolsListed(symbols=tuple(symbols)))
