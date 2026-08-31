"""StockChecklistWidget — searchable multi-stock selection list.

Checkboxes are painted by a custom delegate, so clicking a row toggles
exactly that stock while the "Selected: N" count stays tied to the widget's
selection set, independent of the current search filter. Selections survive
filtering, Select All covers the whole universe (hidden rows included) and
Clear All empties the set. Palette roles only - no hard-coded colors - so
the widget follows the VAYREN dark theme. No bus, no events, no engine.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

_LIST_MIN_HEIGHT = 120

_LIST_VISIBLE_ROWS = 8  # stock rows shown at a time; the rest scroll internally

_CHIP_LIMIT = 6  # selected stocks shown as chips; beyond that a count line

_LABEL_STYLE = "color: palette(placeholder-text); font-size: 11px; font-weight: 600;"

_COUNT_STYLE = "color: palette(placeholder-text); font-size: 11px;"

_WIDGET_STYLE = """
StockChecklistWidget QLineEdit {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 3px;
    padding: 2px 4px;
    font-size: 11px;
}
StockChecklistWidget QLineEdit:focus {
    border-color: palette(highlight);
}
StockChecklistWidget QPushButton {
    color: palette(highlight);
    padding: 2px 4px;
    font-size: 11px;
}
StockChecklistWidget QPushButton:hover {
    color: palette(text);
}
StockChecklistWidget QPushButton:pressed {
    color: palette(placeholder-text);
}
StockChecklistWidget QListWidget {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 3px;
    outline: none;
}
StockChecklistWidget QListWidget::item {
    border: none;
}
StockChecklistWidget QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
StockChecklistWidget QScrollBar::handle:vertical {
    background: palette(mid);
    border-radius: 3px;
    min-height: 20px;
}
StockChecklistWidget QScrollBar::handle:vertical:hover {
    background: palette(dark);
}
StockChecklistWidget QScrollBar::add-line:vertical,
StockChecklistWidget QScrollBar::sub-line:vertical {
    height: 0;
}
StockChecklistWidget QScrollBar::add-page:vertical,
StockChecklistWidget QScrollBar::sub-page:vertical {
    background: transparent;
}
_ChipStrip QPushButton {
    color: palette(highlight);
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 10px;
}
_ChipStrip QPushButton:hover {
    color: palette(text);
    border-color: palette(highlight);
}
_ChipStrip QPushButton:pressed {
    color: palette(placeholder-text);
}
"""


class _FlowLayout(QLayout):
    """Minimal flow layout for the selected-stock chip strip."""

    def __init__(self, parent: QWidget | None = None, margin: int = 0, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class _ChipStrip(QWidget):
    """Compact chips of the selected stocks; clicking a chip removes it."""

    remove_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_WIDGET_STYLE)
        self.setFixedHeight(40)
        self._flow = _FlowLayout(self, margin=0, spacing=4)
        self.setLayout(self._flow)

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for symbol in symbols:
            chip = _SlimButton(f"{symbol} ×", self)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            chip.setMinimumHeight(16)
            chip.clicked.connect(lambda _=False, s=symbol: self.remove_requested.emit(s))
            self._flow.addWidget(chip)


class _SlimLabel(QLabel):
    """QLabel that never forces the panel wider than its allotted width.

    Long unbreakable words (e.g. ticker names in the count) would otherwise
    inflate the minimum size hint and widen the whole side panel; the text
    is meant to wrap or clip inside the fixed panel instead.
    """

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class _ChecklistList(QListWidget):
    """Stock list whose preferred height matches its row count.

    The default 8-row preference would cap the checklist's size hint and
    stop the form from claiming the available middle height; the list is
    the stretch target of the panel, so its preference scales with the
    universe (capped so a 500-stock universe cannot balloon the panel).
    """

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        rows = min(max(self.count(), 1), _LIST_VISIBLE_ROWS)
        return QSize(hint.width(), rows * 26)


class _SlimButton(QPushButton):
    """QPushButton with the same shrink-tolerant minimum size hint."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class _ChecklistDelegate(QStyledItemDelegate):
    """Paints a checkbox (accent fill when selected) plus the symbol text."""

    def __init__(self, owner: StockChecklistWidget) -> None:
        super().__init__(owner)
        self._owner = owner

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        _ = index
        return QSize(super().sizeHint(option, index).width(), 26)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        pal = self._owner.palette()
        rect = option.rect
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, pal.color(QPalette.ColorRole.AlternateBase))

        check = QRect(rect.left() + 6, rect.top() + (rect.height() - 14) // 2, 14, 14)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._owner.is_selected(index.row()):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pal.color(QPalette.ColorRole.Highlight))
            painter.drawRoundedRect(check, 3, 3)
            pen = QPen(pal.color(QPalette.ColorRole.HighlightedText), 1.6)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            left = check.left()
            top = check.top()
            painter.drawPolyline(
                [
                    QPointF(left + 3.2, top + 7.4),
                    QPointF(left + 6.2, top + 10.2),
                    QPointF(left + 11.0, top + 4.2),
                ]
            )
        else:
            pen = QPen(pal.color(QPalette.ColorRole.Mid), 1)
            painter.setPen(pen)
            painter.setBrush(pal.color(QPalette.ColorRole.Base))
            painter.drawRoundedRect(check, 3, 3)

        text_color = pal.color(QPalette.ColorRole.Text)
        if not (option.state & QStyle.StateFlag.State_Enabled):
            text_color = pal.color(QPalette.ColorRole.PlaceholderText)
        painter.setPen(QPen(text_color))
        painter.setFont(option.font)
        text_left = check.right() + 8
        text_rect = QRect(
            text_left,
            rect.top(),
            max(0, rect.right() - text_left),
            rect.height(),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            index.data(),
        )


class StockChecklistWidget(QWidget):
    """Searchable list of stocks with per-stock checkboxes.

    Pure input surface: exposes the selected symbols and a changed signal.
    Selections are kept in a set, so filtering only hides rows and never
    disturbs the current selection. No bus, no events, no engine.
    """

    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbols: tuple[str, ...] = ()
        self._selected: set[str] = set()
        self.setStyleSheet(_WIDGET_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search stocks...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        bar = QHBoxLayout()
        bar.setSpacing(2)
        self._select_all_button = _SlimButton("Select All", self)
        self._select_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_all_button.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self._select_all_button.clicked.connect(self.select_all)
        self._clear_all_button = _SlimButton("Clear All", self)
        self._clear_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_all_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._clear_all_button.clicked.connect(self.clear_all)
        self._count_label = _SlimLabel("Selected: 0", self)
        self._count_label.setStyleSheet(_COUNT_STYLE)
        self._count_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        bar.addWidget(self._select_all_button)
        bar.addWidget(self._clear_all_button)
        bar.addStretch(1)
        bar.addWidget(self._count_label)
        layout.addLayout(bar)

        self._chips = _ChipStrip(self)
        self._chips.remove_requested.connect(self._on_chip_removed)
        self._chips.hide()
        layout.addWidget(self._chips)

        self._selected_note = _SlimLabel("", self)
        self._selected_note.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        self._selected_note.hide()
        layout.addWidget(self._selected_note)

        self._list = _ChecklistList(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.setMouseTracking(True)
        self._list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._list.setMinimumHeight(_LIST_MIN_HEIGHT)
        self._list.setMaximumHeight(_LIST_VISIBLE_ROWS * 26 + 2)  # 8 rows + frame borders
        self._list.setItemDelegate(_ChecklistDelegate(self))
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list, 1)

    # ── universe ─────────────────────────────────────────────────────────────

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        """Replace the universe; selection and filter reset."""
        self._symbols = symbols
        self._selected.clear()
        self._search.clear()
        self._list.clear()
        for symbol in symbols:
            item = QListWidgetItem(symbol)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
        self._update_count()

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    # ── selection ────────────────────────────────────────────────────────────

    def is_selected(self, row: int) -> bool:
        if 0 <= row < len(self._symbols):
            return self._symbols[row] in self._selected
        return False

    @property
    def selected_symbols(self) -> tuple[str, ...]:
        return tuple(symbol for symbol in self._symbols if symbol in self._selected)

    @property
    def selected_count(self) -> int:
        return len(self._selected)

    def select_all(self) -> None:
        """Select the whole universe, including rows hidden by the filter."""
        self._selected = set(self._symbols)
        self._update_count()
        self._list.viewport().update()

    def clear_all(self) -> None:
        self._selected.clear()
        self._update_count()
        self._list.viewport().update()

    def set_selected(self, symbol: str, selected: bool) -> None:
        """Set one stock's selection state (used by the chip strip)."""
        if symbol not in self._symbols:
            return
        if selected:
            self._selected.add(symbol)
        else:
            self._selected.discard(symbol)
        self._update_count()
        self._list.viewport().update()

    # ── internals ────────────────────────────────────────────────────────────

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        symbol = item.text()
        if symbol in self._selected:
            self._selected.discard(symbol)
        else:
            self._selected.add(symbol)
        self._update_count()
        self._list.viewport().update()

    def _update_count(self) -> None:
        self._count_label.setText(f"Selected: {self.selected_count}")
        self._update_chips()
        self.selection_changed.emit()

    def _update_chips(self) -> None:
        selected = self.selected_symbols
        if selected and len(selected) <= _CHIP_LIMIT:
            self._chips.set_symbols(selected)
            self._chips.show()
            self._selected_note.clear()
        elif selected:
            self._chips.hide()
            self._selected_note.setText(f"{len(selected)} stocks selected")
        else:
            self._chips.hide()
            self._selected_note.clear()

    def _on_chip_removed(self, symbol: str) -> None:
        self.set_selected(symbol, False)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().upper()
        for row in range(self._list.count()):
            item = self._list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().upper())
