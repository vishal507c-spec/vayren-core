"""ChartWindow tests — symbol/timeframe events and toolbar wiring."""

from datetime import datetime, timedelta

from core.event_bus.event_bus import EventBus
from market.events.list_timeframes import ListTimeframes
from market.events.load_symbol import LoadSymbol
from market.events.quotes_loaded import QuotesLoaded
from market.events.timeframe_changed import TimeframeChanged
from market.events.timeframes_listed import TimeframesListed
from market.models.bar import Bar
from market.models.symbol_quote import SymbolQuote
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QSplitter, QWidget

from chart.events.chart_ready import ChartReady
from chart.models.chart_model import ChartModel
from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.tools_toolbar import ChartToolsToolbar
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
    toolbar = TimeframeToolbar()
    tools = ChartToolsToolbar()
    window = ChartWindow(widget, watchlist, toolbar, tools, bus, limit=500)
    return window, bus


def test_on_timeframes_listed_populates_toolbar() -> None:
    window, _ = _window()
    window.on_timeframes_listed(TimeframesListed(symbol="SPY", timeframes=("1m", "5m", "1D")))
    assert window.toolbar.timeframes == ("1m", "5m", "1D")


def test_quotes_loaded_attaches_quotes_to_watchlist() -> None:
    window, _ = _window()
    window.watchlist.set_symbols(("SPY", "TCS"))
    quote = SymbolQuote(symbol="SPY", price=100.0, change_pct=0.49, timestamp="2026-06-10")
    window.on_quotes_loaded(QuotesLoaded(quotes=(quote,)))
    spy_item = window.watchlist._list.item(0)
    assert spy_item.data(Qt.ItemDataRole.UserRole) == quote
    tcs_item = window.watchlist._list.item(1)
    assert tcs_item.data(Qt.ItemDataRole.UserRole) is None


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
    assert "TimeframeToolbar QPushButton" in sheet
    assert "QMenu::item:checked" in sheet
    assert "QToolTip" in sheet
    list_sheet = window.watchlist._list.styleSheet()
    assert "QScrollBar" in list_sheet
    assert "::item:hover" in list_sheet


def test_window_applies_institutional_palette() -> None:
    from PySide6.QtGui import QPalette

    from chart.theme import APP_PALETTE

    window, _ = _window()
    assert window.palette().color(QPalette.ColorRole.Window) == APP_PALETTE.color(
        QPalette.ColorRole.Window
    )
    assert window.palette().color(QPalette.ColorRole.Highlight) == APP_PALETTE.color(
        QPalette.ColorRole.Highlight
    )
    assert _app().palette().color(QPalette.ColorRole.Highlight) == APP_PALETTE.color(
        QPalette.ColorRole.Highlight
    )


def test_splitter_handles_are_hairline() -> None:
    window, _ = _window()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    assert splitter.handleWidth() == 1


def test_watchlist_panel_open_by_default_with_rail_icon_checked() -> None:
    window, _ = _window()
    window.show()
    _app().processEvents()
    assert window.active_panel == "watchlist"
    assert window.watchlist.isVisible()
    assert window.tools.watchlist_button.isChecked()


def test_watchlist_icon_click_closes_panel_and_expands_chart() -> None:
    window, _ = _window()
    window.toolbar.set_timeframes(("15m",))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    container = splitter.widget(2)
    assert isinstance(container, QWidget)
    open_chart_width = container.width()
    assert window.tools.isVisible()
    assert window.watchlist.isVisible()

    window.tools.watchlist_button.click()
    _app().processEvents()

    assert window.active_panel is None
    assert not window.tools.watchlist_button.isChecked()
    assert window.tools.isVisible()
    assert not window.watchlist.isVisible()
    assert container.width() == window.width() - 40 - 1
    assert container.width() > open_chart_width


def test_watchlist_icon_click_restores_panel_and_preserves_state() -> None:
    window, _ = _window()
    window.toolbar.set_timeframes(("15m",))
    window.watchlist.set_symbols(("SPY", "TCS"))
    window.watchlist.set_quotes(
        (SymbolQuote(symbol="SPY", price=100.0, change_pct=0.49, timestamp="2026-06-10"),)
    )
    window.on_chart_ready(ChartReady(model=_model("TCS")))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    open_sizes = splitter.sizes()
    container = splitter.widget(2)
    assert isinstance(container, QWidget)
    open_chart_width = container.width()

    window.tools.watchlist_button.click()
    _app().processEvents()
    assert window.watchlist.current_symbol == "TCS"
    window.tools.watchlist_button.click()
    _app().processEvents()

    assert window.active_panel == "watchlist"
    assert window.tools.watchlist_button.isChecked()
    assert window.tools.isVisible()
    assert window.watchlist.isVisible()
    assert splitter.sizes() == open_sizes
    assert window.watchlist.symbols == ("SPY", "TCS")
    assert window.watchlist.current_symbol == "TCS"
    assert container.width() == open_chart_width


def test_watchlist_panel_repeated_cycles_are_stable() -> None:
    window, _ = _window()
    window.toolbar.set_timeframes(("15m",))
    window.watchlist.set_symbols(("SPY", "TCS"))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    for _ in range(5):
        window.tools.watchlist_button.click()
        _app().processEvents()
        assert splitter.count() == 3
        assert window.tools.isVisible()
        if window.active_panel == "watchlist":
            assert window.watchlist.isVisible()
        else:
            assert not window.watchlist.isVisible()
            container = splitter.widget(2)
            assert isinstance(container, QWidget)
            assert container.width() == window.width() - 40 - 1
    assert window.active_panel is None
    window.tools.watchlist_button.click()
    assert window.active_panel == "watchlist"


def test_active_panel_single_state_model() -> None:
    window, _ = _window()
    window.show()
    _app().processEvents()
    assert window.active_panel == "watchlist"
    window.toggle_panel("watchlist")
    assert window.active_panel is None
    assert not window.watchlist.isVisible()
    window.toggle_panel("tool2")
    assert window.active_panel == "tool2"
    assert not window.watchlist.isVisible()
    window.toggle_panel("tool2")
    assert window.active_panel is None
    assert window.tools.isVisible()


def test_rail_watchlist_button_is_first_and_independent() -> None:
    window, _ = _window()
    buttons = window.tools.buttons
    assert window.tools._buttons_by_kind["watchlist"] is buttons[0]
    assert buttons[0].isCheckable()
    assert not window.tools._mode_group.id(buttons[0]) >= 0
    buttons[0].click()
    _app().processEvents()
    assert not window.tools._buttons_by_kind["watchlist"].isChecked()


def test_symbol_switch_works_while_panel_closed() -> None:
    window, bus = _window()
    changed: list[TimeframeChanged] = []
    bus.subscribe(TimeframeChanged, changed.append)
    window.on_chart_ready(ChartReady(model=_model("TCS", timeframe="30m")))
    window.toggle_panel("watchlist")
    window.watchlist.symbol_selected.emit("SPY")
    assert len(changed) == 1
    assert changed[0].symbol == "SPY"
    assert changed[0].timeframe == "30m"


def _window_with_download() -> tuple[ChartWindow, QWidget, EventBus]:
    _app()
    bus = EventBus()
    widget = CandleChartWidget()
    watchlist = WatchlistWidget()
    toolbar = TimeframeToolbar()
    tools = ChartToolsToolbar()
    panel = QWidget()
    window = ChartWindow(widget, watchlist, toolbar, tools, bus, limit=500, download=panel)
    return window, panel, bus


def test_download_panel_sits_between_watchlist_and_chart() -> None:
    window, panel, _ = _window_with_download()
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    assert splitter.count() == 4
    assert splitter.indexOf(window.tools) == 0
    assert splitter.indexOf(window.watchlist) == 1
    assert splitter.indexOf(panel) == 2
    parent = window._widget.parentWidget()
    assert parent is not None
    assert splitter.indexOf(parent) == 3
    assert not panel.isVisible()
    assert window.watchlist.isVisible()
    assert window.active_panel == "watchlist"
    assert window.tools.watchlist_button.isChecked()
    assert not window.tools.download_button.isChecked()


def test_download_icon_click_switches_panel_and_expands_chart() -> None:
    window, panel, _ = _window_with_download()
    window.toolbar.set_timeframes(("15m",))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    container = splitter.widget(3)
    assert isinstance(container, QWidget)
    open_chart_width = container.width()
    assert not panel.isVisible()
    assert window.watchlist.isVisible()

    window.tools.download_button.click()
    _app().processEvents()

    assert window.active_panel == "download"
    assert panel.isVisible()
    assert not window.watchlist.isVisible()
    assert not window.tools.watchlist_button.isChecked()
    assert window.tools.download_button.isChecked()
    assert window.tools.isVisible()
    assert container.width() >= open_chart_width
    assert container.width() == window.width() - 40 - 220 - 2


def test_download_icon_click_again_closes_panel_and_expands_chart() -> None:
    window, panel, _ = _window_with_download()
    window.toolbar.set_timeframes(("15m",))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    container = splitter.widget(3)
    assert isinstance(container, QWidget)

    window.tools.download_button.click()
    _app().processEvents()
    open_chart_width = container.width()
    window.tools.download_button.click()
    _app().processEvents()

    assert window.active_panel is None
    assert not panel.isVisible()
    assert not window.watchlist.isVisible()
    assert not window.tools.download_button.isChecked()
    assert container.width() > open_chart_width
    assert container.width() == window.width() - 40 - 1


def test_panels_switch_with_only_one_active() -> None:
    window, panel, _ = _window_with_download()
    window.toolbar.set_timeframes(("15m",))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    assert window.active_panel == "watchlist"

    window.tools.download_button.click()
    _app().processEvents()
    assert window.active_panel == "download"
    assert panel.isVisible()
    assert not window.watchlist.isVisible()

    window.tools.watchlist_button.click()
    _app().processEvents()
    assert window.active_panel == "watchlist"
    assert window.watchlist.isVisible()
    assert not panel.isVisible()

    window.tools.watchlist_button.click()
    _app().processEvents()
    assert window.active_panel is None
    assert not panel.isVisible()

    window.tools.download_button.click()
    _app().processEvents()
    assert window.active_panel == "download"
    assert panel.isVisible()
    assert not window.watchlist.isVisible()
    assert splitter.sizes() == [40, 0, 220, 1018]


def test_download_panel_repeated_cycles_are_stable() -> None:
    window, panel, _ = _window_with_download()
    window.toolbar.set_timeframes(("15m",))
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    splitter = window.findChild(QSplitter)
    assert isinstance(splitter, QSplitter)
    container = splitter.widget(3)
    assert isinstance(container, QWidget)
    for _ in range(5):
        window.tools.download_button.click()
        _app().processEvents()
        assert splitter.count() == 4
        assert window.tools.isVisible()
        if window.active_panel == "download":
            assert panel.isVisible()
        else:
            assert not panel.isVisible()
            assert container.width() == window.width() - 40 - 1
    assert window.active_panel == "download"
    window.tools.download_button.click()
    _app().processEvents()
    assert window.active_panel is None


def test_close_requested_closes_download_panel() -> None:
    window, panel, _ = _window_with_download()
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    window.tools.download_button.click()
    assert panel.isVisible()
    window.toggle_panel("download")
    assert not panel.isVisible()
    assert window.active_panel is None
