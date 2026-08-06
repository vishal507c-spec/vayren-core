"""Bootstrap — the only place event subscriptions are created."""

from pathlib import Path
from typing import Any

from chart.engine.chart_engine import ChartEngine
from chart.events.chart_ready import ChartReady
from chart.events.window_rendered import WindowRendered
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.windows.chart_window import ChartWindow
from core.event_bus.event_bus import EventBus
from core.events.app_started import AppStarted
from core.registry.registry import Registry
from market.database.sqlite import SqliteCandleDatabase
from market.events.data_loaded import DataLoaded
from market.events.load_symbol import LoadSymbol
from market.loader.market_data_loader import MarketDataLoader
from market.repository.candle_repository import CandleRepository

from app.lifecycle.lifecycle import AppLifecycle


class Bootstrap:
    """Constructs the bus, services and registry; wires every subscription.

    This is the single wiring point of the application and the ONLY place
    EventBus.subscribe may be called. Modules never subscribe themselves.
    """

    def __init__(self, database_path: str | Path, symbol: str, limit: int = 5000) -> None:
        self._bus = EventBus()
        self._services: Registry[Any] = Registry()

        database = SqliteCandleDatabase(database_path)
        repository = CandleRepository(database)
        loader = MarketDataLoader(repository, self._bus)
        engine = ChartEngine(self._bus)
        widget = CandleChartWidget()
        window = ChartWindow(widget, self._bus)
        lifecycle = AppLifecycle(self._bus, symbol, limit)

        self._register_services(database, repository, loader, engine, window, lifecycle)
        self._wire_events(loader, engine, window, lifecycle)
        self._database = database

    def _register_services(
        self,
        database: SqliteCandleDatabase,
        repository: CandleRepository,
        loader: MarketDataLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
    ) -> None:
        self._services.register("sqlite_database", database)
        self._services.register("candle_repository", repository)
        self._services.register("market_data_loader", loader)
        self._services.register("chart_engine", engine)
        self._services.register("chart_window", window)
        self._services.register("app_lifecycle", lifecycle)

    def _wire_events(
        self,
        loader: MarketDataLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
    ) -> None:
        self._bus.subscribe(AppStarted, lifecycle.on_app_started)
        self._bus.subscribe(LoadSymbol, loader.on_load_symbol)
        self._bus.subscribe(DataLoaded, engine.on_data_loaded)
        self._bus.subscribe(ChartReady, window.on_chart_ready)
        self._bus.subscribe(WindowRendered, lifecycle.on_window_rendered)

    def start(self) -> None:
        """Connect the database and start the application flow."""
        self._database.connect()
        self._bus.publish(AppStarted())

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def services(self) -> Registry[Any]:
        return self._services
