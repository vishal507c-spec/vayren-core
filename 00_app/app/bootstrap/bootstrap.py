"""Bootstrap — the only place event subscriptions are created."""

from pathlib import Path
from typing import Any

from chart.engine.chart_engine import ChartEngine
from chart.events.chart_ready import ChartReady
from chart.events.window_rendered import WindowRendered
from chart.manifest import chart_manifest
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.tools_toolbar import ChartToolsToolbar
from chart.widgets.watchlist_widget import WatchlistWidget
from chart.windows.chart_window import ChartWindow
from core.event_bus.event_bus import EventBus
from core.events.app_started import AppStarted
from core.registry.component_registry import ComponentRegistry
from core.registry.registry import Registry
from core.system.system_model import SystemModel
from data import (
    CancelDownload,
    CoverageRequest,
    DownloadCompleted,
    DownloadCoverage,
    DownloadFailed,
    DownloadProgress,
    DownloadRequest,
    DownloadSettings,
    DownloadStarted,
    DownloadWorker,
    HistoricalDownloadEngine,
    data_manifest,
)
from data.provider.factory import build_provider
from data.provider.manager import ProviderCredentialsManager
from data.ui.historical_panel import HistoricalDownloadPanel
from market.events.data_loaded import DataLoaded
from market.events.list_symbols import ListSymbols
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.quotes_loaded import QuotesLoaded
from market.events.symbols_listed import SymbolsListed
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from market.loader.market_data_loader import MarketDataLoader
from market.loader.quote_loader import QuoteLoader
from market.loader.symbol_list_loader import SymbolListLoader
from market.loader.timeframe_list_loader import TimeframeListLoader
from market.manifest import market_manifest
from market.repository.symbol_repository import SymbolRepository
from PySide6.QtWidgets import QApplication

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
        quote_loader = QuoteLoader(repository, self._bus)
        timeframe_loader = TimeframeListLoader(repository, self._bus)
        engine = ChartEngine(self._bus)
        widget = CandleChartWidget()
        watchlist = WatchlistWidget()
        toolbar = TimeframeToolbar()
        tools = ChartToolsToolbar()
        data_window = HistoricalDownloadPanel(self._bus)
        window = ChartWindow(
            widget,
            watchlist,
            toolbar,
            tools,
            self._bus,
            limit=limit,
            download=data_window,
        )
        lifecycle = AppLifecycle(self._bus)

        data_settings = DownloadSettings(data_dir=data_dir)
        data_provider = build_provider(data_settings)
        data_credentials = ProviderCredentialsManager(data_settings, data_provider)
        data_engine = HistoricalDownloadEngine(data_settings, provider=data_provider)
        data_worker = DownloadWorker(data_engine)
        data_engine.set_reporter(data_worker)
        data_window.set_credentials_manager(data_credentials)

        self._register_services(
            repository,
            loader,
            symbol_loader,
            quote_loader,
            timeframe_loader,
            engine,
            window,
            lifecycle,
            data_engine,
            data_worker,
            data_window,
        )
        self._wire_events(
            loader,
            symbol_loader,
            quote_loader,
            timeframe_loader,
            engine,
            window,
            lifecycle,
            data_worker,
            data_window,
        )
        self._build_architecture(repository, engine, data_engine)

    def _build_architecture(
        self,
        repository: SymbolRepository,
        engine: ChartEngine,
        data_engine: HistoricalDownloadEngine,
    ) -> None:
        """Register the real components and build the system model.

        Runs once at startup. The existing runtime is untouched: the real
        service instances remain the implementations, and the old Registry
        keeps its names. This model is metadata for discovery and AI context.
        """
        self._components = ComponentRegistry()
        self._components.register(
            market_manifest(),
            implementations={
                "data.query.candles": repository,
                "data.query.timeframes": repository,
                "data.query.quotes": repository,
                "data.transform.aggregate": repository,
            },
        )
        self._components.register(chart_manifest(), implementations={"chart.render": engine})
        self._components.register(
            data_manifest(),
            implementations={
                "historical_data.download": data_engine,
                "historical_data.coverage": data_engine,
                "historical_data.status": data_engine,
            },
        )
        self._system_model = SystemModel(self._components)

    def _register_services(
        self,
        repository: SymbolRepository,
        loader: MarketDataLoader,
        symbol_loader: SymbolListLoader,
        quote_loader: QuoteLoader,
        timeframe_loader: TimeframeListLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
        data_engine: HistoricalDownloadEngine,
        data_worker: DownloadWorker,
        data_window: HistoricalDownloadPanel,
    ) -> None:
        self._services.register("symbol_repository", repository)
        self._services.register("market_data_loader", loader)
        self._services.register("symbol_list_loader", symbol_loader)
        self._services.register("quote_loader", quote_loader)
        self._services.register("timeframe_list_loader", timeframe_loader)
        self._services.register("chart_engine", engine)
        self._services.register("chart_window", window)
        self._services.register("app_lifecycle", lifecycle)
        self._services.register("data_engine", data_engine)
        self._services.register("data_worker", data_worker)
        self._services.register("data_window", data_window)

    def _wire_events(
        self,
        loader: MarketDataLoader,
        symbol_loader: SymbolListLoader,
        quote_loader: QuoteLoader,
        timeframe_loader: TimeframeListLoader,
        engine: ChartEngine,
        window: ChartWindow,
        lifecycle: AppLifecycle,
        data_worker: DownloadWorker,
        data_window: HistoricalDownloadPanel,
    ) -> None:
        self._bus.subscribe(AppStarted, lifecycle.on_app_started)
        self._bus.subscribe(ListSymbols, symbol_loader.on_list_symbols)
        self._bus.subscribe(SymbolsListed, window.on_symbols_listed)
        self._bus.subscribe(SymbolsListed, quote_loader.on_symbols_listed)
        self._bus.subscribe(SymbolsListed, data_window.on_symbols_listed)
        self._bus.subscribe(QuotesLoaded, window.on_quotes_loaded)
        self._bus.subscribe(LoadSymbol, loader.on_load_symbol)
        self._bus.subscribe(TimeframeChanged, loader.on_timeframe_changed)
        self._bus.subscribe(ListTimeframes, timeframe_loader.on_list_timeframes)
        self._bus.subscribe(TimeframesListed, window.on_timeframes_listed)
        self._bus.subscribe(DataLoaded, engine.on_data_loaded)
        self._bus.subscribe(ChartReady, window.on_chart_ready)
        self._bus.subscribe(WindowRendered, lifecycle.on_window_rendered)
        self._bus.subscribe(DownloadRequest, data_worker.on_download_request)
        self._bus.subscribe(CoverageRequest, data_worker.on_coverage_request)
        self._bus.subscribe(CancelDownload, data_worker.on_cancel_download)
        self._bus.subscribe(DownloadStarted, data_window.on_download_started)
        self._bus.subscribe(DownloadProgress, data_window.on_download_progress)
        self._bus.subscribe(DownloadCompleted, data_window.on_download_completed)
        self._bus.subscribe(DownloadFailed, data_window.on_download_failed)
        self._bus.subscribe(DownloadCoverage, data_window.on_download_coverage)
        data_worker.started.connect(self._bus.publish)
        data_worker.progress.connect(self._bus.publish)
        data_worker.completed.connect(self._bus.publish)
        data_worker.failed.connect(self._bus.publish)
        data_worker.coverage.connect(self._bus.publish)
        data_worker.log.connect(data_window.on_log_line)
        data_window.close_requested.connect(lambda: window.toggle_panel("download"))
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.aboutToQuit.connect(data_worker.shutdown)

    def start(self) -> None:
        """Show the main window and publish AppStarted; the flow continues event-driven."""
        data_window: HistoricalDownloadPanel = self._services.get("data_window")
        data_engine: HistoricalDownloadEngine = self._services.get("data_engine")
        ready, reason = data_engine.provider_available()
        data_window.set_provider(ready, reason)
        self._services.get("chart_window").show()
        self._bus.publish(AppStarted())

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def services(self) -> Registry[Any]:
        return self._services

    @property
    def components(self) -> ComponentRegistry:
        """The registered components with their capabilities (new lookup)."""
        return self._components

    @property
    def system_model(self) -> SystemModel:
        """The in-memory architecture model of the running system."""
        return self._system_model
