"""StockChecklistWidget — selection, filtering and universe-order semantics."""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from data.ui.stock_checklist import StockChecklistWidget

UNIVERSE = ("AMBUJACEM", "BPCL", "HDFCBANK", "RELIANCE")


def make_widget() -> StockChecklistWidget:
    widget = StockChecklistWidget()
    widget.set_symbols(UNIVERSE)
    return widget


def click_row(widget: StockChecklistWidget, row: int) -> None:
    item = widget._list.item(row)
    center = widget._list.visualItemRect(item).center()
    QTest.mouseClick(widget._list.viewport(), Qt.MouseButton.LeftButton, pos=center)


def test_symbols_populated_in_universe_order() -> None:
    widget = make_widget()
    assert widget.symbols == UNIVERSE
    assert widget._list.count() == len(UNIVERSE)
    assert [widget._list.item(r).text() for r in range(widget._list.count())] == list(UNIVERSE)
    assert widget.selected_symbols == ()
    assert widget._count_label.text() == "Selected: 0"


def test_click_toggles_exactly_that_stock() -> None:
    widget = make_widget()
    click_row(widget, 1)
    assert widget.selected_symbols == ("BPCL",)
    assert widget.selected_count == 1
    assert widget._count_label.text() == "Selected: 1"
    click_row(widget, 1)
    assert widget.selected_symbols == ()
    assert widget._count_label.text() == "Selected: 0"


def test_selection_keeps_universe_order() -> None:
    widget = make_widget()
    for row in (3, 1, 0):
        click_row(widget, row)
    assert widget.selected_symbols == ("AMBUJACEM", "BPCL", "RELIANCE")


def test_search_filters_visible_rows_and_keeps_selection() -> None:
    widget = make_widget()
    click_row(widget, 0)
    widget._search.setText("BPCL")
    hidden = [widget._list.item(r).isHidden() for r in range(widget._list.count())]
    assert hidden == [True, False, True, True]
    assert widget.selected_symbols == ("AMBUJACEM",)
    widget._search.setText("")
    assert not any(widget._list.item(r).isHidden() for r in range(widget._list.count()))
    assert widget.selected_symbols == ("AMBUJACEM",)


def test_search_is_case_insensitive_substring() -> None:
    widget = make_widget()
    widget._search.setText("hdfc")
    assert not widget._list.item(2).isHidden()
    assert widget._list.item(0).isHidden()


def test_select_all_covers_hidden_rows() -> None:
    widget = make_widget()
    widget._search.setText("AMBU")
    widget.select_all()
    assert widget.selected_count == len(UNIVERSE)
    assert widget.selected_symbols == UNIVERSE
    assert widget._count_label.text() == f"Selected: {len(UNIVERSE)}"


def test_clear_all_empties_selection() -> None:
    widget = make_widget()
    widget.select_all()
    widget.clear_all()
    assert widget.selected_symbols == ()
    assert widget._count_label.text() == "Selected: 0"


def test_selection_changed_emitted_on_each_change() -> None:
    widget = make_widget()
    emitted: list[int] = []
    widget.selection_changed.connect(lambda: emitted.append(1))
    click_row(widget, 0)
    widget.select_all()
    widget.clear_all()
    assert len(emitted) == 3


def test_busy_disable_blocks_clicks() -> None:
    widget = make_widget()
    widget.setEnabled(False)
    assert not widget.isEnabled()


def test_list_consumes_available_height_and_scrolls() -> None:
    widget = StockChecklistWidget()
    widget.set_symbols(tuple(f"SYM{i:03d}" for i in range(80)))
    widget.resize(200, 600)
    widget.show()
    QApplication.processEvents()
    assert widget._list.height() == 8 * 26 + 2
    assert widget._list.verticalScrollBar().maximum() > 0


def test_list_grows_with_widget_and_shrinks_to_minimum() -> None:
    widget = StockChecklistWidget()
    widget.set_symbols(UNIVERSE)
    widget.resize(200, 600)
    widget.show()
    QApplication.processEvents()
    tall = widget._list.height()
    widget.resize(200, 200)
    QApplication.processEvents()
    short = widget._list.height()
    assert tall > short
    assert short >= widget._list.minimumHeight()


def test_set_selected_toggles_one_stock() -> None:
    widget = make_widget()
    widget.set_selected("BPCL", True)
    assert widget.selected_symbols == ("BPCL",)
    widget.set_selected("GHOST", True)
    assert widget.selected_symbols == ("BPCL",)
    widget.set_selected("BPCL", False)
    assert widget.selected_symbols == ()


def test_chips_show_up_to_limit_then_count_note() -> None:
    widget = StockChecklistWidget()
    widget.show()
    QApplication.processEvents()
    widget.set_symbols(tuple(f"S{i:02d}" for i in range(1, 11)))
    assert not widget._chips.isVisible()
    for symbol in ("S01", "S02"):
        widget.set_selected(symbol, True)
    assert widget._chips.isVisible()
    assert widget._selected_note.text() == ""
    widget.select_all()
    assert not widget._chips.isVisible()
    assert widget._selected_note.text() == "10 stocks selected"
    widget._chips.remove_requested.emit("S01")
    assert not widget.is_selected(0)


def test_delegate_paints_accent_checkbox() -> None:
    widget = make_widget()
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 200, 160))
    widget.setPalette(pal)
    widget.show()
    QApplication.processEvents()
    click_row(widget, 0)
    image = widget.grab().toImage()
    dpr = widget.devicePixelRatio()

    origin = widget._list.viewport().mapTo(widget, QRect(0, 0, 0, 0).topLeft())

    def teal_count(row: int) -> int:
        item = widget._list.item(row)
        row_rect = widget._list.visualItemRect(item)
        box = QRect(
            origin.x() + row_rect.left() + 6,
            origin.y() + row_rect.top() + (row_rect.height() - 14) // 2,
            14,
            14,
        )
        count = 0
        for x in range(int(box.left() * dpr), int(box.right() * dpr)):
            for y in range(int(box.top() * dpr), int(box.bottom() * dpr)):
                pixel = image.pixelColor(x, y)
                if pixel.red() < 60 and pixel.green() > 150 and pixel.blue() > 110:
                    count += 1
        return count

    assert teal_count(0) >= 40
    assert teal_count(1) == 0


def test_delegate_paints_hover_background() -> None:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    widget = make_widget()
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(30, 60, 90))
    widget.setPalette(pal)

    image = QImage(200, 26, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0))
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    option.initFrom(widget._list)
    option.rect = QRect(0, 0, 200, 26)
    option.font = widget._list.font()
    option.state |= QStyle.StateFlag.State_MouseOver
    widget._list.itemDelegate().paint(painter, option, widget._list.model().index(0, 0))
    painter.end()

    pixel = image.pixelColor(25, 13)
    assert pixel.red() == 30
    assert pixel.green() == 60
    assert pixel.blue() == 90
