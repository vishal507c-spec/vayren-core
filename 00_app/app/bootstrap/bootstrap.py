"""Bootstrap — the only place event subscriptions are created."""

import contextlib
import uuid
from logging import getLogger
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

logger = getLogger(__name__)


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

        # ── Strategy Lab platform — VM-only (no Python builtins) ──
        from backtest import BacktestRunner, BacktestWorker  # noqa: I001
        from backtest.ui.overlay import TradeOverlay
        from strategy.registry import StrategyRegistry

        # legacy panels kept for service registry compat but not shown in market
        from strategy.ui.control_panel import StrategyControlPanel
        from strategy.ui.strategy_list_panel import StrategyListPanel

        from app.ui.event_log_panel import EventLogPanel
        from app.ui.market_status_panel import MarketStatusPanel
        from app.ui.strategy_lab_workspace import StrategyLabWorkspace
        from app.ui.system_health_panel import SystemHealthPanel
        from app.ui.top_nav_bar import TopNavBar

        strategy_registry = StrategyRegistry()

        # Strategy Library is user-owned — do NOT auto-create OBR/SMA files.
        # ``ensure_builtin_strategies`` is now a deprecated no-op kept only
        # for backward compat; no seeding on VAYREN START.

        # BacktestRunner is Python-only: .py Strategy -> PythonStrategy -> Signal
        # canonical strategy source D:\VAYREN_STRATEGIES  # noqa: E501
        backtest_runner = BacktestRunner(
            repository, registry=strategy_registry, data_dir=r"D:\VAYREN_STRATEGIES"
        )
        backtest_worker = BacktestWorker(backtest_runner)
        trade_overlay = TradeOverlay()
        widget.set_overlay(trade_overlay)
        # Plot overlay for generic chart series (TradingView plot() primitive) — compact, no OBR-specific
        try:
            from chart.renderer.plot_renderer import PlotOverlay

            plot_overlay = PlotOverlay()
            plot_overlay.set_visibility_checker(widget.is_indicator_visible)  # type: ignore[attr-defined]
            # Single overlay handles all plot series; per-series visibility checked inside
            # Store under generic key, visibility per series title/strategy handled internally
            widget.set_named_overlay("PLOT", plot_overlay)
        except Exception:
            plot_overlay = None  # type: ignore[assignment]

        # legacy controls (kept for service list, not injected into market)
        lab_control = StrategyControlPanel()
        lab_list = StrategyListPanel()
        # dedicated single-strategy lab workspace (isolated from market)
        lab_workspace = StrategyLabWorkspace()
        from backtest.ui.performance_panel import PerformancePanel

        performance_panel = PerformancePanel()
        # Phase 3: generic — sync params from currently selected strategy, fallback to first available  # noqa: E501
        try:
            current_name = ""
            try:  # noqa: SIM105
                current_name = lab_workspace.current_tab_name().strip()  # type: ignore[attr-defined]
            except Exception:
                pass
            if not current_name:
                try:  # noqa: SIM105
                    current_name = lab_workspace.left_nav.current_name() or ""  # type: ignore[attr-defined]
                except Exception:
                    pass
            target_id = (current_name or "").lower().replace(" ", "-") if current_name else ""
            obr_def = None
            if target_id:
                with contextlib.suppress(Exception):
                    obr_def = strategy_registry.get(target_id)
            if obr_def is None:
                try:
                    defs = strategy_registry.list()
                    if defs:
                        obr_def = defs[0]
                except Exception:
                    pass
            if obr_def is not None:
                lab_workspace.center_detail.set_params(dict(obr_def.params))
        except Exception:
            pass

        nav_bar = TopNavBar()
        market_status = MarketStatusPanel()
        event_log = EventLogPanel()
        system_health = SystemHealthPanel(data_dir=data_dir)

        window = ChartWindow(
            widget,
            watchlist,
            toolbar,
            tools,
            self._bus,
            limit=limit,
            download=data_window,
            nav=nav_bar,
            left_extra=market_status,
            lab_workspace=lab_workspace,
            event_log=event_log,
            system_health=system_health,
        )
        lifecycle = AppLifecycle(self._bus)

        # ── Chart session persistence (TradingView exact restore) ──
        from chart.session.session_store import ChartSessionStore

        chart_session_store = ChartSessionStore()
        _initial_chart_session = chart_session_store.load()
        # pre-seed window with saved symbol/timeframe so on_symbols_listed can restore
        # (validated later against actual symbol list)
        try:
            if _initial_chart_session.symbol:
                window._current_symbol = _initial_chart_session.symbol  # type: ignore[attr-defined]
            if _initial_chart_session.timeframe:
                window._current_timeframe = _initial_chart_session.timeframe  # type: ignore[attr-defined]
        except Exception:
            pass
        # restore active indicators (name, visible, settings)
        try:
            for ind in _initial_chart_session.indicators:
                with contextlib.suppress(Exception):
                    widget.add_indicator(ind.name)
                    # restore visibility
                    if not ind.visible:
                        widget.set_indicator_visible(ind.name, False)
                    # restore settings if any (e.g., OBR params) — keep for future
                    if ind.settings:
                        # store settings on widget for later use (not affecting calculation)
                        with contextlib.suppress(Exception):
                            if not hasattr(widget, "_indicator_settings"):
                                widget._indicator_settings = {}  # type: ignore[attr-defined]
                            widget._indicator_settings[ind.name] = dict(ind.settings)  # type: ignore[attr-defined]
        except Exception:
            pass

        # ── auto-save helper (symbol/timeframe/indicator add-remove/visibility/settings/layout) ──
        def _save_chart_session(*_a: Any, **_kw: Any) -> None:
            try:
                from chart.session.session_store import ChartSession, IndicatorState

                # collect active indicators — only those with a row (TradingView active)
                inds: list[IndicatorState] = []
                try:
                    for rname in list(widget.visibility_panel.indicators):  # type: ignore[attr-defined]
                        vis = widget.is_indicator_visible(rname)  # type: ignore[attr-defined]
                        settings: dict[str, Any] = {}
                        try:
                            if hasattr(widget, "_indicator_settings"):
                                settings = dict(widget._indicator_settings.get(rname, {}))  # type: ignore[attr-defined]
                        except Exception:
                            settings = {}
                        inds.append(IndicatorState(name=rname, visible=vis, settings=settings))
                except Exception:
                    inds = []
                # chart layout stub (future)
                layout: dict[str, Any] = {}
                with contextlib.suppress(Exception):
                    layout["follow_latest"] = bool(
                        getattr(widget, "_follow_latest", True)
                    )
                sess = ChartSession(
                    symbol=getattr(window, "_current_symbol", None),
                    timeframe=getattr(window, "_current_timeframe", None),
                    indicators=inds,
                    chart_layout=layout,
                )
                chart_session_store.save(sess)
            except Exception:
                pass

        # connect auto-save to all relevant signals (symbol/timeframe/indicator)
        with contextlib.suppress(Exception):
            window.session_changed.connect(_save_chart_session)
        with contextlib.suppress(Exception):
            widget.session_changed.connect(_save_chart_session)  # type: ignore[attr-defined]
        # also direct panel signals as fallback (widget already emits via session_changed)
        with contextlib.suppress(Exception):
            widget.visibility_panel.visibility_changed.connect(
                lambda *_a: _save_chart_session()  # type: ignore[attr-defined]
            )
            widget.visibility_panel.indicator_removed.connect(
                lambda *_a: _save_chart_session()  # type: ignore[attr-defined]
            )
        # ensure we save on close/crash via aboutToQuit
        with contextlib.suppress(Exception):
            app_inst = QApplication.instance()
            if isinstance(app_inst, QApplication):
                app_inst.aboutToQuit.connect(_save_chart_session)

        data_settings = DownloadSettings(data_dir=data_dir)
        data_provider = build_provider(data_settings)
        data_credentials = ProviderCredentialsManager(data_settings, data_provider)
        data_engine = HistoricalDownloadEngine(data_settings, provider=data_provider)
        data_worker = DownloadWorker(data_engine)
        data_engine.set_reporter(data_worker)
        data_window.set_credentials_manager(data_credentials)

        # dynamic attrs for validation bookkeeping
        self._last_train_result: object | None = None
        self._last_test_cfg: object | None = None
        # keep references for closures
        self._strategy_registry = strategy_registry
        self._lab_control = lab_control
        self._lab_list = lab_list
        self._lab_workspace = lab_workspace
        self._trade_overlay = trade_overlay
        try:
            self._plot_overlay = plot_overlay  # type: ignore[name-defined]
        except Exception:
            self._plot_overlay = None  # type: ignore[attr-defined]
        self._performance_panel = performance_panel
        self._market_status = market_status
        self._event_log = event_log
        self._system_health = system_health
        self._nav_bar = nav_bar
        self._widget = widget

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
            strategy_registry,
            backtest_runner,
            backtest_worker,
            trade_overlay,
            lab_control,
            lab_list,
            lab_workspace,
            performance_panel,
            nav_bar,
            market_status,
            event_log,
            system_health,
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
            strategy_registry,
            backtest_runner,
            backtest_worker,
            trade_overlay,
            lab_control,
            lab_list,
            lab_workspace,
            performance_panel,
            nav_bar,
            market_status,
            event_log,
            system_health,
            widget,
        )
        self._build_architecture(
            repository, engine, data_engine, strategy_registry, backtest_runner
        )

    def _build_architecture(
        self,
        repository: SymbolRepository,
        engine: ChartEngine,
        data_engine: HistoricalDownloadEngine,
        strategy_registry: Any,
        backtest_runner: Any,
    ) -> None:
        """Register the real components and build the system model."""
        from backtest.manifest import backtest_manifest
        from strategy.manifest import strategy_manifest

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
        self._components.register(
            strategy_manifest(),
            implementations={
                "strategy.registry": strategy_registry,
                "strategy.runtime": strategy_registry,
            },
        )
        self._components.register(
            backtest_manifest(),
            implementations={
                "backtest.run": backtest_runner,
                "backtest.metrics": backtest_runner,
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
        strategy_registry: Any,
        backtest_runner: Any,
        backtest_worker: Any,
        trade_overlay: Any,
        lab_control: Any,
        lab_list: Any,
        lab_workspace: Any,
        performance_panel: Any,
        nav_bar: Any,
        market_status: Any,
        event_log: Any,
        system_health: Any,
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
        self._services.register("strategy_registry", strategy_registry)
        self._services.register("backtest_runner", backtest_runner)
        self._services.register("backtest_worker", backtest_worker)
        self._services.register("trade_overlay", trade_overlay)
        self._services.register("lab_control_panel", lab_control)
        self._services.register("lab_strategy_list", lab_list)
        self._services.register("lab_workspace", lab_workspace)
        self._services.register("lab_performance_panel", performance_panel)
        self._services.register("top_nav_bar", nav_bar)
        self._services.register("market_status_panel", market_status)
        self._services.register("event_log_panel", event_log)
        self._services.register("system_health_panel", system_health)

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
        strategy_registry: Any,
        backtest_runner: Any,  # noqa: ARG002
        backtest_worker: Any,
        trade_overlay: Any,
        lab_control: Any,
        lab_list: Any,
        lab_workspace: Any,
        performance_panel: Any,
        nav_bar: Any,
        market_status: Any,
        event_log: Any,
        system_health: Any,
        widget: Any,
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
        # ── TradingView: keep active indicators alive across symbol/timeframe, recalculate ──
        def _recalc_active_indicators(event: ChartReady) -> None:  # type: ignore[no-untyped-def]
            try:
                # only recalc if there are active indicators (panel has rows)
                active = []
                try:
                    active = list(widget.visibility_panel.indicators)  # type: ignore[attr-defined]
                except Exception:
                    active = []
                if not active:
                    return
                # for each active strategy indicator, rerun with new symbol/timeframe
                # keep settings/visibility/style intact, just change data source
                for ind_name in active:
                    # check if this indicator is a strategy (in STRATEGIES list)
                    is_strategy = False
                    try:
                        from strategy.language.storage import list_strategies

                        strats = list_strategies(r"D:\VAYREN_STRATEGIES")
                        if ind_name in strats or ind_name.lower() == "obr":
                            is_strategy = True
                        # also check registry
                        if not is_strategy:
                            try:
                                # strategy ids are lower-kebab
                                sid = ind_name.lower().replace(" ", "-")
                                if strategy_registry.contains(sid):
                                    is_strategy = True
                            except Exception:
                                pass
                    except Exception:
                        is_strategy = ind_name.lower() == "obr"
                    if not is_strategy:
                        # pure indicator (VWAP, EMA, etc.) — no backtest, just keep row
                        # recalculation will happen via its own renderer on next paint
                        continue
                    # strategy: rerun backtest with new symbol/timeframe, preserve settings
                    try:
                        from strategy.language.storage import load_strategy_record

                        rec = load_strategy_record(ind_name, r"D:\VAYREN_STRATEGIES")
                        if rec is None and ind_name.lower() == "obr":
                            # fallback for OBR
                            rec = load_strategy_record("OBR", r"D:\VAYREN_STRATEGIES")
                        if rec is None:
                            continue
                        strategy_id = rec.id
                        symbol = event.model.symbol
                        timeframe = event.model.timeframe
                        # use current chart's bar range for backtest period
                        try:
                            bars = event.model.bars
                            start_date = bars[0].timestamp[:10] if bars else "2024-01-01"
                            end_date = bars[-1].timestamp[:10] if bars else "2026-12-31"
                        except Exception:
                            start_date = "2024-01-01"
                            end_date = "2026-12-31"
                        from backtest.events import RunBacktest

                        self._bus.publish(
                            RunBacktest(
                                request_id=uuid.uuid4().hex[:8],
                                strategy_ids=(strategy_id,),
                                symbol=symbol,  # type: ignore[arg-type]
                                timeframe=timeframe,
                                start_date=start_date,
                                end_date=end_date,
                            )
                        )
                    except Exception:
                        continue
            except Exception:
                pass

        self._bus.subscribe(ChartReady, _recalc_active_indicators)
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
        # ── Strategy Lab wiring ──
        self._wire_lab(
            strategy_registry,
            backtest_worker,
            trade_overlay,
            lab_control,
            lab_list,
            lab_workspace,
            performance_panel,
            nav_bar,
            market_status,
            event_log,
            system_health,
            window,
            widget,
        )
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.aboutToQuit.connect(data_worker.shutdown)
            app.aboutToQuit.connect(backtest_worker.shutdown)
            if hasattr(system_health, "stop"):
                app.aboutToQuit.connect(system_health.stop)

    def _wire_lab(
        self,
        strategy_registry: Any,
        backtest_worker: Any,
        trade_overlay: Any,
        lab_control: Any,
        lab_list: Any,
        lab_workspace: Any,
        performance_panel: Any,
        nav_bar: Any,
        market_status: Any,
        event_log: Any,
        system_health: Any,
        window: ChartWindow,
        widget: Any,
    ) -> None:
        from backtest.events import (
            BacktestCompleted,
            BacktestFailed,
            BacktestProgress,
            BacktestStarted,
            RunBacktest,
        )
        from strategy.events import LabReset, PaperTradeRequested
        from strategy.models.form import StrategyDraft

        # ── nav bar ──
        nav_bar.market_clicked.connect(window.show_market)
        if hasattr(nav_bar, "strategy_lab_clicked"):
            nav_bar.strategy_lab_clicked.connect(window.show_lab)
        nav_bar.system_clicked.connect(window.toggle_bottom)

        # ── control ↔ bus ──
        def _on_backtest_form(form: Any) -> None:
            from backtest.validation import validate_backtest_form

            symbol = window.current_symbol
            timeframe = form.timeframe
            enabled_ids = {d.id for d in strategy_registry.enabled()}
            errors = validate_backtest_form(form, symbol, enabled_ids)
            if errors:
                for err in errors:
                    event_log.add_entry("WARN", f"Validation failed: {err}")
                return
            request_id = uuid.uuid4().hex[:8]
            self._bus.publish(
                RunBacktest(
                    request_id=request_id,
                    strategy_ids=(form.strategy_id,),
                    symbol=symbol,  # type: ignore[arg-type]
                    timeframe=timeframe,
                    start_date=form.start_date,
                    end_date=form.end_date,
                    initial_capital=form.initial_capital,
                    slippage_pct=form.slippage_pct,
                    commission_pct=form.commission_pct,
                )
            )

        lab_control.backtest_requested.connect(_on_backtest_form)
        lab_control.paper_trade_requested.connect(
            lambda sid: (
                self._bus.publish(
                    PaperTradeRequested(
                        strategy_id=sid,
                        symbol=window.current_symbol or "",
                        timeframe=window.current_timeframe or "",
                    )
                ),
                event_log.add_entry(
                    "WARN", "Paper trading requires execution engine (not yet available)"
                ),
            )
        )
        lab_control.reset_requested.connect(lambda: self._bus.publish(LabReset()))

        # ── strategy list ↔ registry ──
        def _refresh_strategies() -> None:
            from strategy.events import StrategiesListed

            strats = strategy_registry.list()
            event = StrategiesListed(strategies=strats)
            self._bus.publish(event)
            lab_control.set_strategies(tuple(s for s in strats if s.enabled) or strats)
            lab_list.set_strategies(strats)
            system_health.set_engine_state("Strategy Engine", f"{len(strats)} strategies")

        def _on_strategy_toggled(sid: str, enabled: bool) -> None:
            try:
                strategy_registry.set_enabled(sid, enabled)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy {'enabled' if enabled else 'disabled'}: {sid}")
            _refresh_strategies()

        def _on_allocation(sid: str, pct: float) -> None:
            try:
                strategy_registry.set_allocation(sid, pct)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
            _refresh_strategies()

        def _on_duplicate(sid: str) -> None:
            try:
                strategy_registry.duplicate(sid)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy duplicated: {sid}")
            _refresh_strategies()

        def _on_remove(sid: str) -> None:
            try:
                strategy_registry.remove(sid)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy removed: {sid}")
            _refresh_strategies()

        def _on_add() -> None:
            from strategy.ui.strategy_dialog import StrategyDialog

            kinds = strategy_registry.kinds
            if not kinds:
                event_log.add_entry("WARN", "No strategy kinds registered")
                return
            specs_map = {k: strategy_registry.param_specs(k) for k in kinds}
            dialog = StrategyDialog(kinds=kinds, specs_map=specs_map, parent=window)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            draft: StrategyDraft | None = dialog.draft()
            if draft is None:
                event_log.add_entry("WARN", "Validation failed: check strategy form fields")
                return
            self._create_strategy_from_draft(
                draft, strategy_registry, event_log, _refresh_strategies
            )

        def _on_configure(sid: str) -> None:
            from strategy.models.parameters import StrategyParameters
            from strategy.ui.strategy_dialog import StrategyDialog

            try:
                definition = strategy_registry.get(sid)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            kinds = strategy_registry.kinds
            specs_map = {k: strategy_registry.param_specs(k) for k in kinds}
            dialog = StrategyDialog(
                kinds=kinds, specs_map=specs_map, definition=definition, parent=window
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            draft = dialog.draft()
            if draft is None:
                event_log.add_entry("WARN", "Validation failed: check strategy form fields")
                return
            try:
                strategy_registry.set_params(sid, StrategyParameters(draft.params))
                strategy_registry.set_allocation(sid, draft.allocation_pct)
            except Exception as exc:  # noqa: BLE001
                event_log.add_entry("ERROR", str(exc))
                return
            event_log.add_entry("INFO", f"Strategy configured: {sid}")
            _refresh_strategies()

        lab_list.strategy_toggled.connect(_on_strategy_toggled)
        lab_list.allocation_changed.connect(_on_allocation)
        lab_list.duplicate_requested.connect(_on_duplicate)
        lab_list.remove_requested.connect(_on_remove)
        lab_list.configure_requested.connect(_on_configure)
        lab_list.add_requested.connect(_on_add)

        # performance → overlay highlight
        performance_panel.trade_selected.connect(trade_overlay.set_highlight)
        # ── Strategy Lab workspace (single-strategy) ──
        if lab_workspace is not None:
            # sync symbols/timeframes
            self._bus.subscribe(
                SymbolsListed, lambda e: lab_workspace.right_settings.set_symbols(e.symbols)
            )  # noqa: E501
            self._bus.subscribe(
                TimeframesListed,
                lambda e: lab_workspace.right_settings.set_timeframes(e.timeframes),
            )  # noqa: E501
            self._bus.subscribe(
                ChartReady,
                lambda e: lab_workspace.right_settings.select_timeframe(e.model.timeframe),
            )  # noqa: E501

            # Phase 3: generic — canonical is StrategyRecord.id (UUID)  # noqa: E501
            def _current_strategy_id() -> str:
                # Prefer resolving via Strategy Library (UUID)  # noqa: E501
                def _resolve_canonical(name: str, data_dir: str | None) -> str:
                    try:
                        from strategy.language.storage import (
                            load_strategy_record,
                        )

                        rec = load_strategy_record(name, data_dir)
                        if rec is not None:
                            return rec.id
                    except Exception:
                        pass
                    # Backward compat fallback for builtins / registry-kinds (slug)
                    return name.lower().replace(" ", "-")

                # Need data_dir for library lookup
                try:
                    _d = _strategy_data_dir()  # type: ignore[has-type]
                except Exception:
                    _d = None
                try:
                    name = lab_workspace.current_tab_name().strip()  # type: ignore[attr-defined]
                    if name:
                        return _resolve_canonical(name, _d)
                except Exception:
                    pass
                try:
                    name = lab_workspace.left_nav.current_name()  # type: ignore[attr-defined]
                    if name:
                        return _resolve_canonical(str(name), _d)
                except Exception:
                    pass
                try:
                    defs = strategy_registry.list()
                    if defs:
                        return defs[0].id
                except Exception:
                    pass
                return ""

            def _on_workspace_param(key: str, value: float) -> None:
                sid = _current_strategy_id()
                try:
                    cur = strategy_registry.get(sid)
                except Exception:
                    # Generic fallback: first available strategy
                    try:
                        defs = strategy_registry.list()
                        if defs:
                            cur = defs[0]
                            sid = cur.id
                        else:
                            raise
                    except Exception as exc:  # noqa: BLE001
                        event_log.add_entry("ERROR", str(exc))
                        return
                try:
                    new_params = dict(cur.params)
                    new_params[key] = float(value)
                    from strategy.models.parameters import StrategyParameters

                    strategy_registry.set_params(sid, StrategyParameters(new_params))
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            lab_workspace.param_changed.connect(_on_workspace_param)

            # workspace run → generic: currently selected strategy
            def _on_workspace_run(cfg: Any) -> None:
                from backtest.validation import validate_backtest_form
                from strategy.models.form import BacktestForm

                sid = _current_strategy_id()
                # Generic fallback: first available strategy if selected not in registry
                if not strategy_registry.contains(sid):
                    try:
                        defs = strategy_registry.list()
                        if defs and not strategy_registry.contains(sid):
                            sid = defs[0].id
                    except Exception:
                        pass
                form = BacktestForm(
                    strategy_id=sid,
                    timeframe=cfg.get("timeframe", ""),
                    start_date=cfg.get("start_date", ""),
                    end_date=cfg.get("end_date", ""),
                    initial_capital=cfg.get("initial_capital", 1_000_000),
                    slippage_pct=cfg.get("slippage_pct", 0.02),
                    commission_pct=cfg.get("commission_pct", 0.03),
                )
                symbol = cfg.get("symbol") or window.current_symbol
                enabled_ids = {d.id for d in strategy_registry.enabled()}
                errors = validate_backtest_form(form, symbol, enabled_ids)
                if errors:
                    for err in errors:
                        event_log.add_entry("WARN", f"Validation failed: {err}")
                    return
                request_id = uuid.uuid4().hex[:8]
                self._bus.publish(
                    RunBacktest(
                        request_id=request_id,
                        strategy_ids=(sid,),
                        symbol=symbol,  # type: ignore[arg-type]
                        timeframe=form.timeframe,
                        start_date=form.start_date,
                        end_date=form.end_date,
                        initial_capital=form.initial_capital,
                        slippage_pct=form.slippage_pct,
                        commission_pct=form.commission_pct,
                    )
                )
                # store test period for validation
                self._last_test_cfg = cfg
                self._last_train_result = None

            lab_workspace.run_backtest.connect(_on_workspace_run)

            # trade focus → highlight + chart focus
            def _on_trade_focus(idx: int) -> None:
                trade_overlay.set_highlight(idx)
                # try to focus chart on trade bar
                try:
                    # find trade from last result
                    last = getattr(self, "_last_train_result", None)
                    if last is not None and hasattr(last, "trades") and 0 <= idx < len(last.trades):
                        _ = last.trades[idx].entry_index
                        widget.update()
                except Exception:
                    pass

            lab_workspace.trade_focus.connect(_on_trade_focus)
            lab_workspace.journal.trade_clicked.connect(_on_trade_focus)

            # ── code editor SAVE / COMPILE ──
            # Canonical per product requirement: UI ↔ D:\VAYREN_STRATEGIES
            # symbol_repository directory is for market data (D:\ZerodhaTradingData),
            # NOT for strategy source. Canonical is fixed.
            def _strategy_data_dir() -> str | None:
                return r"D:\VAYREN_STRATEGIES"

            def _refresh_my_strategies() -> None:
                try:
                    from strategy.language.storage import list_strategies_with_mtime

                    items = list_strategies_with_mtime(_strategy_data_dir())
                    lab_workspace.left_nav.set_strategies(items)  # type: ignore[attr-defined]
                except Exception:
                    pass
                # Keep INDICATORS → STRATEGIES in sync (single control, same storage)
                try:
                    from strategy.language.storage import list_strategies

                    names = tuple(sorted(list_strategies(r"D:\VAYREN_STRATEGIES")))
                    window.set_indicator_strategies(names)
                except Exception:
                    pass

            def _refresh_indicator_strategies() -> None:
                try:
                    from strategy.language.storage import list_strategies

                    names = tuple(sorted(list_strategies(r"D:\VAYREN_STRATEGIES")))
                    window.set_indicator_strategies(names)
                except Exception:
                    pass

            _refresh_indicator_strategies()

            def _on_chart_indicator_strategy(name: str) -> None:
                try:
                    from strategy.language.storage import load_strategy_record

                    rec = load_strategy_record(name, r"D:\VAYREN_STRATEGIES")
                    if rec is None:
                        event_log.add_entry("WARN", f"Strategy not found: {name}")
                        return
                    strategy_id = rec.id
                    symbol = window.current_symbol
                    timeframe = window.current_timeframe
                    if symbol is None or timeframe is None:
                        event_log.add_entry("WARN", f"No chart loaded — cannot run {name}")
                        return
                    start_date = "2024-01-01"
                    end_date = "2026-12-31"
                    try:
                        model = getattr(window._widget, "_model", None)  # type: ignore[attr-defined]
                        if model is not None and getattr(model, "bars", None):
                            bars = model.bars  # type: ignore[attr-defined]
                            if bars:
                                start_date = bars[0].timestamp[:10]  # type: ignore[index]
                                end_date = bars[-1].timestamp[:10]  # type: ignore[index]
                    except Exception:
                        pass
                    import uuid

                    from backtest.events import RunBacktest

                    self._bus.publish(
                        RunBacktest(
                            request_id=uuid.uuid4().hex[:8],
                            strategy_ids=(strategy_id,),
                            symbol=symbol,  # type: ignore[arg-type]
                            timeframe=timeframe,
                            start_date=start_date,
                            end_date=end_date,
                        )
                    )
                    event_log.add_entry("INFO", f"Indicator strategy run: {name}")
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            window.indicators.strategy_selected.connect(_on_chart_indicator_strategy)  # type: ignore[attr-defined]

            def _on_save(code: str) -> None:
                try:
                    import re as _re

                    from strategy.language.storage import (
                        list_strategies,
                        save_strategy,
                    )

                    data_dir = _strategy_data_dir()
                    current_id = lab_workspace.current_tab_name().strip()  # type: ignore[attr-defined]
                    existing = set(list_strategies(data_dir))
                    is_update = bool(current_id) and current_id in existing

                    if is_update:
                        target_name = current_id
                    else:
                        match = _re.search(r'strategy\s*\(\s*["\']([^"\']+)["\']', code)
                        candidate = match.group(1).strip() if match else ""
                        if candidate and candidate not in existing:
                            target_name = candidate
                        elif candidate and candidate in existing:
                            # Dedup: candidate exists, append counter
                            base = candidate
                            counter = 2
                            target_name = f"{base} {counter}"
                            while target_name in existing:
                                counter += 1
                                target_name = f"{base} {counter}"
                        else:
                            # Use draft name with dedup if needed
                            base = current_id or "Untitled Strategy"
                            if not base.strip():
                                base = "Untitled Strategy"
                            target_name = base
                            if target_name in existing:
                                counter = 2
                                candidate_name = f"{base} {counter}"
                                while candidate_name in existing:
                                    counter += 1
                                    candidate_name = f"{base} {counter}"
                                target_name = candidate_name

                    save_strategy(code, target_name, data_dir)
                    # Phase 5: immutable version (canonical = StrategyRecord.id)  # noqa: E501
                    try:
                        import hashlib

                        from strategy.language import compile_strategy
                        from strategy.language.storage import load_strategy_record
                        from strategy.version import (
                            DuplicateVersionError,
                            create_version,
                            list_versions,
                        )

                        rec = load_strategy_record(target_name, data_dir)
                        strategy_id = rec.id if rec else target_name
                        # Determine parent version (latest) for linear history
                        parent_id = None
                        try:
                            versions = list_versions(strategy_id, data_dir)
                            if versions:
                                parent_id = versions[-1].version_id
                        except Exception:
                            pass
                        # Build Python snapshot + hash + params
                        ir_snapshot: str | None = None
                        ir_hash = ""
                        ir_version = 1
                        params: dict[str, float] = {}
                        try:
                            compiled = compile_strategy(code)
                            ir_snapshot = compiled.code
                            ir_hash = hashlib.sha256(compiled.code.encode("utf-8")).hexdigest()
                            params = dict(compiled.param_defaults)
                        except Exception:
                            ir_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
                        try:
                            create_version(
                                strategy_id,
                                code,
                                ir_version=ir_version,
                                ir_hash=ir_hash,
                                ir_snapshot=ir_snapshot,
                                parameters=params,
                                parent_version_id=parent_id,
                                data_dir=data_dir,
                                metadata={"name": target_name},
                            )
                        except DuplicateVersionError as dup:
                            event_log.add_entry(
                                "INFO",
                                f"No change — version {dup.existing_version_id} already has identical source",  # noqa: E501
                            )
                        except Exception as ve:
                            # Graph or other version error — surface but don't break save
                            event_log.add_entry("WARN", f"Version not created: {ve}")
                    except Exception:
                        pass
                    event_log.add_entry("SUCCESS", f"Strategy saved: {target_name}")
                    lab_workspace.center_detail.mark_saved()  # type: ignore[attr-defined]
                    if not is_update and target_name != current_id:
                        lab_workspace.center_detail.rename_buffer(  # type: ignore[attr-defined]
                            current_id, target_name
                        )
                    lab_workspace.set_strategy_name(target_name)
                    _refresh_my_strategies()
                    lab_workspace.left_nav.select_name(target_name)  # type: ignore[attr-defined]
                    lab_workspace.center_detail.show_compile_result(  # type: ignore[attr-defined]
                        True, "✓ Saved"
                    )
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", f"Save failed: {exc}")
                    lab_workspace.center_detail.show_compile_result(False, str(exc))  # type: ignore[attr-defined]  # noqa: E501

            def _on_compile(code: str):
                try:
                    from strategy.language import compile_strategy

                    compiled = compile_strategy(code)
                    self._compiled_strategy = compiled  # type: ignore[attr-defined]

                    def _factory(params):  # type: ignore[no-untyped-def]
                        return compiled.create_logic(params)

                    # Phase 3: generic — register under currently selected strategy's kind
                    sid = _current_strategy_id()
                    kind = sid.replace("-", "_")
                    target_kind = kind
                    # Ensure kind exists — register if new, otherwise overwrite factory for live update  # noqa: E501
                    if strategy_registry.has_kind(target_kind):
                        strategy_registry._kinds[target_kind] = _factory  # type: ignore[attr-defined]
                    else:
                        # Register new kind for this user strategy (generic)
                        try:
                            from strategy.models.parameters import ParameterSpec

                            # Prefer declared specs (unique keys, real min/max) —
                            # param_defaults would duplicate label+key rows.
                            declared = tuple(getattr(compiled, "param_specs", ()) or ())
                            if declared:
                                specs = declared
                            else:
                                specs = tuple(
                                    ParameterSpec(
                                        key=k,
                                        label=k,
                                        default=float(v),
                                        minimum=0,
                                        maximum=1e9,
                                        decimals=2,
                                    )
                                    for k, v in compiled.param_defaults.items()
                                )
                            strategy_registry.register_kind(target_kind, _factory, specs)
                            # Also create a definition for backtest if missing
                            if not strategy_registry.contains(sid):
                                from strategy.models.definition import StrategyDefinition
                                from strategy.models.parameters import StrategyParameters

                                definition = StrategyDefinition(
                                    id=sid,
                                    name=lab_workspace.current_tab_name().strip() or sid,  # type: ignore[attr-defined]
                                    version="1.0",
                                    kind=target_kind,
                                    params=StrategyParameters(compiled.param_defaults),
                                )
                                with contextlib.suppress(Exception):
                                    strategy_registry.register_definition(definition)
                        except Exception:
                            strategy_registry._kinds[target_kind] = _factory  # type: ignore[attr-defined]
                    # also refresh params UI from compiled defaults
                    lab_workspace.center_detail._refresh_params_from_code(code)  # type: ignore[attr-defined]
                    # sync registry params to compiled defaults for current strategy
                    try:
                        from strategy.models.parameters import StrategyParameters

                        # Unique spec keys only (label keys would fail validation)
                        spec_keys = [s.key for s in getattr(compiled, "param_specs", ()) or ()]
                        if spec_keys:
                            new_params = {
                                k: float(compiled.param_defaults[k]) for k in spec_keys
                            }
                        else:
                            new_params = {k: float(v) for k, v in compiled.param_defaults.items()}
                        mapped = {}
                        for lk, lv in compiled.param_defaults.items():
                            lk_lower = lk.lower().replace(" ", "_").replace("-", "_")
                            if "c1" in lk_lower:
                                mapped["c1"] = lv
                            elif "c4" in lk_lower:
                                mapped["c4"] = lv
                            elif "rsi" in lk_lower:
                                mapped["rsi_threshold"] = lv
                            else:
                                mapped[lk] = lv
                        try:
                            strategy_registry.set_params(sid, StrategyParameters(mapped))
                        except Exception:
                            strategy_registry.set_params(sid, StrategyParameters(new_params))
                    except Exception:
                        pass
                    # Phase 3: show compile info (unique param count, warmup)
                    specs = tuple(getattr(compiled, "param_specs", ()) or ())
                    unique_keys = {getattr(s, "key", None) for s in specs} - {None}
                    param_count = len(unique_keys) if unique_keys else len(compiled.param_defaults)
                    try:
                        warmup = compiled.create_logic(
                            StrategyParameters(compiled.param_defaults)
                        ).warmup()
                    except Exception:
                        warmup = None
                    ir = getattr(compiled, "ir", None)
                    if ir is not None:
                        msg = (
                            f"✓ COMPILED (IR v{ir.ir_version} · "
                            f"{len(ir.parameters)} params · {len(ir.statements)} stmts · "
                            f"{len(ir.data_requirements)} data req)"
                        )
                    elif warmup is not None:
                        msg = f"✓ COMPILED — {param_count} params · warmup {warmup} · Python"
                    else:
                        msg = f"Strategy compiled successfully ({param_count} params)"  # noqa: E501
                    lab_workspace.center_detail.show_compile_result(True, msg)
                    event_log.add_entry("SUCCESS", msg)
                except Exception as exc:  # noqa: BLE001
                    from strategy.language.compiler import StrategyLanguageError

                    if isinstance(exc, StrategyLanguageError) and exc.errors:
                        e = exc.errors[0]
                        if isinstance(e, str):
                            msg = e
                            lab_workspace.center_detail.show_compile_result(False, msg)
                            event_log.add_entry("ERROR", msg)
                        else:
                            msg = f"Line {e.line}, Col {e.col}: {e.message}"
                            if getattr(e, "hint", None):
                                msg += f" ({e.hint})"
                            lab_workspace.center_detail.show_compile_result(
                                False, msg, e.line, e.col
                            )
                            event_log.add_entry("ERROR", msg)
                    else:
                        msg = str(exc)
                        lab_workspace.center_detail.show_compile_result(False, msg)
                        event_log.add_entry("ERROR", f"Compile failed: {msg}")

            # wire editor toolbar buttons
            lab_workspace.center_detail.save_requested.connect(_on_save)  # type: ignore[attr-defined]
            lab_workspace.save_requested_relay.connect(_on_save)
            lab_workspace.center_detail.compile_requested.connect(_on_compile)  # type: ignore[attr-defined]
            lab_workspace.compile_requested_relay.connect(_on_compile)
            # initial compile at startup (only for non-empty buffers)
            try:
                initial_code = lab_workspace.center_detail.get_code()  # type: ignore[attr-defined]
                if initial_code.strip():
                    _on_compile(initial_code)
            except Exception:
                pass

            # new strategy = instant blank draft (no template injection)
            def _on_new_draft() -> None:
                names = set(lab_workspace.left_nav.names())
                base = "Untitled Strategy"
                name = base if base not in names else f"{base} {len(names) + 1}"
                lab_workspace.open_strategy(name, "")
                event_log.add_entry("INFO", f"New strategy draft: {name}")

            lab_workspace.left_nav.new_strategy_requested.connect(_on_new_draft)

            def _on_file_duplicate(name: str) -> None:
                try:
                    from strategy.language.storage import (
                        duplicate_strategy,
                        list_strategies_with_mtime,
                    )

                    copy_name = f"{name} Copy"
                    existing = {n for n, _ in list_strategies_with_mtime(_strategy_data_dir())}
                    counter = 2
                    while copy_name in existing:
                        copy_name = f"{name} Copy {counter}"
                        counter += 1
                    duplicate_strategy(name, copy_name, _strategy_data_dir())
                    event_log.add_entry("INFO", f"Strategy duplicated: {copy_name}")
                    _refresh_my_strategies()
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            def _on_file_rename(old: str, new: str) -> None:
                try:
                    from strategy.language.storage import rename_strategy

                    rename_strategy(old, new, _strategy_data_dir())
                    lab_workspace.center_detail.rename_buffer(old, new)  # type: ignore[attr-defined]
                    if lab_workspace.current_tab_name() == old:
                        lab_workspace.set_strategy_name(new)
                    event_log.add_entry("INFO", f"Strategy renamed: {old} → {new}")
                    _refresh_my_strategies()
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            def _on_file_delete(name: str) -> None:
                try:
                    from strategy.language.storage import delete_strategy

                    delete_strategy(name, _strategy_data_dir())
                    event_log.add_entry("INFO", f"Strategy deleted: {name}")
                    _refresh_my_strategies()
                    lab_workspace.select_next_after_delete(name)
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            lab_workspace.left_nav.duplicate_requested.connect(_on_file_duplicate)  # type: ignore[attr-defined]
            lab_workspace.left_nav.rename_requested.connect(_on_file_rename)  # type: ignore[attr-defined]
            lab_workspace.left_nav.delete_requested.connect(_on_file_delete)  # type: ignore[attr-defined]

            def _on_strategy_selected(name: str) -> None:
                try:
                    from strategy.language.storage import load_strategy

                    code = load_strategy(name, _strategy_data_dir())
                    if code is not None:
                        lab_workspace.open_strategy(name, code)
                        event_log.add_entry("INFO", f"Opened strategy: {name}")
                    else:
                        event_log.add_entry("WARN", f"Strategy not found: {name}")
                except Exception as exc:  # noqa: BLE001
                    event_log.add_entry("ERROR", str(exc))

            lab_workspace.left_nav.strategy_selected.connect(_on_strategy_selected)  # type: ignore[attr-defined]
            lab_workspace.strategy_open_requested.connect(_on_strategy_selected)
            # initial populate
            _refresh_my_strategies()

        # ── bus → panels ──
        self._bus.subscribe(DataLoaded, lambda e: self._on_data_loaded(e, market_status, event_log))
        self._bus.subscribe(
            ChartReady, lambda e: self._on_chart_ready_lab(e, lab_control, trade_overlay, event_log)
        )
        self._bus.subscribe(TimeframesListed, lambda e: lab_control.set_timeframes(e.timeframes))
        self._bus.subscribe(RunBacktest, backtest_worker.on_run_backtest)
        self._bus.subscribe(
            BacktestStarted,
            lambda e: self._on_backtest_started(e, lab_control, event_log, system_health),
        )
        self._bus.subscribe(
            BacktestProgress, lambda e: self._on_backtest_progress(e, trade_overlay, widget)
        )
        self._bus.subscribe(
            BacktestCompleted,
            lambda e: self._on_backtest_completed(
                e, performance_panel, trade_overlay, lab_control, event_log, system_health, widget
            ),
        )
        self._bus.subscribe(
            BacktestFailed,
            lambda e: self._on_backtest_failed(e, lab_control, event_log, system_health),
        )
        self._bus.subscribe(
            PaperTradeRequested, lambda e: logger.info("Paper trade requested: %s", e.strategy_id)
        )
        self._bus.subscribe(
            LabReset,
            lambda _e: self._on_lab_reset(performance_panel, trade_overlay, event_log, widget),
        )

        # Qt signals → bus
        backtest_worker.started.connect(self._bus.publish)
        backtest_worker.progress.connect(self._bus.publish)
        backtest_worker.completed.connect(self._bus.publish)
        backtest_worker.failed.connect(self._bus.publish)

        # initial population (post-subscriptions so StrategiesListed handlers exist)
        _refresh_strategies()
        system_health.set_engine_state(
            "Strategy Engine", f"{len(strategy_registry.list())} strategies"
        )
        system_health.set_engine_state("Backtest Engine", "Idle")
        system_health.set_engine_state("Chart Engine", "--")
        system_health.set_engine_state("Data Engine", "--")

    def _create_strategy_from_draft(
        self, draft: Any, registry: Any, event_log: Any, refresh: Any
    ) -> None:  # type: ignore[no-untyped-def]
        from strategy.models.definition import StrategyDefinition
        from strategy.models.parameters import StrategyParameters

        base_id = draft.name.lower().replace(" ", "-").strip("-") or "strategy"
        candidate = base_id
        suffix = 0
        while registry.contains(candidate):
            suffix += 1
            candidate = f"{base_id}-{suffix}"
        try:
            definition = StrategyDefinition(
                id=candidate,
                name=draft.name,
                version=draft.version,
                kind=draft.kind,
                params=StrategyParameters(draft.params),
                allocation_pct=draft.allocation_pct,
            )
            registry.register_definition(definition)
        except Exception as exc:  # noqa: BLE001
            event_log.add_entry("ERROR", f"Validation failed: {exc}")
            return
        event_log.add_entry("INFO", f"Strategy created: {definition.label}")
        refresh()

    def _on_data_loaded(self, event: DataLoaded, market_status: Any, event_log: Any) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        market_status.set_data_status(
            last_update=event.bars[-1].timestamp[:16] if event.bars else "--",
            bars_loaded=len(event.bars),
        )
        event_log.add_entry("INFO", f"Data loaded: {event.symbol} ({len(event.bars)} bars)")

    def _on_chart_ready_lab(
        self, event: ChartReady, lab_control: Any, trade_overlay: Any, event_log: Any
    ) -> None:  # type: ignore[no-untyped-def]
        lab_control.select_timeframe(event.model.timeframe)
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.select_timeframe(event.model.timeframe)
        except Exception:
            pass
        trade_overlay.clear()
        try:
            if hasattr(self, "_plot_overlay") and self._plot_overlay is not None:
                self._plot_overlay.clear()  # type: ignore[attr-defined]
                # widget will repaint via ChartReady handling; plot will be repopulated via recalc
                if hasattr(self, "_widget") and self._widget is not None:
                    self._widget.update()  # type: ignore[attr-defined]
        except Exception:
            pass
        self._system_health.set_engine_state("Chart Engine", "Active")
        event_log.add_entry("INFO", f"Chart ready: {event.model.symbol} ({event.model.timeframe})")

    def _on_backtest_started(
        self, event: Any, lab_control: Any, event_log: Any, system_health: Any
    ) -> None:  # type: ignore[no-untyped-def]
        lab_control.set_busy(True)
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.set_busy(True)
        except Exception:
            pass
        event_log.add_entry("INFO", f"Backtest started: {event.symbol} {event.timeframe}")
        system_health.set_engine_state("Backtest Engine", "Running")

    def _on_backtest_progress(self, event: Any, trade_overlay: Any, widget: Any) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        trade_overlay.set_replay_index(event.processed)
        widget.update()

    def _on_backtest_completed(
        self,
        event: Any,
        performance_panel: Any,
        trade_overlay: Any,
        lab_control: Any,
        event_log: Any,
        system_health: Any,
        widget: Any,
    ) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        from backtest.models.result import BacktestResult

        result: BacktestResult = event.result  # type: ignore[assignment]
        lab_control.set_busy(False)
        # also update dedicated workspace if present
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.set_busy(False)
                if result.results:
                    self._lab_workspace.set_result(result.results[0])
                self._last_train_result = result.results[0] if result.results else None
        except Exception:
            pass
        if result.results:
            first = result.results[0]
            performance_panel.set_result(first)
            trade_overlay.set_result(first)
            # chart plots — generic, no OBR-specific
            try:
                if hasattr(self, "_plot_overlay") and self._plot_overlay is not None:
                    cs = getattr(first, "chart_series", ())
                    self._plot_overlay.set_from_chart_series(cs)  # type: ignore[attr-defined]
            except Exception:
                pass
            widget.update()
            event_log.add_entry(
                "SUCCESS",
                f"Backtest completed: {first.name} — {len(first.trades)} trades, net ₹{first.metrics.net_profit:+,.0f}",  # noqa: E501  # noqa: E501
            )
            # Phase 5: create immutable execution history (canonical strategy_id = StrategyRecord.id)  # noqa: E501
            try:
                import hashlib

                from backtest.execution import (
                    ExecutionEvent,
                    ExecutionHistory,
                    create_snapshot,
                    save_history,
                )
                from strategy.language.storage import (
                    get_strategy_by_id,
                    load_strategy,
                    load_strategy_record,
                )
                from strategy.version import list_versions

                # Canonical strategy source versions live with D:\VAYREN_STRATEGIES  # noqa: E501
                data_dir = r"D:\VAYREN_STRATEGIES"
                for res in result.results:
                    # Find strategy definition and resolve canonical UUID via library
                    try:
                        definition = self._strategy_registry.get(res.strategy_id)
                        # Resolve canonical id: prefer library UUID if name exists
                        canonical_id = definition.id
                        source = None
                        version = None
                        # Try to map definition.name -> library UUID
                        try:
                            rec = load_strategy_record(definition.name, data_dir)
                            if rec is not None:
                                canonical_id = rec.id
                                source = rec.code
                        except Exception:
                            pass
                        if source is None:
                            source = load_strategy(definition.name, data_dir) or ""
                        # Also try get_strategy_by_id with registry id (might be UUID already)
                        if not source:
                            rec2 = get_strategy_by_id(definition.id, data_dir)
                            if rec2 is not None:
                                canonical_id = rec2.id
                                source = rec2.code
                        strategy_id = canonical_id
                        # Find latest version for canonical strategy
                        versions = list_versions(strategy_id, data_dir)
                        if versions:
                            version = versions[-1]
                            source_hash = version.source_hash
                            ir_version = version.ir_version
                            version_id = version.version_id
                        else:
                            # Fallback: if canonical had no version, check slug-based listing (backward compat)  # noqa: E501
                            alt_versions = list_versions(definition.id, data_dir)
                            if alt_versions:
                                version = alt_versions[-1]
                                strategy_id = definition.id
                                source_hash = version.source_hash
                                ir_version = version.ir_version
                                version_id = version.version_id
                            else:
                                source_hash = (
                                    hashlib.sha256((source or "").encode("utf-8")).hexdigest()
                                    if source
                                    else ""
                                )
                                ir_version = 1
                                version_id = "v0"
                        # Build snapshot — exact version reference preserved forever (Python-native)
                        snap = create_snapshot(
                            strategy_id,
                            version_id,
                            source_hash,
                            None,
                            dict(definition.params) if hasattr(definition, "params") else {},
                            res.config,  # type: ignore[attr-defined]
                            data_dir,
                        )
                        # Build generic events from trades
                        events = []
                        seq = 0
                        for t in res.trades:
                            events.append(
                                ExecutionEvent(
                                    execution_id=snap.execution_id,
                                    sequence=seq,
                                    event_type="TradeClosed",
                                    timestamp=t.exit_time,
                                    data={
                                        "symbol": t.symbol,
                                        "side": t.side,
                                        "pnl": t.pnl,
                                        "entry": t.entry_price,
                                        "exit": t.exit_price,
                                    },
                                )
                            )
                            seq += 1
                        # Also add BarProcessed-like for replay determinism (using equity curve length)  # noqa: E501
                        for idx, pt in enumerate(res.equity_curve[:5]):
                            events.append(
                                ExecutionEvent(
                                    execution_id=snap.execution_id,
                                    sequence=seq,
                                    event_type="BarProcessed",
                                    timestamp=pt.timestamp,
                                    data={"index": idx},
                                )
                            )
                            seq += 1
                        signals = [
                            {
                                "index": t.entry_index,
                                "kind": "BUY" if t.side == "LONG" else "SELL",
                                "price": t.entry_price,
                                "timestamp": t.entry_time,
                            }
                            for t in res.trades
                        ]
                        history = ExecutionHistory(
                            snapshot=snap, events=events, signals=signals, trades=res.trades
                        )
                        save_history(history, data_dir)
                        # Lineage: VERSION -> EXECUTION (and STRATEGY -> VERSION already via version creation)  # noqa: E501
                        try:
                            from strategy.research.lineage import (
                                load_lineage,
                                save_lineage,
                            )

                            g = load_lineage(data_dir)
                            g.add_node("STRATEGY", strategy_id)
                            g.add_node("VERSION", version_id)
                            g.add_edge(
                                "STRATEGY",
                                strategy_id,
                                "VERSION",
                                version_id,
                                relationship="has_version",
                            )
                            g.add_edge(
                                "VERSION",
                                version_id,
                                "EXECUTION",
                                snap.execution_id,
                                relationship="executed_as",
                            )
                            # Also link execution to strategy directly for backward traversal convenience  # noqa: E501
                            g.add_edge(
                                "STRATEGY",
                                strategy_id,
                                "EXECUTION",
                                snap.execution_id,
                                relationship="executed",
                            )
                            save_lineage(g, data_dir)
                        except Exception:
                            pass
                        event_log.add_entry(
                            "INFO", f"Execution {snap.execution_id} saved (v{version_id[:8]})"
                        )
                        # Expose to lab workspace for replay UI if available
                        try:
                            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                                # Store last execution for replay button
                                self._last_execution_id = snap.execution_id  # type: ignore[attr-defined]
                                self._last_execution_ir = ir  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    except Exception as e:  # noqa: BLE001
                        event_log.add_entry("WARN", f"History save failed: {e}")
            except Exception:
                pass
        system_health.set_engine_state("Backtest Engine", "Idle")

    def _on_backtest_failed(
        self, event: Any, lab_control: Any, event_log: Any, system_health: Any
    ) -> None:  # type: ignore[no-untyped-def]
        lab_control.set_busy(False)
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.right_settings.set_busy(False)
        except Exception:
            pass
        event_log.add_entry("ERROR", f"Backtest failed: {event.reason}")
        system_health.set_engine_state("Backtest Engine", "Idle")

    def _on_lab_reset(
        self, performance_panel: Any, trade_overlay: Any, event_log: Any, widget: Any
    ) -> None:  # type: ignore[no-untyped-def]
        performance_panel.clear()
        trade_overlay.clear()
        try:
            if hasattr(self, "_plot_overlay") and self._plot_overlay is not None:
                self._plot_overlay.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        widget.update()
        try:
            if hasattr(self, "_lab_workspace") and self._lab_workspace is not None:
                self._lab_workspace.clear()
        except Exception:
            pass
        event_log.add_entry("INFO", "Lab reset")

    def start(self) -> None:
        """Show the main window and publish AppStarted; the flow continues event-driven."""
        data_window: HistoricalDownloadPanel = self._services.get("data_window")
        data_engine: HistoricalDownloadEngine = self._services.get("data_engine")
        ready, reason = data_engine.provider_available()
        data_window.set_provider(ready, reason)
        # data-status provider row
        provider_label = "Zerodha" if ready else "Not Configured"
        self._market_status.set_data_status(provider=provider_label, latency="--")
        self._system_health.set_engine_state("Data Engine", "Ready" if ready else "Unavailable")
        if not ready and reason:
            self._event_log.add_entry("WARN", f"Data provider: {reason}")
        self._services.get("chart_window").show()
        self._event_log.add_entry("INFO", "VAYREN started — Strategy Lab ready")
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
