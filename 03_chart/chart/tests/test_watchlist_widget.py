"""WatchlistWidget tests — header layout, sort row, watchlist actions and
symbol selection wiring.

State-level tests (no pixel grabs — unreliable offscreen).
"""

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QFrame

from chart.widgets.watchlist_widget import WatchlistWidget

_KEEP_APP: QCoreApplication | None = None

_SYMBOLS = ("NETWEB", "TCS", "VOLTAMP", "SPY")


def _app() -> QApplication:
    global _KEEP_APP
    _KEEP_APP = QApplication.instance()
    if not isinstance(_KEEP_APP, QApplication):
        _KEEP_APP = QApplication([])
    return _KEEP_APP


def _widget() -> WatchlistWidget:
    _app()
    widget = WatchlistWidget()
    widget.resize(240, 400)
    widget.set_symbols(_SYMBOLS)
    return widget


# ── header layout ────────────────────────────────────────────────────────


def test_header_row_controls_left_to_right() -> None:
    widget = _widget()
    widget.show()
    _app().processEvents()
    buttons = (
        widget._watchlist_button,
        widget._add_button,
        widget._tool_button,
        widget._more_button,
    )
    ys = [button.y() for button in buttons]
    assert len(set(ys)) == 1
    xs = [button.x() for button in buttons]
    assert xs == sorted(xs)
    assert widget._watchlist_button.x() < widget._add_button.x()
    assert widget._add_button.x() < widget._tool_button.x()
    assert widget._tool_button.x() < widget._more_button.x()


def test_watchlist_button_shows_active_name_with_dropdown() -> None:
    widget = _widget()
    assert widget._watchlist_button.text() == WatchlistWidget.ALL_STOCKS
    assert widget._watchlist_button.menu() is widget._watchlist_menu
    assert widget._watchlist_button.popupMode() == (
        widget._watchlist_button.ToolButtonPopupMode.InstantPopup
    )


def test_sort_row_below_header() -> None:
    widget = _widget()
    widget.show()
    _app().processEvents()
    assert widget._sort_row.y() > widget._header_row.y()
    assert widget._header_row.height() > 0
    assert widget._sort_button.text() == "Sort by"
    assert widget._sort_button.menu() is widget._sort_menu


# ── symbol list ──────────────────────────────────────────────────────────


def test_set_symbols_populates_list() -> None:
    widget = _widget()
    assert widget.symbols == tuple(sorted(_SYMBOLS))


def test_symbol_click_emits_symbol_selected() -> None:
    widget = _widget()
    captured: list[str] = []
    widget.symbol_selected.connect(captured.append)
    widget._list.itemClicked.emit(widget._list.item(0))
    assert captured == [_SYMBOLS[0]]


def test_select_symbol_highlights_row() -> None:
    widget = _widget()
    widget.select_symbol("TCS")
    assert widget._list.currentItem().text() == "TCS"


# ── sorting ──────────────────────────────────────────────────────────────


def test_sort_default_ascending() -> None:
    widget = _widget()
    assert widget.symbols == tuple(sorted(_SYMBOLS))


def test_sort_descending_reorders_list() -> None:
    widget = _widget()
    widget.sort_by_name(False)
    assert widget.symbols == tuple(sorted(_SYMBOLS, reverse=True))
    widget.sort_by_name(True)
    assert widget.symbols == tuple(sorted(_SYMBOLS))


def test_sort_menu_actions_change_order() -> None:
    widget = _widget()
    widget._sort_menu.actions()[1].trigger()
    assert widget.symbols == tuple(sorted(_SYMBOLS, reverse=True))


# ── separators and scrolling ──────────────────────────────────────────────


def test_separators_between_header_sort_and_list() -> None:
    widget = _widget()
    widget.show()
    _app().processEvents()
    assert widget._header_separator.frameShape() == QFrame.Shape.HLine
    assert widget._sort_separator.frameShape() == QFrame.Shape.HLine
    assert widget._header_row.y() < widget._header_separator.y()
    assert widget._header_separator.y() < widget._sort_row.y()
    assert widget._sort_row.y() < widget._sort_separator.y()
    assert widget._sort_separator.y() < widget._list.y()


def test_stock_rows_styled_with_separators_and_selection_border() -> None:
    widget = _widget()
    widget.show()
    _app().processEvents()
    sheet = widget._list.styleSheet()
    assert "::item" in sheet
    assert "border-bottom" in sheet
    assert "border-radius" in sheet
    assert "palette(highlight)" in sheet
    assert widget._list.visualItemRect(widget._list.item(0)).height() > 0


def test_only_list_scrolls_header_and_sort_fixed() -> None:
    widget = _widget()
    widget.set_symbols(tuple(f"S{i:03d}" for i in range(200)))
    widget.show()
    _app().processEvents()
    header_y = widget._header_row.y()
    sort_y = widget._sort_row.y()
    scrollbar = widget._list.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    _app().processEvents()
    assert scrollbar.value() > 0
    assert widget._header_row.y() == header_y
    assert widget._sort_row.y() == sort_y


# ── watchlist actions ────────────────────────────────────────────────────


def test_add_watchlist_creates_and_switches() -> None:
    widget = _widget()
    widget.add_watchlist()
    assert widget.watchlists == (WatchlistWidget.ALL_STOCKS, "Watchlist 1")
    assert widget.active_watchlist == "Watchlist 1"
    assert widget.symbols == ()
    widget.add_watchlist()
    assert widget.watchlists == (WatchlistWidget.ALL_STOCKS, "Watchlist 1", "Watchlist 2")
    assert widget.active_watchlist == "Watchlist 2"


def test_switch_back_to_all_stocks_shows_all_symbols() -> None:
    widget = _widget()
    widget.add_watchlist()
    widget._set_active(WatchlistWidget.ALL_STOCKS)
    assert widget.symbols == tuple(sorted(_SYMBOLS))
    assert widget._watchlist_button.text() == WatchlistWidget.ALL_STOCKS


def test_remove_active_watchlist_falls_back_to_all_stocks() -> None:
    widget = _widget()
    widget.add_watchlist()
    widget.remove_active_watchlist()
    assert widget.watchlists == (WatchlistWidget.ALL_STOCKS,)
    assert widget.active_watchlist == WatchlistWidget.ALL_STOCKS
    assert widget.symbols == tuple(sorted(_SYMBOLS))


def test_all_stocks_cannot_be_removed() -> None:
    widget = _widget()
    widget.remove_active_watchlist()
    assert widget.watchlists == (WatchlistWidget.ALL_STOCKS,)
    assert widget.active_watchlist == WatchlistWidget.ALL_STOCKS


def test_watchlist_menu_lists_and_checks_active() -> None:
    widget = _widget()
    widget.add_watchlist()
    widget._refresh_watchlist_menu()
    actions = widget._watchlist_menu.actions()
    assert [action.text() for action in actions] == [
        WatchlistWidget.ALL_STOCKS,
        "Watchlist 1",
    ]
    assert not actions[0].isChecked()
    assert actions[1].isChecked()


def test_more_menu_remove_disabled_for_all_stocks() -> None:
    widget = _widget()
    widget._refresh_more_menu()
    assert not widget._remove_action.isEnabled()
    widget.add_watchlist()
    widget._refresh_more_menu()
    assert widget._remove_action.isEnabled()


# ── reset tool ───────────────────────────────────────────────────────────


def test_tool_button_emits_reset_requested() -> None:
    widget = _widget()
    captured: list[bool] = []
    widget.reset_requested.connect(lambda: captured.append(True))
    widget._tool_button.click()
    assert len(captured) == 1
