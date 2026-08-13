"""OptionsPanel tests — vertical column beside the watchlist, placeholder
buttons, panel order.

State-level geometry tests (no pixel grabs — unreliable offscreen).
"""

from core.event_bus.event_bus import EventBus
from PySide6.QtCore import QCoreApplication, QPoint, QRect
from PySide6.QtWidgets import QApplication, QSplitter, QToolButton

from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.options_panel import OptionsPanel
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.watchlist_widget import WatchlistWidget
from chart.windows.chart_window import ChartWindow

_KEEP_APP: QCoreApplication | None = None


def _app() -> QApplication:
    global _KEEP_APP
    _KEEP_APP = QApplication.instance()
    if not isinstance(_KEEP_APP, QApplication):
        _KEEP_APP = QApplication([])
    return _KEEP_APP


def _window() -> ChartWindow:
    _app()
    watchlist = WatchlistWidget()
    options = OptionsPanel()
    toolbar = TimeframeToolbar()
    window = ChartWindow(CandleChartWidget(), watchlist, options, toolbar, EventBus())
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    return window


def _splitter(window: ChartWindow) -> QSplitter:
    splitter = window.findChild(QSplitter)
    assert splitter is not None
    return splitter


def _rect_in_splitter(window: ChartWindow, widget) -> QRect:
    splitter = _splitter(window)
    origin = widget.mapTo(splitter, QPoint(0, 0))
    return QRect(origin, widget.size())


def test_panel_order_watchlist_options_chart() -> None:
    window = _window()
    splitter = _splitter(window)
    container = window._widget.parentWidget()
    assert container is not None
    assert splitter.indexOf(window.watchlist) == 0
    assert splitter.indexOf(window.options) == 1
    assert splitter.indexOf(container) == 2
    watchlist_rect = _rect_in_splitter(window, window.watchlist)
    options_rect = _rect_in_splitter(window, window.options)
    chart_rect = _rect_in_splitter(window, window._widget)
    assert watchlist_rect.right() <= options_rect.left()
    assert options_rect.right() <= chart_rect.left()


def test_options_has_narrow_fixed_width() -> None:
    window = _window()
    assert window.options.width() == OptionsPanel.OPTIONS_WIDTH
    assert window.options.minimumWidth() == OptionsPanel.OPTIONS_WIDTH
    assert window.options.maximumWidth() == OptionsPanel.OPTIONS_WIDTH


def test_options_spans_chart_height() -> None:
    window = _window()
    options_rect = _rect_in_splitter(window, window.options)
    chart_rect = _rect_in_splitter(window, window._widget)
    assert options_rect.top() <= chart_rect.top()
    assert options_rect.bottom() >= chart_rect.bottom()


def test_options_does_not_overlap_chart() -> None:
    window = _window()
    options_rect = _rect_in_splitter(window, window.options)
    chart_rect = _rect_in_splitter(window, window._widget)
    assert not options_rect.intersects(chart_rect)


def test_options_is_splitter_sibling_not_chart_child() -> None:
    window = _window()
    assert window.options.parentWidget() is _splitter(window)
    assert window.options.parentWidget() is not window._widget.parentWidget()


def test_options_has_exactly_two_placeholder_buttons() -> None:
    options = OptionsPanel()
    options.resize(OptionsPanel.OPTIONS_WIDTH, 200)
    options.show()
    _app().processEvents()
    buttons = options.findChildren(QToolButton)
    assert len(buttons) == 2
    assert [button.text() for button in buttons] == ["◉", "◇"]
    assert all(isinstance(button, QToolButton) for button in buttons)
    assert all(
        button.size().width() == OptionsPanel.BUTTON_SIZE
        and button.size().height() == OptionsPanel.BUTTON_SIZE
        for button in buttons
    )


def test_placeholder_buttons_are_disabled_and_inert() -> None:
    options = OptionsPanel()
    assert not options._button_top.isEnabled()
    assert not options._button_bottom.isEnabled()
    assert options._button_top.menu() is None
    assert options._button_bottom.menu() is None
    options._button_top.click()
    options._button_bottom.click()


def test_placeholder_buttons_stacked_vertically() -> None:
    options = OptionsPanel()
    options.resize(OptionsPanel.OPTIONS_WIDTH, 200)
    options.show()
    _app().processEvents()
    assert options._button_top.x() == options._button_bottom.x()
    assert options._button_top.y() < options._button_bottom.y()
    assert options._button_top.y() + options._button_top.height() <= options._button_bottom.y()
