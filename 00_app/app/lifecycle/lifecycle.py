"""AppLifecycle — application startup and terminal flow."""

from logging import getLogger

from chart.events.window_rendered import WindowRendered
from core.event_bus.event_bus import EventBus
from core.events.app_started import AppStarted
from market.events.list_symbols import ListSymbols

logger = getLogger(__name__)


class AppLifecycle:
    """Owns the application flow: scans symbols on AppStarted, closes on WindowRendered."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def on_app_started(self, _event: AppStarted) -> None:
        """Begin Phase 2 flow: request the list of available symbols."""
        logger.info("Application started — scanning data directory")
        self._bus.publish(ListSymbols())

    def on_window_rendered(self, _event: WindowRendered) -> None:
        """Terminal event: the chart is displayed and the app is running."""
        logger.info("Window rendered — application ready")
