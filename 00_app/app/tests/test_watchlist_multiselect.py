"""WatchlistMultiSelect — chips, search, toggle, stale-marking contracts."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.ui.watchlist_multiselect import WatchlistMultiSelect


@pytest.fixture()
def selector(qt_app: QApplication):  # type: ignore[no-untyped-def]
    _ = qt_app
    widget = WatchlistMultiSelect()
    widget.show()
    return widget


def test_empty_selection_initially(selector: WatchlistMultiSelect) -> None:
    assert selector.selected_symbols() == ()
    assert not selector.has_selection()
    assert selector._empty_hint.isVisibleTo(selector)


def test_toggle_via_api_emits_once(selector: WatchlistMultiSelect) -> None:
    selector.set_symbols(("AAA", "BBB"))
    seen: list[tuple[str, ...]] = []
    selector.selection_changed.connect(lambda s: seen.append(tuple(s)))
    selector.set_selected_symbols(("AAA", "BBB"))
    assert selector.selected_symbols() == ("AAA", "BBB")
    assert seen == [("AAA", "BBB")]
    assert selector.has_selection()


def test_remove_symbol_updates_chips(selector: WatchlistMultiSelect) -> None:
    selector.set_symbols(("AAA", "BBB", "CCC"))
    selector.set_selected_symbols(("AAA", "BBB"))
    selector._remove_symbol("AAA")
    assert selector.selected_symbols() == ("BBB",)


def test_selection_survives_universe_change_and_marks_stale(
    selector: WatchlistMultiSelect,
) -> None:
    selector.set_symbols(("AAA", "BBB"))
    selector.set_selected_symbols(("AAA", "BBB"))
    # Watchlist switches elsewhere — selection must survive, AAA marked stale.
    selector.set_symbols(("BBB", "CCC"))
    assert selector.selected_symbols() == ("AAA", "BBB")
    assert selector._chip_flow.count() == 2  # chips still rendered


def test_clear_selection(selector: WatchlistMultiSelect) -> None:
    selector.set_symbols(("AAA",))
    selector.set_selected_symbols(("AAA",))
    selector.clear_selection()
    assert selector.selected_symbols() == ()
    assert not selector.has_selection()


def test_popup_list_search_filters(selector: WatchlistMultiSelect) -> None:
    selector.set_symbols(("360ONE", "INFY", "HDFCBANK"))
    menu = selector._build_popup()
    assert selector._popup_list.count() == 3
    selector._rebuild_popup_list("inf")
    assert selector._popup_list.count() == 1
    item = selector._popup_list.item(0)
    assert item is not None and item.text() == "INFY"
    menu.close()


def test_empty_universe_shows_empty_state(selector: WatchlistMultiSelect) -> None:
    selector.set_symbols(())
    menu = selector._build_popup()
    assert selector._popup_list.count() == 0
    assert not selector._popup_list.isVisibleTo(selector._popup_empty.parentWidget() or selector)
    assert selector._popup_empty.text() != ""
    menu.close()


def test_check_toggle_updates_selection(selector: WatchlistMultiSelect) -> None:
    selector.set_symbols(("AAA", "BBB"))
    menu = selector._build_popup()
    item = selector._popup_list.item(0)
    assert item is not None
    item.setCheckState(Qt.CheckState.Checked)
    QApplication.processEvents()
    assert selector.selected_symbols() == ("AAA",)
    item.setCheckState(Qt.CheckState.Unchecked)
    QApplication.processEvents()
    assert selector.selected_symbols() == ()
    menu.close()
