"""ChartWindow — hosts the symbol sidebar, timeframe toolbar and candle chart."""

from logging import getLogger

from core.event_bus.event_bus import EventBus
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.quotes_loaded import QuotesLoaded
from market.events.symbols_listed import SymbolsListed
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from chart.events.chart_ready import ChartReady
from chart.events.window_rendered import WindowRendered
from chart.theme import APP_PALETTE, APP_STYLE
from chart.widgets.candle_chart_widget import CandleChartWidget
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

    def __init__(
        self,
        widget: CandleChartWidget,
        watchlist: WatchlistWidget,
        toolbar: TimeframeToolbar,
        tools: ChartToolsToolbar,
        bus: EventBus,
        limit: int | None = None,
        download: QWidget | None = None,
    ) -> None:
        super().__init__()
        self._widget = widget
        self._watchlist = watchlist
        self._toolbar = toolbar
        self._tools = tools
        self._bus = bus
        self._limit = limit
        self._download = download
        self._current_symbol: str | None = None
        self._current_timeframe: str | None = None
        self._active_panel: str | None = "watchlist"

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(tools)
        splitter.addWidget(watchlist)
        if download is not None:
            splitter.addWidget(download)
        container = QWidget(splitter)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addWidget(widget)
        layout.setStretchFactor(toolbar, 0)
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
        self.setCentralWidget(splitter)
        self._apply_panel_state()
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
        """Populate the watchlist with the discovered stock symbols."""
        self._watchlist.set_symbols(event.symbols)
        logger.info("Watchlist populated (%d stocks)", len(event.symbols))

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

    def _on_timeframe_selected(self, timeframe: str) -> None:
        symbol = self._current_symbol
        if symbol is None:
            logger.warning("No symbol loaded — ignoring timeframe %s", timeframe)
            return
        logger.info("User selected timeframe %s for %s", timeframe, symbol)
        self._current_timeframe = timeframe
        self._bus.publish(TimeframeChanged(symbol=symbol, timeframe=timeframe, limit=self._limit))
