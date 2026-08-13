"""SymbolListWidget — stock list: rows emit the chosen symbol."""

from market.models.symbol_quote import SymbolQuote
from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from chart.renderer.candle_renderer import CandleRenderer

_QUOTE_ROLE = Qt.ItemDataRole.UserRole

_LIST_STYLE = """
QListWidget {
    border: none;
    outline: 0;
    font-size: 12px;
}
QListWidget::item {
    padding: 3px 8px;
    border-bottom: 1px solid palette(midlight);
    border-radius: 2px;
}
QListWidget::item:hover {
    background: palette(alternate-base);
}
QListWidget::item:selected {
    background: palette(highlight);
    color: palette(highlighted-text);
    font-weight: 600;
    border-bottom: 1px solid palette(highlight);
    border-radius: 2px;
}
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: palette(mid);
    border-radius: 3px;
    min-height: 20px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: palette(dark);
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    width: 0;
    height: 0;
}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: transparent;
}
"""

_ROW_LEFT_PAD = 8
_ROW_RIGHT_PAD = 8
_ROW_TOP_PAD = 2
_ROW_BOTTOM_PAD = 3
_ROW_HAIRLINE = 1
_SELECTED_EDGE = 2
_CHANGE_FONT_PX = 11


class _SymbolRowDelegate(QStyledItemDelegate):
    """Two-line row rendering: symbol + price on line 1, change % on line 2.

    Left column: symbol (line 1). Right-aligned column: price (line 1) and
    change % (line 2) in the app's bull/bear colors. Rows without a quote
    render symbol-only, vertically centered. Selection = subtle panel tint
    with a 2px accent edge so the change % colors stay readable; hover =
    alternate-base tint. Hairline separators between rows. All text derives
    from the view's font so rows stay consistent across the app.
    """

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        quote: SymbolQuote | None = index.data(_QUOTE_ROLE)
        symbol = index.data(Qt.ItemDataRole.DisplayRole)
        rect = option.rect
        palette = option.palette
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        if selected:
            painter.fillRect(rect, palette.color(QPalette.ColorRole.Midlight))
            painter.fillRect(
                QRect(rect.left(), rect.top(), _SELECTED_EDGE, rect.height()),
                palette.color(QPalette.ColorRole.Highlight),
            )
        elif hovered:
            painter.fillRect(rect, palette.color(QPalette.ColorRole.AlternateBase))
        painter.fillRect(
            QRect(rect.left(), rect.bottom() - _ROW_HAIRLINE, rect.width(), _ROW_HAIRLINE),
            palette.color(QPalette.ColorRole.Midlight),
        )

        text_color = palette.color(QPalette.ColorRole.Text)
        line = QFontMetrics(option.font).height()
        left = rect.left() + _ROW_LEFT_PAD
        right = rect.right() - _ROW_RIGHT_PAD
        width = right - left

        if quote is None:
            painter.setFont(option.font)
            painter.setPen(text_color)
            painter.drawText(
                QRect(left, rect.top(), width, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                symbol,
            )
        else:
            top = rect.top() + _ROW_TOP_PAD
            symbol_font = QFont(option.font)
            symbol_font.setWeight(QFont.Weight.DemiBold)
            price_font = QFont(option.font)
            price_font.setWeight(QFont.Weight.DemiBold)
            change_font = QFont(option.font)
            change_font.setPixelSize(_CHANGE_FONT_PX)

            painter.setFont(symbol_font)
            painter.setPen(text_color)
            painter.drawText(
                QRect(left, top, width, line),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                symbol,
            )

            painter.setFont(price_font)
            painter.drawText(
                QRect(left, top, width, line),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{quote.price:,.2f}",
            )

            painter.setFont(change_font)
            color = CandleRenderer.BULL if quote.change_pct >= 0 else CandleRenderer.BEAR
            painter.setPen(QColor(color))
            painter.drawText(
                QRect(left, top + line + 1, width, line),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{quote.change_pct:.2f}%",
            )
        painter.restore()

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        _ = index
        height = (
            QFontMetrics(option.font).height() * 2 + _ROW_TOP_PAD + _ROW_BOTTOM_PAD + _ROW_HAIRLINE
        )
        return QSize(0, height)


class SymbolListWidget(QListWidget):
    """Lists discovered stock symbols; a click emits the chosen symbol.

    Rows are dense, uniformly sized, two-line: symbol + price on the first
    line, change % on the second (bull/bear colored) — populated only from
    real quotes attached via ``set_quotes``. Rows without a quote render
    symbol-only. Hairline separators, soft hover highlight and a subtle
    accent-edge selection state (no bulky OS-style box). A slim 6px
    scrollbar keeps the list visually quiet.

    Pure UI. No bus, no SQL, no events — selection surfaces as a Qt signal.
    """

    symbol_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_LIST_STYLE)
        self.setItemDelegate(_SymbolRowDelegate(self))
        self.itemClicked.connect(self._on_item_clicked)

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        """Replace the sidebar contents with the given symbols.

        The rebuild is batched: updates are suspended so the list relayouts
        and repaints exactly once instead of once per row.
        """
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            self.addItems(symbols)
        finally:
            self.setUpdatesEnabled(True)

    def set_quotes(self, quotes: dict[str, SymbolQuote]) -> None:
        """Attach the latest real quote per symbol to its row, if one exists.

        Rows without a quote keep symbol-only. Called once after population
        and again after any rebuild (sort, watchlist switch).
        """
        for row in range(self.count()):
            item = self.item(row)
            item.setData(_QUOTE_ROLE, quotes.get(item.text()))

    def select_symbol(self, symbol: str) -> None:
        """Highlight the row matching `symbol` (if present)."""
        for row in range(self.count()):
            if self.item(row).text() == symbol:
                self.setCurrentRow(row)
                return

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.symbol_selected.emit(item.text())
