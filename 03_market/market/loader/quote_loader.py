"""QuoteLoader — event-driven latest-quote snapshot."""

from logging import getLogger

from core.event_bus.event_bus import EventBus

from market.events.quotes_loaded import QuotesLoaded
from market.events.symbols_listed import SymbolsListed
from market.repository.symbol_repository import SymbolRepository

logger = getLogger(__name__)


class QuoteLoader:
    """Loads the latest real quote for every listed symbol, once per universe.

    Triggered by SymbolsListed (subscribed in bootstrap, after the watchlist
    is populated) and publishes QuotesLoaded. Re-listings of the same symbol
    set are a no-op, so the one-time batch is never re-run for a chart switch.

    Never called directly by other modules — only via the EventBus.
    """

    def __init__(self, repository: SymbolRepository, bus: EventBus) -> None:
        self._repository = repository
        self._bus = bus
        self._last_symbols: tuple[str, ...] | None = None

    def on_symbols_listed(self, event: SymbolsListed) -> None:
        if event.symbols == self._last_symbols:
            return
        try:
            quotes = self._repository.get_quotes(event.symbols)
        except Exception:
            logger.exception("Failed to load quotes")
            return
        self._last_symbols = event.symbols
        logger.info("Loaded %d quotes", len(quotes))
        self._bus.publish(QuotesLoaded(quotes=tuple(quotes)))
