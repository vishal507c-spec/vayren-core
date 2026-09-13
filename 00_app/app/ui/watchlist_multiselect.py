"""WatchlistMultiSelect — compact selection readout + searchable symbol manager.

The main Strategy Lab must stay visually identical whether 1, 10, 100 or 527
symbols are selected (spec §7). So the inline surface is a *summary*, not a
wall of chips:

* 0 selected   → empty hint ("No symbols selected — manage your universe")
* ≤ ``_CHIP_LIMIT`` → removable chips (fast deselection for small universes)
* > ``_CHIP_LIMIT`` → a single honest count line ("527 stocks selected")

All selection work happens inside the **Manage Symbols** popup: search,
selected count, Select All / Clear All, a scrollable checkable list and the
selected state. Selection stores symbols only — market history is loaded only
when a backtest runs.

Presentation only: no bus, no SQL, no events. Follows the lab theme.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from app.ui import lab_theme as t

# Above this many selections the chip row is replaced by a summary line.
# 12 keeps the chips useful for hand-picked universes while guaranteeing
# the main screen never renders hundreds of chips.
_CHIP_LIMIT = 12

_CHIP_QSS = (
    f"QFrame {{ background: {t.PANEL2}; border: 1px solid {t.BORDER}; border-radius: 3px; }}"
)

_CHIP_STALE_QSS = (
    f"QFrame {{ background: {t.BG1}; border: 1px dashed {t.MUTED}; border-radius: 3px; }}"
)

_CHIP_TEXT_QSS = f"color: {t.TEXT}; font-size: 12px; font-weight: 600; border: none;"

_CHIP_STALE_TEXT_QSS = f"color: {t.MUTED}; font-size: 12px; border: none;"

_REMOVE_QSS = (
    f"QToolButton {{ background: transparent; border: none; color: {t.MUTED};"
    f" font-size: 13px; padding: 0 3px; }}"
    f"QToolButton:hover {{ color: {t.NEG}; }}"
)

_EMPTY_QSS = f"color: {t.MUTED}; font-size: 13px;"


class _FlowLayout(QLayout):
    """Minimal left-to-right wrapping layout for chips (no new dependency)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(4)

    def addItem(self, item) -> None:  # type: ignore[no-untyped-def]  # noqa: N802,ANN001
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # type: ignore[no-untyped-def]  # noqa: N802,ANN001
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # type: ignore[no-untyped-def]  # noqa: N802,ANN001
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout((0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:  # type: ignore[no-untyped-def]  # noqa: N802,ANN001
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self):  # type: ignore[no-untyped-def]  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # type: ignore[no-untyped-def]  # noqa: N802
        from PySide6.QtCore import QSize

        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect, apply: bool) -> int:  # type: ignore[no-untyped-def]  # noqa: ANN001
        x = rect.x() if hasattr(rect, "x") else rect[0]
        y = rect.y() if hasattr(rect, "y") else rect[1]
        width = rect.width() if hasattr(rect, "width") else rect[2]
        row_height = 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > width and x > (rect.x() if hasattr(rect, "x") else rect[0]):
                x = rect.x() if hasattr(rect, "x") else rect[0]
                y += row_height + self.spacing()
                row_height = 0
            if apply:
                from PySide6.QtCore import QRect as _QRect

                item.setGeometry(_QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + self.spacing()
            row_height = max(row_height, hint.height())
        return y + row_height


class WatchlistMultiSelect(QWidget):
    """Universe selector bound to the Market Watchlist.

    Signals:
        selection_changed: emitted with the selected symbols (selection order)
            whenever the selection changes.
    """

    selection_changed = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._available: tuple[str, ...] = ()
        self._selected: list[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(t.SP_SM)

        # ── inline summary: one honest line, never hundreds of chips ──
        self._summary = QLabel("", self)
        self._summary.setStyleSheet(f"color: {t.TEXT}; font-size: {t.FS_BODY}px; font-weight: 700;")
        self._summary.setVisible(False)
        lay.addWidget(self._summary)

        # chips (only rendered for small, hand-picked universes)
        self._chip_scroll = QScrollArea(self)
        self._chip_scroll.setWidgetResizable(True)
        self._chip_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chip_scroll.setStyleSheet(f"background: transparent; {t.SCROLLBAR_QSS}")
        self._chip_scroll.setMaximumHeight(70)
        self._chip_host = QWidget(self._chip_scroll)
        self._chip_flow = _FlowLayout(self._chip_host)
        self._chip_host.setLayout(self._chip_flow)
        self._chip_host.setStyleSheet("background: transparent;")
        self._chip_scroll.setWidget(self._chip_host)
        lay.addWidget(self._chip_scroll)

        self._empty_hint = QLabel("No symbols selected — choose the universe to test.", self)
        self._empty_hint.setStyleSheet(_EMPTY_QSS)
        self._empty_hint.setWordWrap(True)
        lay.addWidget(self._empty_hint)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(t.SP_MD)
        self._add_button = QPushButton("Manage Symbols…", self)
        self._add_button.setStyleSheet(t.BUTTON_QSS)
        self._add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_button.setToolTip("Choose which stocks this backtest runs on")
        self._add_button.clicked.connect(self._open_popup)
        row.addWidget(self._add_button)
        self._count_label = QLabel("", self)
        self._count_label.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_SMALL}px;")
        row.addWidget(self._count_label)
        row.addStretch(1)
        self._clear_button = QPushButton("Clear", self)
        self._clear_button.setStyleSheet(t.BUTTON_QSS)
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.setToolTip("Remove every selected symbol")
        self._clear_button.clicked.connect(self.clear_selection)
        row.addWidget(self._clear_button)
        lay.addLayout(row)

        self._menu: QMenu | None = None
        self._refresh_chips()

    # ── public API ──────────────────────────────────────────────

    @property
    def available_symbols(self) -> tuple[str, ...]:
        """The current watchlist universe backing the choices."""
        return self._available

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        """Replace the available universe (watchlist feed). Selection survives.

        Already-selected symbols are never silently dropped; ones missing
        from the new universe render as stale chips until removed.
        """
        self._available = tuple(symbols)
        self._refresh_chips()

    def selected_symbols(self) -> tuple[str, ...]:
        """The selected symbols in selection order."""
        return tuple(self._selected)

    def set_selected_symbols(self, symbols: tuple[str, ...] | list[str]) -> None:
        """Replace the selection (deduped, order preserved)."""
        wanted = [s for s in dict.fromkeys(symbols) if s]
        if wanted == self._selected:
            return
        self._selected = wanted
        self._refresh_chips()
        self.selection_changed.emit(tuple(self._selected))

    def clear_selection(self) -> None:
        """Remove every selected symbol."""
        if not self._selected:
            return
        self._selected = []
        self._refresh_chips()
        self.selection_changed.emit(())

    def has_selection(self) -> bool:
        """True when at least one symbol is selected."""
        return bool(self._selected)

    def selected_count(self) -> int:
        """Number of selected symbols (0 when empty)."""
        return len(self._selected)

    # ── inline readout ──────────────────────────────────────────

    def _refresh_chips(self) -> None:
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        available = set(self._available)
        stale = [s for s in self._selected if s not in available]
        show_chips = 0 < len(self._selected) <= _CHIP_LIMIT
        if show_chips:
            for symbol in self._selected:
                is_stale = symbol not in available
                chip = QFrame(self._chip_host)
                chip.setStyleSheet(_CHIP_STALE_QSS if is_stale else _CHIP_QSS)
                chip_lay = QHBoxLayout(chip)
                chip_lay.setContentsMargins(7, 2, 3, 2)
                chip_lay.setSpacing(t.SP_SM)
                label = QLabel(symbol, chip)
                label.setStyleSheet(_CHIP_STALE_TEXT_QSS if is_stale else _CHIP_TEXT_QSS)
                if is_stale:
                    label.setToolTip("Not in the current Market Watchlist")
                    chip.setToolTip(
                        "Not in the current Market Watchlist — remove or switch watchlist"
                    )
                chip_lay.addWidget(label)
                remove = QToolButton(chip)
                remove.setText("×")
                remove.setCursor(Qt.CursorShape.PointingHandCursor)
                remove.setStyleSheet(_REMOVE_QSS)
                remove.clicked.connect(lambda _c=False, s=symbol: self._remove_symbol(s))
                chip_lay.addWidget(remove)
                self._chip_flow.addWidget(chip)

        count = len(self._selected)
        noun = "stock" if count == 1 else "stocks"
        # Large universes collapse to one line — the main screen never
        # carries hundreds of chips (spec §7).
        self._summary.setText(f"{count} {noun} selected" if count > _CHIP_LIMIT else "")
        self._summary.setVisible(count > _CHIP_LIMIT)
        if count > _CHIP_LIMIT and stale:
            self._summary.setToolTip(
                f"{len(stale)} selected symbol(s) are not in the current Market Watchlist"
            )
        else:
            self._summary.setToolTip("")

        self._empty_hint.setVisible(count == 0)
        self._chip_scroll.setVisible(show_chips)
        inline = f"{count} selected" if count and count <= _CHIP_LIMIT else ""
        self._count_label.setText(inline)
        self._clear_button.setEnabled(count > 0)

    def _remove_symbol(self, symbol: str) -> None:
        if symbol in self._selected:
            self._selected.remove(symbol)
            self._refresh_chips()
            self.selection_changed.emit(tuple(self._selected))

    def _toggle_symbol(self, symbol: str, checked: bool) -> None:
        if checked and symbol not in self._selected:
            self._selected.append(symbol)
            self._refresh_chips()
            self.selection_changed.emit(tuple(self._selected))
        elif not checked and symbol in self._selected:
            self._remove_symbol(symbol)
        self._sync_popup_checks()
        self._sync_popup_count()

    # ── Manage Symbols popup ────────────────────────────────────

    def _build_popup(self) -> QMenu:
        """Build the searchable checklist popup (exec'd by :meth:`_open_popup`)."""
        menu = QMenu(self)
        menu.setStyleSheet(t.MENU_QSS)
        menu.setMinimumWidth(max(320, self._add_button.width() + 160))
        menu.setMinimumHeight(360)
        host = QWidget(menu)
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(12, 10, 12, 10)
        host_lay.setSpacing(t.SP_MD)

        title = QLabel("MANAGE SYMBOLS", host)
        title.setStyleSheet(t.section_title(12))
        host_lay.addWidget(title)

        self._popup_count = QLabel("", host)
        self._popup_count.setStyleSheet(
            f"color: {t.ACCENT}; font-size: {t.FS_SMALL}px; font-weight: 700;"
        )
        host_lay.addWidget(self._popup_count)

        search = QLineEdit(host)
        search.setPlaceholderText("Search watchlist…")
        search.setStyleSheet(t.INPUT_QSS)
        host_lay.addWidget(search)

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(t.SP_MD)
        all_btn = QPushButton("Select All", host)
        all_btn.setStyleSheet(t.BUTTON_QSS)
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn = QPushButton("Clear All", host)
        none_btn.setStyleSheet(t.BUTTON_QSS)
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tools.addWidget(all_btn)
        tools.addWidget(none_btn)
        tools.addStretch(1)
        host_lay.addLayout(tools)

        self._popup_list = QListWidget(host)
        self._popup_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: none; }"
            f"QListWidget::item {{ padding: 5px 4px; color: {t.TEXT};"
            f" font-size: {t.FS_TABLE}px; }}"
            f"QListWidget::item:selected {{ background: {t.PANEL2}; }}"
        )
        self._popup_list.setMouseTracking(True)
        host_lay.addWidget(self._popup_list, 1)

        self._popup_empty = QLabel("Watchlist is empty — add symbols from the Market tab.", host)
        self._popup_empty.setStyleSheet(_EMPTY_QSS)
        self._popup_empty.setWordWrap(True)
        self._popup_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        host_lay.addWidget(self._popup_empty)

        action = QWidgetAction(menu)
        action.setDefaultWidget(host)
        menu.addAction(action)

        # populate
        self._rebuild_popup_list("")
        search.textChanged.connect(lambda text: self._rebuild_popup_list(text))
        self._popup_list.itemChanged.connect(self._on_popup_item_changed)
        all_btn.clicked.connect(lambda: self._set_all_visible(True))
        none_btn.clicked.connect(lambda: self._set_all_visible(False))
        self._popup_search = search
        self._sync_popup_count()
        return menu

    def _open_popup(self) -> None:
        menu = self._build_popup()
        self._menu = menu
        pos = self._add_button.mapToGlobal(QPoint(0, self._add_button.height()))
        self._popup_search.setFocus(Qt.FocusReason.PopupFocusReason)
        menu.exec(pos)
        self._menu = None

    def _rebuild_popup_list(self, needle: str) -> None:
        """Recreate visible rows matching `needle` (instant search).

        Check states always derive from the selection, so filtering never
        loses state. Select All / Clear All apply to the currently visible rows.
        """
        needle = needle.strip().lower()
        lst = self._popup_list
        lst.blockSignals(True)
        lst.clear()
        shown = 0
        for symbol in self._available:
            if needle and needle not in symbol.lower():
                continue
            item = QListWidgetItem(symbol, lst)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if symbol in self._selected else Qt.CheckState.Unchecked
            )
            shown += 1
        lst.blockSignals(False)
        self._popup_empty.setVisible(shown == 0)
        lst.setVisible(shown > 0)
        self._sync_popup_count(visible=shown)

    def _sync_popup_count(self, visible: int | None = None) -> None:
        """Honest selected/total readout so the count is never guessed."""
        counter = getattr(self, "_popup_count", None)
        if counter is None:
            return
        total = len(self._available)
        if visible is None:
            visible = total
        noun = "stock" if total == 1 else "stocks"
        text = f"{len(self._selected)} of {total} {noun} selected"
        if visible != total:
            text = f"{text}  ·  {visible} shown"
        counter.setText(text)

    def _on_popup_item_changed(self, item: QListWidgetItem) -> None:
        self._toggle_symbol(item.text(), item.checkState() == Qt.CheckState.Checked)

    def _sync_popup_checks(self) -> None:
        lst = getattr(self, "_popup_list", None)
        if lst is None:
            return
        selected = set(self._selected)
        lst.blockSignals(True)
        try:
            for row in range(lst.count()):
                item = lst.item(row)
                if item is not None:
                    want = (
                        Qt.CheckState.Checked
                        if item.text() in selected
                        else Qt.CheckState.Unchecked
                    )
                    if item.checkState() != want:
                        item.setCheckState(want)
        finally:
            lst.blockSignals(False)

    def _set_all_visible(self, checked: bool) -> None:
        lst = getattr(self, "_popup_list", None)
        if lst is None:
            return
        changed = False
        for row in range(lst.count()):
            item = lst.item(row)
            if item is None or item.isHidden():
                continue
            symbol = item.text()
            if checked and symbol not in self._selected:
                self._selected.append(symbol)
                changed = True
            elif not checked and symbol in self._selected:
                self._selected.remove(symbol)
                changed = True
        if changed:
            self._refresh_chips()
            self.selection_changed.emit(tuple(self._selected))
        self._sync_popup_checks()
        self._sync_popup_count()


__all__ = ["WatchlistMultiSelect"]
