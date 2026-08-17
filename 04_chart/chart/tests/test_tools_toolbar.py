"""ChartToolsToolbar tests — left rail structure, states and chart boundary.

State-level geometry and state tests (no pixel grabs — unreliable offscreen).
"""

import re

from core.event_bus.event_bus import EventBus
from PySide6.QtCore import QCoreApplication, QSize
from PySide6.QtWidgets import QApplication, QFrame, QSplitter, QToolButton

from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.tools_toolbar import (
    BUTTON_SIZE,
    TOOLBAR_WIDTH,
    ChartToolsToolbar,
)
from chart.widgets.watchlist_widget import WatchlistWidget
from chart.windows.chart_window import ChartWindow

_KEEP_APP: QCoreApplication | None = None


def _app() -> QApplication:
    global _KEEP_APP
    _KEEP_APP = QApplication.instance()
    if not isinstance(_KEEP_APP, QApplication):
        _KEEP_APP = QApplication([])
    return _KEEP_APP


def _toolbar() -> ChartToolsToolbar:
    _app()
    toolbar = ChartToolsToolbar()
    toolbar.show()
    _app().processEvents()
    return toolbar


def _window() -> ChartWindow:
    _app()
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
    )
    window.resize(1280, 760)
    window.show()
    _app().processEvents()
    return window


def _kind(button: QToolButton) -> str:
    return str(button.property("kind"))


def _separators(toolbar: ChartToolsToolbar) -> list[QFrame]:
    frames = toolbar.findChildren(QFrame)
    return [frame for frame in frames if frame.objectName() == "separator"]


def test_toolbar_has_fixed_compact_width() -> None:
    _app()
    toolbar = ChartToolsToolbar()
    assert toolbar.sizeHint().width() == TOOLBAR_WIDTH
    assert toolbar.minimumSize().width() == TOOLBAR_WIDTH
    assert toolbar.maximumSize().width() == TOOLBAR_WIDTH
    toolbar.resize(800, 400)
    _app().processEvents()
    assert toolbar.width() == TOOLBAR_WIDTH


def test_toolbar_button_count_and_labels() -> None:
    toolbar = _toolbar()
    assert len(toolbar.buttons) == 2
    for button in toolbar.buttons:
        assert button.toolTip(), f"{_kind(button)} has no tooltip"
        assert not button.icon().isNull(), f"{_kind(button)} has no icon"
        assert button.iconSize() == QSize(16, 16)
        assert button.size() == QSize(BUTTON_SIZE, BUTTON_SIZE)


def test_watchlist_active_by_default() -> None:
    toolbar = _toolbar()
    checked = [button for button in toolbar.buttons if button.isChecked()]
    assert [button for button in checked if _kind(button) == "watchlist"]
    assert len(checked) == 1


def test_toolbar_renders_two_nav_icons_with_empty_space_below() -> None:
    toolbar = _toolbar()
    by_kind = {_kind(button): button for button in toolbar.buttons}
    assert [_kind(button) for button in toolbar.buttons] == ["watchlist", "download"]
    first, second = by_kind["watchlist"], by_kind["download"]
    assert toolbar.buttons[0] is first
    assert toolbar.buttons[1] is second
    for button in (first, second):
        assert button.parentWidget() is toolbar
    assert first.y() < second.y()
    assert second.y() - (first.y() + first.height()) < 30
    assert second.geometry().bottom() < toolbar.height()
    stretch_item = toolbar._layout.itemAt(2)
    assert stretch_item is not None and stretch_item.spacerItem() is not None


def test_watchlist_nav_button_is_first_and_independent() -> None:
    toolbar = _toolbar()
    by_kind = {_kind(button): button for button in toolbar.buttons}
    assert toolbar.buttons[0] is by_kind["watchlist"]
    assert by_kind["watchlist"].isCheckable()
    assert not toolbar._mode_group.id(by_kind["watchlist"]) >= 0
    by_kind["watchlist"].click()
    assert not by_kind["watchlist"].isChecked()


def test_download_nav_button_is_second_and_independent() -> None:
    toolbar = _toolbar()
    by_kind = {_kind(button): button for button in toolbar.buttons}
    assert toolbar.buttons[1] is by_kind["download"]
    assert by_kind["download"].isCheckable()
    assert not toolbar._mode_group.id(by_kind["download"]) >= 0
    by_kind["download"].click()
    assert by_kind["download"].isChecked()
    assert by_kind["watchlist"].isChecked()
    by_kind["download"].click()
    assert not by_kind["download"].isChecked()


def test_toolbar_has_no_internal_separators() -> None:
    toolbar = _toolbar()
    assert _separators(toolbar) == []


def test_toolbar_stylesheet_uses_palette_roles_only() -> None:
    toolbar = _toolbar()
    sheet = toolbar.styleSheet()
    assert not re.search(r"#[0-9a-fA-F]{6}", sheet), "hardcoded color in QSS"


def test_toolbar_first_watchlist_second_chart_last() -> None:
    window = _window()
    tools = window.tools
    watchlist = window.watchlist
    widget = window._widget
    splitter = tools.parentWidget()
    assert splitter is not None
    assert isinstance(splitter, QSplitter)
    assert watchlist.parentWidget() is splitter
    assert splitter.indexOf(tools) == 0
    assert tools.x() == 0
    assert tools.width() == TOOLBAR_WIDTH
    assert watchlist.x() > tools.x() + tools.width()
    container = widget.parentWidget()
    assert container is not None
    assert container is not splitter
    assert container.x() > watchlist.x() + watchlist.width()
    assert widget.x() == 0
    assert widget.width() == container.width()
    assert widget.y() == window.toolbar.height()
    assert container.x() + container.width() <= splitter.width()


def test_layout_stable_across_resizes() -> None:
    window = _window()
    tools = window.tools
    watchlist = window.watchlist
    widget = window._widget
    container = widget.parentWidget()
    assert container is not None
    for size in ((900, 500), (1600, 1000), (1280, 760)):
        window.resize(*size)
        _app().processEvents()
        assert tools.width() == TOOLBAR_WIDTH
        assert tools.x() == 0
        assert watchlist.x() > tools.x() + tools.width()
        assert widget.x() == 0
        assert widget.width() == container.width()
        assert widget.width() >= 1
