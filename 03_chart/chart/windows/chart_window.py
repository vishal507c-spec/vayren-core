"""ChartWindow — hosts the symbol sidebar, timeframe toolbar and candle chart."""

from logging import getLogger

from core.event_bus.event_bus import EventBus
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.symbols_listed import SymbolsListed
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QSplitter, QVBoxLayout, QWidget

from chart.events.chart_ready import ChartReady
from chart.events.window_rendered import WindowRendered
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.symbol_list_widget import SymbolListWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar

logger = getLogger(__name__)


class ChartWindow(QMainWindow):
    """Displays a ChartModel via CandleChartWidget, with a stock sidebar and
    a timeframe toolbar.

    Clicking a symbol publishes LoadSymbol and ListTimeframes; clicking a
    timeframe publishes TimeframeChanged. Subscriptions are wired by
    bootstrap; this class only handles incoming events and publishes the
    terminal results.
    """

    def __init__(
        self,
        widget: CandleChartWidget,
        sidebar: SymbolListWidget,
        toolbar: TimeframeToolbar,
        bus: EventBus,
        limit: int | None = None,
    ) -> None:
        super().__init__()
        self._widget = widget
        self._sidebar = sidebar
        self._toolbar = toolbar
        self._bus = bus
        self._limit = limit
        self._current_symbol: str | None = None

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(sidebar)
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
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 1060])
        self.setCentralWidget(splitter)
        self.resize(1280, 760)
        self.setWindowTitle("VAYREN")

        sidebar.symbol_selected.connect(self._on_symbol_selected)
        toolbar.timeframe_selected.connect(self._on_timeframe_selected)

    def on_symbols_listed(self, event: SymbolsListed) -> None:
        """Populate the sidebar with the discovered stock symbols."""
        self._sidebar.set_symbols(event.symbols)
        logger.info("Symbol sidebar populated (%d stocks)", len(event.symbols))

    def on_timeframes_listed(self, event: TimeframesListed) -> None:
        """Populate the timeframe toolbar with the detected timeframes."""
        self._toolbar.set_timeframes(event.timeframes)
        logger.info("Timeframe toolbar populated (%d timeframes)", len(event.timeframes))

    @property
    def sidebar(self) -> SymbolListWidget:
        """The stock sidebar widget."""
        return self._sidebar

    @property
    def toolbar(self) -> TimeframeToolbar:
        """The timeframe selector toolbar."""
        return self._toolbar

    def on_chart_ready(self, event: ChartReady) -> None:
        """Display the prepared chart model and highlight its symbol."""
        model = event.model
        self._current_symbol = model.symbol
        self._widget.set_model(model)
        self._sidebar.select_symbol(model.symbol)
        self._toolbar.select_timeframe(model.timeframe)
        self.setWindowTitle(f"VAYREN — {model.symbol}")
        self.show()
        logger.info("Chart window shown for %s", model.symbol)
        self._bus.publish(WindowRendered())

    def _on_symbol_selected(self, symbol: str) -> None:
        logger.info("User selected symbol: %s", symbol)
        self._current_symbol = symbol
        self._bus.publish(LoadSymbol(symbol=symbol, limit=self._limit))
        self._bus.publish(ListTimeframes(symbol=symbol))

    def _on_timeframe_selected(self, timeframe: str) -> None:
        symbol = self._current_symbol
        if symbol is None:
            logger.warning("No symbol loaded — ignoring timeframe %s", timeframe)
            return
        logger.info("User selected timeframe %s for %s", timeframe, symbol)
        self._bus.publish(TimeframeChanged(symbol=symbol, timeframe=timeframe, limit=self._limit))
