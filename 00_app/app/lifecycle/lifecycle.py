"""AppLifecycle — application startup and terminal flow."""

from logging import getLogger

from chart.events.window_rendered import WindowRendered
from core.event_bus.event_bus import EventBus
from core.events.app_started import AppStarted
from market.events.load_symbol import LoadSymbol

logger = getLogger(__name__)


class AppLifecycle:
    """Owns the application flow: starts the load on AppStarted, closes on WindowRendered."""

    def __init__(self, bus: EventBus, symbol: str, limit: int) -> None:
        self._bus = bus
        self._symbol = symbol
        self._limit = limit

    def on_app_started(self, _event: AppStarted) -> None:
        """Begin Phase 1 flow: request the symbol's candles."""
        logger.info("Application started — loading %s", self._symbol)
        self._bus.publish(LoadSymbol(symbol=self._symbol, limit=self._limit))

    def on_window_rendered(self, _event: WindowRendered) -> None:
        """Terminal event: the chart is displayed and the app is running."""
        logger.info("Window rendered — application ready")
