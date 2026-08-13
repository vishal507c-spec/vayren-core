"""WatchlistWidget — watchlist panel: header actions, sort row and stock list."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chart.widgets.symbol_list_widget import SymbolListWidget

_SYMBOL_LABEL_STYLE = "color: palette(placeholder-text);"


class WatchlistWidget(QWidget):
    """Watchlist panel with a compact header row, a sort row and the stock list.

    Fixed top: header row (watchlist selector with dropdown arrow, add (＋),
    reset-view tool (↩), more (⋯)) with a separator below, then "Symbol" /
    "Sort by ▼" with a separator below. Only the stock list scrolls. The
    stock list reuses SymbolListWidget — clicking a symbol emits
    ``symbol_selected``; the tool button emits ``reset_requested`` so the
    window can reuse the chart's existing reset-view action.

    Pure UI: watchlists are session-local memory; no bus, no SQL, no events.
    """

    symbol_selected = Signal(str)
    reset_requested = Signal()

    ALL_STOCKS = "All Stocks"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_symbols: tuple[str, ...] = ()
        self._watchlists: dict[str, tuple[str, ...]] = {self.ALL_STOCKS: ()}
        self._active = self.ALL_STOCKS
        self._sort_ascending = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header_row = self._build_header_row()
        self._header_separator = self._separator()
        self._sort_row = self._build_sort_row()
        self._sort_separator = self._separator()
        layout.addWidget(self._header_row)
        layout.addWidget(self._header_separator)
        layout.addWidget(self._sort_row)
        layout.addWidget(self._sort_separator)

        self._list = SymbolListWidget(self)
        layout.addWidget(self._list, 1)
        self._list.symbol_selected.connect(self.symbol_selected)

        self._watchlist_button.setText(self._active)

    def _separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        return line

    def _build_header_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self._watchlist_button = QToolButton(row)
        self._watchlist_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._watchlist_button.setToolTip("Select watchlist")
        self._watchlist_menu = QMenu(self._watchlist_button)
        self._watchlist_menu.aboutToShow.connect(self._refresh_watchlist_menu)
        self._watchlist_button.setMenu(self._watchlist_menu)

        self._add_button = QToolButton(row)
        self._add_button.setText("+")
        self._add_button.setToolTip("New watchlist")
        self._add_button.clicked.connect(self.add_watchlist)

        self._tool_button = QToolButton(row)
        self._tool_button.setText("↩")
        self._tool_button.setToolTip("Reset chart view (Alt+R)")
        self._tool_button.clicked.connect(self.reset_requested)

        self._more_button = QToolButton(row)
        self._more_button.setText("⋯")
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_button.setToolTip("More watchlist actions")
        self._more_menu = QMenu(self._more_button)
        self._more_menu.addAction("New watchlist", self.add_watchlist)
        self._remove_action = self._more_menu.addAction(
            "Remove watchlist", self.remove_active_watchlist
        )
        self._more_menu.aboutToShow.connect(self._refresh_more_menu)
        self._more_button.setMenu(self._more_menu)

        for button in (
            self._watchlist_button,
            self._add_button,
            self._tool_button,
            self._more_button,
        ):
            button.setAutoRaise(True)
            layout.addWidget(button)
        layout.addStretch(1)
        return row

    def _build_sort_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        symbol_label = QLabel("Symbol", row)
        symbol_label.setStyleSheet(_SYMBOL_LABEL_STYLE)

        self._sort_button = QToolButton(row)
        self._sort_button.setText("Sort by")
        self._sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._sort_button.setToolTip("Sort the stock list")
        self._sort_button.setAutoRaise(True)
        self._sort_menu = QMenu(self._sort_button)
        self._sort_menu.addAction("Name A → Z", lambda: self.sort_by_name(True))
        self._sort_menu.addAction("Name Z → A", lambda: self.sort_by_name(False))
        self._sort_button.setMenu(self._sort_menu)

        layout.addWidget(symbol_label)
        layout.addStretch(1)
        layout.addWidget(self._sort_button)
        return row

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        """Replace the stock universe (existing SymbolsListed flow).

        An identical symbol set is a no-op: symbol clicks re-publish the same
        universe, so rows are never rebuilt for one selected stock.
        """
        symbols = tuple(symbols)
        if symbols == self._all_symbols:
            return
        self._all_symbols = symbols
        self._watchlists[self.ALL_STOCKS] = symbols
        self._refresh_list()

    def select_symbol(self, symbol: str) -> None:
        """Highlight `symbol` in the list (passthrough to the list widget)."""
        self._list.select_symbol(symbol)

    def add_watchlist(self) -> None:
        """Create a new empty watchlist and switch to it (in-memory)."""
        n = 1
        while f"Watchlist {n}" in self._watchlists:
            n += 1
        name = f"Watchlist {n}"
        self._watchlists[name] = ()
        self._set_active(name)

    def remove_active_watchlist(self) -> None:
        """Remove the active watchlist (never the built-in All Stocks)."""
        if self._active == self.ALL_STOCKS:
            return
        del self._watchlists[self._active]
        self._set_active(self.ALL_STOCKS)

    def sort_by_name(self, ascending: bool) -> None:
        """Sort the displayed stock list by symbol name."""
        self._sort_ascending = ascending
        self._refresh_list()

    @property
    def watchlists(self) -> tuple[str, ...]:
        """The available watchlist names, in creation order."""
        return tuple(self._watchlists)

    @property
    def active_watchlist(self) -> str:
        """The currently selected watchlist name."""
        return self._active

    @property
    def symbols(self) -> tuple[str, ...]:
        """The symbols currently displayed, in display order."""
        return tuple(self._list.item(i).text() for i in range(self._list.count()))

    @property
    def current_symbol(self) -> str | None:
        """The currently highlighted symbol, if any."""
        item = self._list.currentItem()
        return item.text() if item is not None else None

    def _set_active(self, name: str) -> None:
        if name not in self._watchlists:
            return
        self._active = name
        self._watchlist_button.setText(name)
        self._refresh_list()

    def _refresh_watchlist_menu(self) -> None:
        self._watchlist_menu.clear()
        for name in self._watchlists:
            action = self._watchlist_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == self._active)
            action.triggered.connect(lambda _checked=False, n=name: self._set_active(n))

    def _refresh_more_menu(self) -> None:
        self._remove_action.setEnabled(self._active != self.ALL_STOCKS)

    def _refresh_list(self) -> None:
        members = self._watchlists.get(self._active, ())
        ordered = sorted(members, reverse=not self._sort_ascending)
        self._list.set_symbols(tuple(ordered))
