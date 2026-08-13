"""SymbolListWidget — stock list: rows emit the chosen symbol."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

_LIST_STYLE = """
QListWidget {
    border: none;
    outline: 0;
}
QListWidget::item {
    padding: 4px 8px;
    border-bottom: 1px solid palette(midlight);
    border-radius: 4px;
}
QListWidget::item:hover {
    background: palette(alternate-base);
}
QListWidget::item:selected {
    background: palette(highlight);
    color: palette(highlightedText);
    border-bottom: 1px solid palette(highlight);
    border-radius: 4px;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: palette(mid);
    border-radius: 4px;
    min-height: 24px;
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


class SymbolListWidget(QListWidget):
    """Lists discovered stock symbols; a click emits the chosen symbol.

    Rows are compact and uniformly sized with subtle separators, a soft hover
    highlight and a smooth rounded selection state (no bulky box). A slim,
    rounded scrollbar keeps the list visually quiet. Only the symbol text is
    rendered — additional fields (price, change) land here when real data
    exists.

    Pure UI. No bus, no SQL, no events — selection surfaces as a Qt signal.
    """

    symbol_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_LIST_STYLE)
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

    def select_symbol(self, symbol: str) -> None:
        """Highlight the row matching `symbol` (if present)."""
        for row in range(self.count()):
            if self.item(row).text() == symbol:
                self.setCurrentRow(row)
                return

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.symbol_selected.emit(item.text())
