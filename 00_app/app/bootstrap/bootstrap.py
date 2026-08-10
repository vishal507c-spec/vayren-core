"""Bootstrap — the only place event subscriptions are created."""

from pathlib import Path
from typing import Any

from chart.engine.chart_engine import ChartEngine
from chart.events.chart_ready import ChartReady
from chart.events.window_rendered import WindowRendered
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.symbol_list_widget import SymbolListWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.windows.chart_window import ChartWindow
from core.event_bus.event_bus import EventBus
from core.events.app_started import AppStarted
from core.registry.registry import Registry
from market.events.data_loaded import DataLoaded
from market.events.list_symbols import ListSymbols
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.symbols_listed import SymbolsListed
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from market.loader.market_data_loader import MarketDataLoader
from market.loader.symbol_list_loader import SymbolListLoader
from market.loader.timeframe_list_loader import TimeframeListLoader
from market.repository.symbol_repository import SymbolRepository

from app.lifecycle.lifecycle import AppLifecycle


class Bootstrap:
    """Constructs the bus, services and registry; wires every subscription.

    This is the single wiring point of the application and the ONLY place
    EventBus.subscribe may be called. Modules never subscribe themselves.
    """

    def __init__(self, data_dir: str | Path, limit: int | None = None) -> None:
        self._bus = EventBus()
        self._services: Registry[Any] = Registry()

        repository = SymbolRepository(data_dir)
        loader = MarketDataLoader(repository, self._bus)
        symbol_loader = SymbolListLoader(repository, self._bus)
        timeframe_loader = TimeframeListLoader(repository, self._bus)
        engine = ChartEngine(self._bus)
        widget = CandleChartWidget()
        sidebar = SymbolListWidget()
        toolbar = TimeframeToolbar()
        window = ChartWindow(widget, sidebar, toolbar, self._bus, limit=limit)
        lifecycle = AppLifecycle(self._bus)

        self._register_services(
            repository, loader, symbol_loader, timeframe_loader, engine, window, lifecycle
        )
        self._wire_events(loader, symbol_loader, timeframe_loader, engine, window, lifecycle)

    def _register_services(
        self,
        repository: SymbolRepository,
        loader: MarketDataLoader,
        symbol_loader: SymbolListLoader,
        timeframe_loader: TimeframeListLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
    ) -> None:
        self._services.register("symbol_repository", repository)
        self._services.register("market_data_loader", loader)
        self._services.register("symbol_list_loader", symbol_loader)
        self._services.register("timeframe_list_loader", timeframe_loader)
        self._services.register("chart_engine", engine)
        self._services.register("chart_window", window)
        self._services.register("app_lifecycle", lifecycle)

    def _wire_events(
        self,
        loader: MarketDataLoader,
        symbol_loader: SymbolListLoader,
        timeframe_loader: TimeframeListLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
    ) -> None:
        self._bus.subscribe(AppStarted, lifecycle.on_app_started)
        self._bus.subscribe(ListSymbols, symbol_loader.on_list_symbols)
        self._bus.subscribe(SymbolsListed, window.on_symbols_listed)
        self._bus.subscribe(LoadSymbol, loader.on_load_symbol)
        self._bus.subscribe(TimeframeChanged, loader.on_timeframe_changed)
        self._bus.subscribe(ListTimeframes, timeframe_loader.on_list_timeframes)
        self._bus.subscribe(TimeframesListed, window.on_timeframes_listed)
        self._bus.subscribe(DataLoaded, engine.on_data_loaded)
        self._bus.subscribe(ChartReady, window.on_chart_ready)
        self._bus.subscribe(WindowRendered, lifecycle.on_window_rendered)

    def start(self) -> None:
        """Show the main window and publish AppStarted; the flow continues event-driven."""
        self._services.get("chart_window").show()
        self._bus.publish(AppStarted())

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def services(self) -> Registry[Any]:
        return self._services
