"""ChartWindow — hosts the symbol sidebar, timeframe toolbar and candle chart."""

import contextlib
from logging import getLogger

from core.event_bus.event_bus import EventBus
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.quotes_loaded import QuotesLoaded
from market.events.symbols_listed import SymbolsListed
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from chart.events.chart_ready import ChartReady
from chart.events.window_rendered import WindowRendered
from chart.theme import APP_PALETTE, APP_STYLE
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.indicators_toolbar import IndicatorsToolbar
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.tools_toolbar import TOOLBAR_WIDTH, ChartToolsToolbar
from chart.widgets.watchlist_widget import WatchlistWidget

logger = getLogger(__name__)

WATCHLIST_PANEL_WIDTH = 220


class ChartWindow(QMainWindow):
    """Displays a ChartModel via CandleChartWidget, with a watchlist panel,
    a timeframe toolbar and a chart-tools rail.

    Layout: tools rail | active side panel | chart (left to right). The
    chart-tools rail is the permanent navigation rail on the extreme left,
    always visible. A single ``active_panel`` state (``None`` or a panel id)
    decides which side panel sits next to the rail; the watchlist and the
    historical download panel are wired to their rail icons (an injected
    ``download`` widget keeps chart independent of the data domain). The
    chart area (timeframe bar + chart canvas, whose top band carries the
    stock header) fills all remaining space and expands/shrinks as panels
    open and close. Clicking a symbol publishes LoadSymbol (first load) or
    TimeframeChanged at the currently selected timeframe (so symbol switches
    never reset the timeframe), plus ListTimeframes; clicking a timeframe
    publishes TimeframeChanged; the watchlist tool button reuses the chart's
    reset-view action. Subscriptions are wired by bootstrap; this class only
    handles incoming events and publishes the terminal results.
    """

    session_changed = Signal()

    def __init__(
        self,
        widget: CandleChartWidget,
        watchlist: WatchlistWidget,
        toolbar: TimeframeToolbar,
        tools: ChartToolsToolbar,
        bus: EventBus,
        limit: int | None = None,
        download: QWidget | None = None,
        nav: QWidget | None = None,
        left_extra: QWidget | None = None,
        lab_workspace: QWidget | None = None,
        live_workspace: QWidget | None = None,
        research_workspace: QWidget | None = None,
        portfolio_workspace: QWidget | None = None,
        brokers_workspace: QWidget | None = None,
        event_log: QWidget | None = None,
        system_health: QWidget | None = None,
        trade_context: QWidget | None = None,
    ) -> None:
        super().__init__()
        self._widget = widget
        self._watchlist = watchlist
        self._toolbar = toolbar
        self._tools = tools
        self._bus = bus
        self._limit = limit
        self._download = download
        self._nav = nav
        self._left_extra = left_extra
        self._lab_workspace = lab_workspace
        self._live_workspace = live_workspace
        self._research_workspace = research_workspace
        self._portfolio_workspace = portfolio_workspace
        self._brokers_workspace = brokers_workspace
        self._event_log = event_log
        self._system_health = system_health
        self._current_symbol: str | None = None
        self._current_timeframe: str | None = None
        self._active_panel: str | None = "watchlist"
        self._lab_active = False
        self._bottom_visible = False

        self._trade_context = trade_context
        # ── Chart top bar: ONE horizontal line (TradingView-style) ──
        # 15m 30m 45m 1h 2h 4h ▾ INDICATORS
        self._indicators = IndicatorsToolbar(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(tools)
        splitter.addWidget(watchlist)
        if download is not None:
            splitter.addWidget(download)
        container = QWidget(splitter)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Single row: timeframes + ▼ + INDICATORS grouped together (left-aligned),
        # empty space AFTER the group — INDICATORS never pushed to the right edge.
        top_row = QWidget(container)
        top_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        top_row.setObjectName("MarketTopBar")
        top_lay = QHBoxLayout(top_row)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(6)
        top_lay.addWidget(toolbar, 0)
        top_lay.addWidget(self._indicators, 0)
        top_lay.addStretch(1)
        layout.addWidget(top_row)
        if trade_context is not None:
            layout.addWidget(trade_context)
        layout.addWidget(widget)
        layout.setStretchFactor(top_row, 0)
        if trade_context is not None:
            layout.setStretchFactor(trade_context, 0)  # type: ignore[arg-type]
        layout.setStretchFactor(widget, 1)
        splitter.addWidget(container)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        if download is not None:
            splitter.setStretchFactor(2, 0)
            splitter.setStretchFactor(3, 1)
            splitter.setSizes([TOOLBAR_WIDTH, WATCHLIST_PANEL_WIDTH, 0, 1018])
        else:
            splitter.setStretchFactor(2, 1)
            splitter.setSizes([TOOLBAR_WIDTH, WATCHLIST_PANEL_WIDTH, 1018])
        splitter.setHandleWidth(1)
        self._splitter = splitter
        self._market_container = splitter

        # ── Enhanced layout when lab/nav are injected (bootstrap lab mode) ──
        has_lab = any(
            x is not None for x in (nav, lab_workspace, event_log, system_health, left_extra)
        )
        if has_lab:
            # bottom area: event log + system health side-by-side, hidden by default
            bottom: QWidget | None = None
            if event_log is not None or system_health is not None:
                bottom = QWidget(self)
                b_lay = QHBoxLayout(bottom)
                b_lay.setContentsMargins(0, 0, 0, 0)
                b_lay.setSpacing(0)
                if event_log is not None and system_health is not None:
                    split_b = QSplitter(Qt.Orientation.Horizontal, bottom)
                    split_b.addWidget(event_log)
                    split_b.addWidget(system_health)
                    split_b.setSizes([640, 640])
                    split_b.setHandleWidth(1)
                    b_lay.addWidget(split_b)
                elif event_log is not None:
                    b_lay.addWidget(event_log)
                else:
                    b_lay.addWidget(system_health)  # type: ignore[arg-type]
                bottom.setVisible(False)
                bottom.setMaximumHeight(220)
            self._bottom = bottom

            # stacked middle: market splitter vs lab/live/research/portfolio
            if lab_workspace is not None:
                self._stack = QStackedWidget(self)
                self._stack.addWidget(splitter)
                self._stack.addWidget(lab_workspace)
                if live_workspace is not None:
                    self._stack.addWidget(live_workspace)
                if research_workspace is not None:
                    self._stack.addWidget(research_workspace)
                if portfolio_workspace is not None:
                    self._stack.addWidget(portfolio_workspace)
                if brokers_workspace is not None:
                    self._stack.addWidget(brokers_workspace)
                self._stack.setCurrentIndex(0)
            else:
                self._stack = None  # type: ignore[assignment]

            outer = QWidget(self)
            o_lay = QVBoxLayout(outer)
            o_lay.setContentsMargins(0, 0, 0, 0)
            o_lay.setSpacing(0)
            if nav is not None:
                o_lay.addWidget(nav)
            if left_extra is not None:
                # left_extra as a small left dock below nav, above market/lab
                # For minimal fix keep it hidden-collapsible next to market; simplest:
                # add as a widget above the stack but compact
                left_extra.setVisible(False)
                o_lay.addWidget(left_extra)
                self._left_extra_widget = left_extra
            if lab_workspace is not None:
                o_lay.addWidget(self._stack, 1)  # type: ignore[arg-type]
            else:
                o_lay.addWidget(splitter, 1)
            if bottom is not None:
                o_lay.addWidget(bottom)
            self.setCentralWidget(outer)
            self._outer = outer
        else:
            self._stack = None  # type: ignore[assignment]
            self._bottom = None  # type: ignore[assignment]
            self.setCentralWidget(splitter)
        self._apply_panel_state()
        # Restored/normal geometry only (used if the user later un-maximizes).
        # Startup size is NOT defined here — Bootstrap.start() shows this window
        # maximized (native), so no saved or hardcoded size can pin startup small.
        self.resize(1280, 760)
        self.setWindowTitle("VAYREN")
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(APP_PALETTE)
        self.setPalette(APP_PALETTE)
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet(APP_STYLE)

        watchlist.symbol_selected.connect(self._on_symbol_selected)
        watchlist.reset_requested.connect(self._widget.reset_view)
        toolbar.timeframe_selected.connect(self._on_timeframe_selected)
        tools.watchlist_clicked.connect(lambda: self.toggle_panel("watchlist"))
        tools.download_clicked.connect(lambda: self.toggle_panel("download"))
        self._indicators.strategy_selected.connect(self._on_indicator_strategy_selected)
        self._indicators.indicator_selected.connect(self._on_indicator_selected)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Release this window and its widget tree when closed.

        Qt's ``close()`` only hides a window by default, so the whole tree
        (splitter, chart, panels, watchlist — ~1150 widgets) stayed alive as a
        top-level object for the process lifetime. In long-lived processes
        that rebuild the window (notably the test suite, where cyclic GC is
        disabled) this accumulated without bound. Deleting on close keeps the
        production lifecycle honest and stops the growth.
        """
        super().closeEvent(event)
        self.deleteLater()

    @property
    def active_panel(self) -> str | None:
        """The id of the currently open side panel, or None when closed."""
        return self._active_panel

    def toggle_panel(self, panel_id: str) -> None:
        """TradingView-style panel navigation: clicking the active panel's
        icon closes it, clicking another icon switches to that panel. Only
        ONE panel can be active at a time.
        """
        self._active_panel = None if self._active_panel == panel_id else panel_id
        self._apply_panel_state()

    def _apply_panel_state(self) -> None:
        watchlist_open = self._active_panel == "watchlist"
        download_open = self._active_panel == "download"
        self._watchlist.setVisible(watchlist_open)
        self._tools.watchlist_button.setChecked(watchlist_open)
        width = max(1, self._splitter.width())
        if self._download is None:
            if watchlist_open:
                self._splitter.setSizes(
                    [
                        TOOLBAR_WIDTH,
                        WATCHLIST_PANEL_WIDTH,
                        max(1, width - TOOLBAR_WIDTH - WATCHLIST_PANEL_WIDTH),
                    ]
                )
            else:
                self._splitter.setSizes([TOOLBAR_WIDTH, 0, max(1, width - TOOLBAR_WIDTH)])
            return
        self._download.setVisible(download_open)
        self._tools.download_button.setChecked(download_open)
        if watchlist_open:
            self._splitter.setSizes(
                [
                    TOOLBAR_WIDTH,
                    WATCHLIST_PANEL_WIDTH,
                    0,
                    max(1, width - TOOLBAR_WIDTH - WATCHLIST_PANEL_WIDTH),
                ]
            )
        elif download_open:
            self._splitter.setSizes(
                [
                    TOOLBAR_WIDTH,
                    0,
                    WATCHLIST_PANEL_WIDTH,
                    max(1, width - TOOLBAR_WIDTH - WATCHLIST_PANEL_WIDTH),
                ]
            )
        else:
            self._splitter.setSizes([TOOLBAR_WIDTH, 0, 0, max(1, width - TOOLBAR_WIDTH)])

    def on_symbols_listed(self, event: SymbolsListed) -> None:
        """Populate the watchlist with the discovered stock symbols.

        Restores saved session symbol if available and valid, otherwise
        TradingView-style: first symbol. Persists are validated against
        actual symbol list; corrupted/missing falls back to defaults.
        """
        self._watchlist.set_symbols(event.symbols)
        logger.info("Watchlist populated (%d stocks)", len(event.symbols))
        if not event.symbols:
            return
        target: str | None = None
        if self._current_symbol is not None and self._current_symbol in event.symbols:
            target = self._current_symbol
        elif self._current_symbol is None:
            target = event.symbols[0]
        else:
            # saved symbol not found (corrupted) → fallback to first
            target = event.symbols[0]
            self._current_symbol = None  # reset to allow fallback logic next time
            # also clear timeframe if symbol invalid? keep as is, will be validated later
            logger.info("Saved symbol not found, fallback to %s", target)
        if target is not None:
            self._watchlist.select_symbol(target)
            self._current_symbol = target
            self._on_symbol_selected(target)

    def on_quotes_loaded(self, event: QuotesLoaded) -> None:
        """Attach the latest real quotes to the watchlist rows."""
        self._watchlist.set_quotes(event.quotes)
        logger.info("Watchlist quotes attached (%d)", len(event.quotes))

    def on_timeframes_listed(self, event: TimeframesListed) -> None:
        """Populate the timeframe toolbar with the detected timeframes.

        When the toolbar is populated after a chart already loaded (the
        timeframes list always lags the chart in the event chain), re-apply
        the current timeframe so the active button stays visibly correct.
        """
        self._toolbar.set_timeframes(event.timeframes)
        if self._current_timeframe is not None:
            self._toolbar.select_timeframe(self._current_timeframe)
        logger.info("Timeframe toolbar populated (%d timeframes)", len(event.timeframes))

    @property
    def watchlist(self) -> WatchlistWidget:
        """The watchlist panel widget."""
        return self._watchlist

    @property
    def toolbar(self) -> TimeframeToolbar:
        """The timeframe selector toolbar."""
        return self._toolbar

    @property
    def tools(self) -> ChartToolsToolbar:
        """The chart-tools rail, first element on the extreme left."""
        return self._tools

    @property
    def download(self) -> QWidget | None:
        """The historical download side panel (injected by bootstrap), or None."""
        return self._download

    @property
    def current_symbol(self) -> str | None:
        """Currently loaded symbol, or None before the first chart."""
        return self._current_symbol

    @property
    def current_timeframe(self) -> str | None:
        """Currently active timeframe, or None before the first chart."""
        return self._current_timeframe

    def show_market(self) -> None:
        """Switch to the market chart view."""
        self._lab_active = False
        if getattr(self, "_stack", None) is not None and self._stack is not None:
            self._stack.setCurrentIndex(0)
        if self._nav is not None and hasattr(self._nav, "set_active"):
            with contextlib.suppress(Exception):
                self._nav.set_active("MARKET")  # type: ignore[attr-defined]

    def show_lab(self) -> None:
        """Switch to the Strategy Lab workspace."""
        self._lab_active = True
        if getattr(self, "_stack", None) is not None and self._stack is not None:
            self._stack.setCurrentIndex(1)
        if self._nav is not None and hasattr(self._nav, "set_active"):
            with contextlib.suppress(Exception):
                self._nav.set_active("STRATEGY LAB")  # type: ignore[attr-defined]

    def show_live(self) -> None:
        """Switch to the LIVE execution workspace (index 2 when present)."""
        self._lab_active = False
        stack = getattr(self, "_stack", None)
        live = getattr(self, "_live_workspace", None)
        if stack is not None and live is not None:
            for index in range(stack.count()):
                if stack.widget(index) is live:
                    stack.setCurrentIndex(index)
                    break
        if self._nav is not None and hasattr(self._nav, "set_active"):
            with contextlib.suppress(Exception):
                self._nav.set_active("LIVE")  # type: ignore[attr-defined]

    def _show_workspace(self, attr: str, section: str) -> None:
        """Switch to an injected workspace widget by attribute name."""
        self._lab_active = False
        stack = getattr(self, "_stack", None)
        target = getattr(self, attr, None)
        if stack is not None and target is not None:
            for index in range(stack.count()):
                if stack.widget(index) is target:
                    stack.setCurrentIndex(index)
                    break
        if self._nav is not None and hasattr(self._nav, "set_active"):
            with contextlib.suppress(Exception):
                self._nav.set_active(section)  # type: ignore[attr-defined]

    def show_research(self) -> None:
        """Switch to the Research workspace (when injected)."""
        self._show_workspace("_research_workspace", "RESEARCH")

    def show_portfolio(self) -> None:
        """Switch to the Portfolio workspace (when injected)."""
        self._show_workspace("_portfolio_workspace", "PORTFOLIO")

    def show_brokers(self) -> None:
        """Switch to the SYSTEM → BROKERS workspace (when injected)."""
        self._show_workspace("_brokers_workspace", "SYSTEM")

    def toggle_bottom(self) -> None:
        """Toggle the bottom system/event panels."""
        self._bottom_visible = not self._bottom_visible
        bottom = getattr(self, "_bottom", None)
        if bottom is not None:
            bottom.setVisible(self._bottom_visible)
        # left extra toggles with bottom as well (market status)
        left = getattr(self, "_left_extra", None)
        if left is not None:
            with contextlib.suppress(Exception):
                left.setVisible(self._bottom_visible)

    def on_chart_ready(self, event: ChartReady) -> None:
        """Display the prepared chart model and highlight its symbol."""
        model = event.model
        self._current_symbol = model.symbol
        self._current_timeframe = model.timeframe
        self._widget.set_model(model)
        self._watchlist.select_symbol(model.symbol)
        self._toolbar.select_timeframe(model.timeframe)
        self.setWindowTitle(f"VAYREN — {model.symbol}")
        self.show()
        logger.info("Chart window shown for %s", model.symbol)
        self._bus.publish(WindowRendered())
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def _on_symbol_selected(self, symbol: str) -> None:
        logger.info("User selected symbol: %s", symbol)
        self._current_symbol = symbol
        timeframe = self._current_timeframe
        if timeframe is None:
            self._bus.publish(LoadSymbol(symbol=symbol, limit=self._limit))
        else:
            self._bus.publish(
                TimeframeChanged(symbol=symbol, timeframe=timeframe, limit=self._limit)
            )
        self._bus.publish(ListTimeframes(symbol=symbol))
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def _on_timeframe_selected(self, timeframe: str) -> None:
        symbol = self._current_symbol
        if symbol is None:
            logger.warning("No symbol loaded — ignoring timeframe %s", timeframe)
            return
        logger.info("User selected timeframe %s for %s", timeframe, symbol)
        self._current_timeframe = timeframe
        self._bus.publish(TimeframeChanged(symbol=symbol, timeframe=timeframe, limit=self._limit))
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    @property
    def indicators(self) -> IndicatorsToolbar:
        """Single INDICATORS control at top of chart."""
        return self._indicators

    def _on_indicator_strategy_selected(self, name: str) -> None:
        """Add strategy indicator to chart visibility list (TradingView dynamic)."""
        logger.info("Indicators strategy selected: %s (forwarded to app layer)", name)
        with contextlib.suppress(Exception):
            self._widget.add_indicator(name)
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def _on_indicator_selected(self, name: str, category: str) -> None:
        """Add indicator to chart visibility list (TradingView dynamic)."""
        logger.info("Indicator selected: %s (%s) — no strategy execution", name, category)
        with contextlib.suppress(Exception):
            self._widget.add_indicator(name)
        with contextlib.suppress(Exception):
            self.session_changed.emit()

    def set_indicator_strategies(self, names: tuple[str, ...]) -> None:
        """Inject strategy names into the INDICATORS menu (called by bootstrap)."""
        with contextlib.suppress(Exception):
            self._indicators.set_strategies(names)
