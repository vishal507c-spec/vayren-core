"""SymbolListWidget — left sidebar listing available stocks."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem


class SymbolListWidget(QListWidget):
    """Lists discovered stock symbols; a click emits the chosen symbol.

    Pure UI. No bus, no SQL, no events — selection surfaces as a Qt signal.
    """

    symbol_selected = Signal(str)

    def __init__(self, parent: QListWidget | None = None) -> None:
        super().__init__(parent)
        self.itemClicked.connect(self._on_item_clicked)

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        """Replace the sidebar contents with the given symbols."""
        self.clear()
        for symbol in symbols:
            self.addItem(QListWidgetItem(symbol))

    def select_symbol(self, symbol: str) -> None:
        """Highlight the row matching `symbol` (if present)."""
        for row in range(self.count()):
            if self.item(row).text() == symbol:
                self.setCurrentRow(row)
                return

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.symbol_selected.emit(item.text())
