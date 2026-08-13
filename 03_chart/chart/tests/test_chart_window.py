"""ChartWindow tests — symbol/timeframe events and toolbar wiring."""

from datetime import datetime, timedelta

from core.event_bus.event_bus import EventBus
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from market.models.bar import Bar
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QSplitter

from chart.events.chart_ready import ChartReady
from chart.models.chart_model import ChartModel
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.options_panel import OptionsPanel
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.watchlist_widget import WatchlistWidget
from chart.windows.chart_window import ChartWindow

_KEEP_APP: QCoreApplication | None = None


def _bar(timestamp: str) -> Bar:
    return Bar(
        symbol="SPY",
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
        volume=1000,
        timestamp=timestamp,
    )


def _model(symbol: str = "SPY", count: int = 50, timeframe: str = "15m") -> ChartModel:
    start = datetime(2026, 4, 6, 9, 15, 0)
    stamps = ((start + timedelta(seconds=900 * i)).isoformat(sep=" ") for i in range(count))
    bars = tuple(_bar(timestamp) for timestamp in stamps)
    return ChartModel(symbol=symbol, bars=bars, timeframe=timeframe, exchange="NSE")


def _app() -> QApplication:
    global _KEEP_APP
    _KEEP_APP = QApplication.instance()
    if not isinstance(_KEEP_APP, QApplication):
        _KEEP_APP = QApplication([])
    return _KEEP_APP


def _window() -> tuple[ChartWindow, EventBus]:
    _app()
    bus = EventBus()
    widget = CandleChartWidget()
    watchlist = WatchlistWidget()
    options = OptionsPanel()
    toolbar = TimeframeToolbar()
    window = ChartWindow(widget, watchlist, options, toolbar, bus, limit=500)
    return window, bus


def test_on_timeframes_listed_populates_toolbar() -> None:
    window, _ = _window()
    window.on_timeframes_listed(TimeframesListed(symbol="SPY", timeframes=("1m", "5m", "1D")))
    assert window.toolbar.timeframes == ("1m", "5m", "1D")


def test_symbol_selection_publishes_load_and_list() -> None:
    window, bus = _window()
    loads: list[LoadSymbol] = []
    lists: list[ListTimeframes] = []
    bus.subscribe(LoadSymbol, loads.append)
    bus.subscribe(ListTimeframes, lists.append)
    window.watchlist.symbol_selected.emit("TCS")
    assert len(loads) == 1
    assert loads[0].symbol == "TCS"
    assert loads[0].limit == 500
    assert len(lists) == 1
    assert lists[0].symbol == "TCS"


def test_symbol_switch_keeps_chart_timeframe() -> None:
    window, bus = _window()
    loads: list[LoadSymbol] = []
    changed: list[TimeframeChanged] = []
    bus.subscribe(LoadSymbol, loads.append)
    bus.subscribe(TimeframeChanged, changed.append)
    window.on_chart_ready(ChartReady(model=_model("TCS", timeframe="30m")))
    window.watchlist.symbol_selected.emit("SPY")
    assert loads == []
    assert len(changed) == 1
    assert changed[0].symbol == "SPY"
    assert changed[0].timeframe == "30m"
    assert changed[0].limit == 500


def test_symbol_switch_keeps_explicitly_selected_timeframe() -> None:
    window, bus = _window()
    changed: list[TimeframeChanged] = []
    bus.subscribe(TimeframeChanged, changed.append)
    window.on_chart_ready(ChartReady(model=_model("TCS")))
    window.toolbar.set_timeframes(("15m", "1D"))
    window.toolbar._buttons["1D"].click()
    window.watchlist.symbol_selected.emit("SPY")
    window.watchlist.symbol_selected.emit("NETWEB")
    assert [event.timeframe for event in changed] == ["1D", "1D", "1D"]
    assert [event.symbol for event in changed] == ["TCS", "SPY", "NETWEB"]


def test_watchlist_reset_tool_reuses_chart_reset_view() -> None:
    window, _ = _window()
    window.watchlist.set_symbols(("SPY", "TCS"))
    window.on_chart_ready(ChartReady(model=_model("TCS")))
    calls: list[bool] = []
    window._widget.reset_view = lambda: calls.append(True)
    window.watchlist.reset_requested.emit()
    assert len(calls) == 1


def test_timeframe_click_publishes_timeframe_changed() -> None:
    window, bus = _window()
    window.on_chart_ready(ChartReady(model=_model("TCS")))
    changed: list[TimeframeChanged] = []
    bus.subscribe(TimeframeChanged, changed.append)
    window.toolbar.set_timeframes(("15m", "1D"))
    window.toolbar._buttons["1D"].click()
    assert len(changed) == 1
    assert changed[0].symbol == "TCS"
    assert changed[0].timeframe == "1D"
    assert changed[0].limit == 500


def test_timeframe_click_without_symbol_is_ignored() -> None:
    window, bus = _window()
    changed: list[TimeframeChanged] = []
    bus.subscribe(TimeframeChanged, changed.append)
    window.toolbar.set_timeframes(("1D",))
    window.toolbar._buttons["1D"].click()
    assert changed == []


def test_chart_ready_highlights_timeframe_button() -> None:
    window, _ = _window()
    window.toolbar.set_timeframes(("15m", "1D"))
    window.on_chart_ready(ChartReady(model=_model()))
    assert window.toolbar._buttons["15m"].isChecked()
    assert window.windowTitle() == "VAYREN — SPY"


def test_chart_fills_remaining_space_below_toolbar() -> None:
    window, _ = _window()
    window.toolbar.set_timeframes(("15m", "30m", "1h", "1D", "1W"))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    toolbar_bottom = window.toolbar.y() + window.toolbar.height()
    assert window.toolbar.height() == window.toolbar.sizeHint().height()
    assert window._widget.y() == toolbar_bottom
    splitter = window.findChild(QSplitter)
    assert splitter is not None
    assert splitter.count() == 3
    container = splitter.widget(2)
    assert container is not None
    assert window._widget.height() == container.height() - window.toolbar.height()


def test_chart_expands_with_window_resize_no_gap() -> None:
    window, _ = _window()
    window.toolbar.set_timeframes(("1D",))
    window.resize(1200, 760)
    window.show()
    for width, height in ((1200, 760), (1200, 1500), (900, 500)):
        window.resize(width, height)
        _app().processEvents()
        toolbar_bottom = window.toolbar.y() + window.toolbar.height()
        assert window._widget.y() == toolbar_bottom
        assert window._widget.height() >= height - 100


def test_window_applies_polished_theme() -> None:
    window, _ = _window()
    sheet = window.styleSheet()
    assert "QToolButton:hover" in sheet
    assert "QPushButton:checked" in sheet
    assert "QMenu::item:selected" in sheet
    assert "QSplitter::handle" in sheet
    list_sheet = window.watchlist._list.styleSheet()
    assert "QScrollBar" in list_sheet
    assert "::item:hover" in list_sheet


def test_splitter_handles_are_hairline() -> None:
    window, _ = _window()
    splitter = window.centralWidget()
    assert isinstance(splitter, QSplitter)
    assert splitter.handleWidth() == 1
