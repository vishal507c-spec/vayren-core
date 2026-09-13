"""Strategy Lab Workspace — BUY / SELL / COMPARE tri-mode research screen.

Rebuilt presentation layer: professional institutional quant-terminal density.
All backend logic, state management, signals, and public API preserved.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from backtest.models.result import StrategyResult
from PySide6.QtCore import QDate, QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from strategy.ui.code_editor import CodeEditor

from app.ui import lab_theme as t
from app.ui.watchlist_multiselect import WatchlistMultiSelect

if TYPE_CHECKING:
    from PySide6.QtGui import QResizeEvent

    from app.ui.stock_ranking import StockRankRow

_DEFAULT_SLIPPAGE_PCT = 0.02
_DEFAULT_COMMISSION_PCT = 0.03

_BUY_ACCENT = t.ACCENT  # canonical UI_DESIGN_SYSTEM.md s3
_SELL_ACCENT = t.NEG  # canonical UI_DESIGN_SYSTEM.md s3


class StrategyViewMode(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    COMPARE = "COMPARE"


# ── shared helpers ────────────────────────────────────────────────────


def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {t.BORDER}; background-color: {t.BORDER};")
    line.setFixedHeight(1)
    return line


def _inr(value: float) -> str:
    """Indian-grouping money text, e.g. -1234567.8 → '-12,34,567.80'."""
    negative = value < 0
    whole, frac = divmod(abs(round(value * 100) / 100), 1)
    digits = str(int(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    text = f"{digits}.{int(round(frac * 100)):02d}"
    return ("-" if negative else "") + text


def _relative_time(mtime: float) -> str:
    """Compact recency label: 2m ago / 5h ago / 3d ago / 12 Mar."""
    delta = datetime.now().timestamp() - mtime
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)}d ago"
    return datetime.fromtimestamp(mtime).strftime("%d %b")


def _compact_inr(value: float) -> str:
    """Compact Indian money for dense context lines: ₹10L, ₹1.25Cr, ₹50K."""
    amount = float(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1e7:
        text = f"{amount / 1e7:.2f}".rstrip("0").rstrip(".")
        return f"{sign}₹{text}Cr"
    if amount >= 1e5:
        text = f"{amount / 1e5:.2f}".rstrip("0").rstrip(".")
        return f"{sign}₹{text}L"
    if amount >= 1e3:
        return f"{sign}₹{amount / 1e3:.0f}K"
    return f"{sign}₹{amount:.0f}"


def _short_date(iso: str) -> str:
    """'2026-01-02' → '02 Jan 26'; unparseable input passes through."""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d %b %y")
    except (ValueError, TypeError):
        return iso or "—"


def _signed_inr(value: float) -> str:
    """'-₹1,18,788.00' — sign before the currency symbol, never '₹-'."""
    return ("-" if value < 0 else "") + "₹" + _inr(abs(float(value)))


# ── run lifecycle vocabulary ──────────────────────────────────────────
_STATE_TEXT: dict[str, tuple[str, str]] = {
    "ready": ("● READY", t.MUTED),
    "running": ("● RUNNING", t.ACCENT),
    "aggregating": ("◌ AGGREGATING", t.ACCENT),
    "finalizing": ("◌ FINALIZING", t.ACCENT),
    "complete": ("✓ COMPLETE", t.POS),
    "failed": ("✕ FAILED", t.NEG),
}


# ═══════════════════════════════════════════════════════════════════════
#  EXPERIMENT CONTEXT BAR — orientation strip
# ═══════════════════════════════════════════════════════════════════════


class ExperimentContextBar(QWidget):
    """Single-line experiment orientation: strategy · mode · universe · tf · dates · capital · state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExperimentContextBar")
        self.setFixedHeight(t.H_CONTEXT)
        self.setStyleSheet(
            f"QWidget#ExperimentContextBar {{ background: {t.BG0};"
            f" border-bottom: 1px solid {t.BORDER}; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(t.SP_XL, 0, t.SP_XL, 0)
        lay.setSpacing(t.SP_SM)

        self._strategy = self._seg(t.FS_SMALL, t.TEXT, 700)
        self._mode = self._seg(t.FS_SMALL, t.TEXT2, 700)
        self._universe = self._seg(t.FS_SMALL, t.TEXT2, 500)
        self._timeframe = self._seg(t.FS_SMALL, t.TEXT2, 500)
        self._dates = self._seg(t.FS_SMALL, t.TEXT2, 500)
        self._capital = self._seg(t.FS_SMALL, t.TEXT2, 500)

        self._dots: list[QLabel] = []
        segments = (
            self._strategy,
            self._mode,
            self._universe,
            self._timeframe,
            self._dates,
            self._capital,
        )
        for i, widget in enumerate(segments):
            if i > 0:
                dot = self._seg(t.FS_SMALL, t.BORDER, 500)
                dot.setText("·")
                lay.addWidget(dot)
                self._dots.append(dot)
            lay.addWidget(widget)

        lay.addStretch(1)
        self._status = self._seg(t.FS_SMALL, t.MUTED, 700)
        lay.addWidget(self._status)
        self._compact = False
        self._tooltip = ""
        self.set_context()

    @staticmethod
    def _seg(size: int, color: str, weight: int) -> QLabel:
        label = QLabel("", None)
        label.setStyleSheet(f"color: {color}; font-size: {size}px; font-weight: {weight};")
        return label

    def set_context(
        self,
        *,
        strategy: str = "",
        mode: str = "",
        universe: str = "",
        timeframe: str = "",
        dates: str = "",
        capital: str = "",
        state: str = "ready",
    ) -> None:
        """Render the full experiment context in one deterministic pass."""
        self._strategy.setText(strategy or "NO STRATEGY")
        self._strategy.setStyleSheet(
            f"color: {t.TEXT if strategy else t.MUTED}; font-size: {t.FS_SMALL}px; font-weight: 700;"
        )
        tone = t.TEXT2
        if mode.startswith("BUY"):
            tone = _BUY_ACCENT
        elif mode.startswith("SELL"):
            tone = _SELL_ACCENT
        elif mode.startswith("COMPARE"):
            tone = t.ACCENT
        self._mode.setText(mode or "—")
        self._mode.setStyleSheet(f"color: {tone}; font-size: {t.FS_SMALL}px; font-weight: 700;")
        self._universe.setText(universe or "—")
        self._timeframe.setText(timeframe or "—")
        self._dates.setText(dates or "—")
        self._capital.setText(capital or "—")
        text, color = _STATE_TEXT.get(state, _STATE_TEXT["ready"])
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}; font-size: {t.FS_SMALL}px; font-weight: 700;")
        self._tooltip = (
            f"{strategy or 'NO STRATEGY'}  ·  {mode or '—'}  ·  {universe or '—'}"
            f"  ·  {timeframe or '—'}  ·  {dates or '—'}  ·  {capital or '—'}  ·  {text}"
        )
        self.setToolTip(self._tooltip)
        self._apply_compact()

    def set_compact(self, compact: bool) -> None:
        """Fold secondary segments on short viewports."""
        if compact == self._compact:
            return
        self._compact = compact
        self._apply_compact()

    def _apply_compact(self) -> None:
        for widget in (self._dates, self._capital):
            widget.setVisible(not self._compact)
        # Hide dots before dates and capital (indices 3,4 in the dots list)
        if len(self._dots) >= 5:
            self._dots[3].setVisible(not self._compact)
            self._dots[4].setVisible(not self._compact)


# ═══════════════════════════════════════════════════════════════════════
#  STRATEGY LIBRARY — left sidebar
# ═══════════════════════════════════════════════════════════════════════


class _StrategyRow(QWidget):
    """One library row: status dot + name · modified · three-dot menu."""

    menu_requested = Signal()

    def __init__(self, name: str, stamp: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(t.SP_MD, t.SP_XS, t.SP_XS, t.SP_XS)
        lay.setSpacing(t.SP_SM)
        dot = QLabel("●", self)
        dot.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
        lay.addWidget(dot)
        name_label = QLabel(name, self)
        name_label.setStyleSheet(f"color: {t.TEXT}; font-size: {t.FS_SMALL}px; font-weight: 600;")
        lay.addWidget(name_label, 1)
        if stamp:
            time_label = QLabel(stamp, self)
            time_label.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
            lay.addWidget(time_label)
        more = QToolButton(self)
        more.setText("⋮")
        more.setFixedSize(16, 16)
        more.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 2px;"
            f" color: {t.MUTED}; font-size: {t.FS_LABEL}px; padding: 0; }}"
            f"QToolButton:hover {{ background: {t.PANEL2}; color: {t.TEXT}; }}"
        )
        more.clicked.connect(self._open_menu)
        lay.addWidget(more)
        self._name = name

    def _open_menu(self) -> None:
        self.menu_requested.emit()


class _DeleteConfirmDialog(QDialog):
    """Tiny non-dominant confirmation for destructive strategy delete."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Delete Strategy")
        self.setFixedWidth(320)
        self.setStyleSheet(
            f"QDialog {{ background: {t.PANEL}; }}"
            f"QLabel {{ color: {t.TEXT}; font-size: {t.FS_SMALL}px; }}"
            f"QPushButton {{ {t.BUTTON_QSS} }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(8)
        head = QLabel("Delete strategy?", self)
        head.setStyleSheet(f"color: {t.TEXT}; font-size: {t.FS_TABLE}px; font-weight: 600;")
        lay.addWidget(head)
        sub = QLabel(f'"{name}" — this action cannot be undone.')
        sub.setObjectName("sub")
        sub.setStyleSheet(f"color: {t.TEXT2}; font-size: {t.FS_LABEL}px;")
        lay.addWidget(sub)
        lay.addSpacing(4)
        buttons = QDialogButtonBox(self)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        delete = buttons.addButton(QDialogButtonBox.StandardButton.Yes)
        assert cancel is not None and delete is not None
        cancel.setText("Cancel")
        delete.setText("Delete")
        delete.setStyleSheet(t.BUTTON_QSS)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class StrategyLibraryPanel(QWidget):
    """Left column — strategy library: search, rows, row menu, safe delete."""

    strategy_selected = Signal(str)
    new_strategy_requested = Signal()
    duplicate_requested = Signal(str)
    rename_requested = Signal(str, str)
    delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(180)
        self.setMaximumWidth(280)
        self.setStyleSheet(f"background: {t.BG0}; border-right: 1px solid {t.BORDER};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(t.SP_MD, t.SP_MD, t.SP_MD, t.SP_MD)
        lay.setSpacing(t.SP_MD)

        head = QLabel("STRATEGIES", self)
        head.setStyleSheet(t.label(t.TEXT2, t.FS_LABEL, 700, 0.8))
        lay.addWidget(head)

        self._new_btn = QPushButton("+  NEW", self)
        self._new_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {t.BORDER};"
            f" color: {t.TEXT}; padding: 5px; border-radius: 3px; font-size: {t.FS_LABEL}px;"
            f" font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: {t.ACCENT_DIM}; color: {t.ACCENT}; }}"
        )
        self._new_btn.clicked.connect(self.new_strategy_requested.emit)
        lay.addWidget(self._new_btn)

        search_row = QHBoxLayout()
        search_row.setSpacing(t.SP_SM)
        icon = QLabel("🔍", self)
        icon.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
        search_row.addWidget(icon)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search…")
        self._search.setStyleSheet(t.INPUT_QSS)
        self._search.textChanged.connect(self._refilter)
        search_row.addWidget(self._search, 1)
        lay.addLayout(search_row)

        # Library taxonomy (§13): ALL / ★ favorites / recent — session-local
        # sets, no fake persistence. Filter buttons, not new widgets.
        self._favorites: set[str] = set()
        self._recent: list[str] = []
        self._lib_filter = "all"
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(t.SP_XS)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_btns: dict[str, QPushButton] = {}
        for key, text in (("all", "ALL"), ("fav", "★"), ("recent", "RECENT")):
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setStyleSheet(t.TOOL_QSS)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, _k=key: self.set_lib_filter(_k))
            self._filter_group.addButton(btn)
            self._filter_btns[key] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch(1)
        lay.addLayout(filter_row)
        self._filter_row = filter_row
        self.strategy_selected.connect(self._track_recent)

        self._list = QListWidget(self)
        self._list.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: none; }"
            f"QListWidget::item {{ border: 1px solid transparent;"
            f" border-radius: 2px; margin: 1px 0; }}"
            f"QListWidget::item:selected {{ background: {t.PANEL2};"
            f" border: 1px solid {t.ACCENT_DIM}; }}"
            f"QListWidget::item:hover {{ background: {t.BG2}; }}"
        )
        self._list.itemClicked.connect(
            lambda item: self.strategy_selected.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._row_menu)
        lay.addWidget(self._list, 1)

        self._empty = QWidget(self)
        empty_lay = QVBoxLayout(self._empty)
        empty_lay.setContentsMargins(8, 24, 8, 24)
        empty_lay.setSpacing(6)
        empty_lay.addStretch(1)
        icon_label = QLabel("◧", self._empty)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_DISPLAY}px;")
        empty_lay.addWidget(icon_label)
        msg = QLabel("No strategies yet", self._empty)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(f"color: {t.TEXT}; font-size: {t.FS_SMALL}px; font-weight: 600;")
        empty_lay.addWidget(msg)
        self._empty_msg = msg
        sub = QLabel(
            "Create your first strategy to start\nbuilding, backtesting and analyzing.",
            self._empty,
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
        empty_lay.addWidget(sub)
        self._empty_sub = sub
        empty_lay.addSpacing(6)
        empty_btn = QPushButton("+  NEW STRATEGY", self._empty)
        empty_btn.setStyleSheet(
            f"QPushButton {{ background: {t.ACCENT}; border: none; border-radius: 3px;"
            f" color: {t.ACCENT_DEEP}; padding: 6px 14px; font-size: {t.FS_LABEL}px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {t.ACCENT_DIM}; }}"
        )
        empty_btn.clicked.connect(self.new_strategy_requested.emit)
        empty_lay.addWidget(empty_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_lay.addStretch(1)
        self._empty.setVisible(False)
        lay.addWidget(self._empty)
        self._items: list[tuple[str, float]] = []
        self._filter_text = ""

    def set_strategies(self, items: list[str] | list[tuple[str, float]]) -> None:
        normalized: list[tuple[str, float]] = [
            (i, 0.0) if isinstance(i, str) else (str(i[0]), float(i[1])) for i in items
        ]
        self._items = sorted(normalized, key=lambda x: x[0].lower())
        live = {name for name, _ in self._items}
        self._favorites.intersection_update(live)
        self._recent = [name for name in self._recent if name in live]
        self._refilter(self._filter_text)

    def set_lib_filter(self, key: str) -> None:
        if key not in ("all", "fav", "recent"):
            return
        self._lib_filter = key
        btn = self._filter_btns.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self._refilter(self._filter_text)

    def toggle_favorite(self, name: str) -> None:
        if name in self._favorites:
            self._favorites.discard(name)
        elif any(name == item for item, _ in self._items):
            self._favorites.add(name)
        self._refilter(self._filter_text)

    def _track_recent(self, name: str) -> None:
        if name in self._recent:
            self._recent.remove(name)
        self._recent.insert(0, name)
        del self._recent[10:]
        if self._lib_filter == "recent":
            self._refilter(self._filter_text)

    def names(self) -> list[str]:
        return [name for name, _ in self._items]

    def select_name(self, name: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) == name:
                self._list.blockSignals(True)
                self._list.setCurrentRow(row)
                self._list.blockSignals(False)
                return
        # Filtered out of view — fall back to ALL so wiring never silently
        # fails to select.
        if self._lib_filter != "all" and any(name == item for item, _ in self._items):
            self.set_lib_filter("all")
            self.select_name(name)

    def current_name(self) -> str | None:
        item = self._list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def request_rename(self, name: str) -> None:
        self._ask_rename(name)

    def _refilter(self, text: str) -> None:
        self._filter_text = text
        needle = text.strip().lower()
        if self._lib_filter == "recent":
            ordered = [(n, m) for n, m in ((n, self._mtime_of(n)) for n in self._recent)]
        elif self._lib_filter == "fav":
            ordered = sorted(
                [(n, m) for n, m in self._items if n in self._favorites],
                key=lambda x: x[0].lower(),
            )
        else:
            ordered = list(self._items)
        self._list.clear()
        shown = 0
        for name, mtime in ordered:
            if needle and needle not in name.lower():
                continue
            stamp = _relative_time(mtime) if mtime else ""
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(item)
            display = f"★ {name}" if name in self._favorites else name
            row = _StrategyRow(display, stamp, self._list)
            row.menu_requested.connect(lambda n=name: self._row_menu_for(n))
            self._list.setItemWidget(item, row)
            item.setSizeHint(row.sizeHint())
            shown += 1
        if not self._items:
            self._empty_msg.setText("No strategies yet")
            self._empty_sub.setText(
                "Create your first strategy to start\nbuilding, backtesting and analyzing."
            )
            self._empty.setVisible(True)
            self._list.setVisible(False)
        elif shown == 0:
            self._empty_msg.setText("No matches")
            self._empty_sub.setText("Adjust the search or library filter.")
            self._empty.setVisible(True)
            self._list.setVisible(False)
        else:
            self._empty.setVisible(False)
            self._list.setVisible(True)
        self._new_btn.setVisible(bool(self._items))
        self._search.setVisible(bool(self._items) or bool(needle))

    def _mtime_of(self, name: str) -> float:
        for item, mtime in self._items:
            if item == name:
                return mtime
        return 0.0

    def _row_menu(self, pos) -> None:  # noqa: ANN001
        item = self._list.itemAt(pos)
        if item is None:
            return
        name = str(item.data(Qt.ItemDataRole.UserRole))
        menu = self._build_menu(name)
        menu.exec(self._list.mapToGlobal(pos))

    def _row_menu_for(self, name: str) -> None:
        from PySide6.QtGui import QCursor

        menu = self._build_menu(name)
        menu.exec(QCursor.pos())

    def _build_menu(self, name: str) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(t.MENU_QSS)
        menu.addAction("Open", lambda: self.strategy_selected.emit(name))
        fav_label = "☆ Unfavorite" if name in self._favorites else "★ Favorite"
        menu.addAction(fav_label, lambda: self.toggle_favorite(name))
        menu.addAction("Rename", lambda: self._ask_rename(name))
        menu.addAction("Duplicate", lambda: self.duplicate_requested.emit(name))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self._confirm_delete(name))
        return menu

    def _ask_rename(self, old: str) -> None:
        from PySide6.QtWidgets import QInputDialog

        new, ok = QInputDialog.getText(self, "Rename Strategy", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            self.rename_requested.emit(old, new.strip())

    def _confirm_delete(self, name: str) -> None:
        if _DeleteConfirmDialog(name, self).exec() == QDialog.DialogCode.Accepted:
            self.delete_requested.emit(name)


# ═══════════════════════════════════════════════════════════════════════
#  STATUS CHIP
# ═══════════════════════════════════════════════════════════════════════


class _StatusChip(QLabel):
    """Compact ● STATUS indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("● READY", parent)
        self.set_ready()

    def set_ready(self) -> None:
        self.setText("● READY")
        self.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px; font-weight: 600;")

    def set_modified(self) -> None:
        self.setText("● MODIFIED")
        self.setStyleSheet(f"color: {t.WARN}; font-size: {t.FS_LABEL}px; font-weight: 600;")

    def set_error(self) -> None:
        self.setText("● ERROR")
        self.setStyleSheet(f"color: {t.NEG}; font-size: {t.FS_LABEL}px; font-weight: 600;")


# ═══════════════════════════════════════════════════════════════════════
#  PARAMS PANE — compact parameter grid
# ═══════════════════════════════════════════════════════════════════════


class ParamsPane(QWidget):
    """Compact parameter grid for the compiled strategy inputs."""

    param_changed = Signal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        head = QLabel("PARAMETERS", self)
        head.setStyleSheet(t.label(t.TEXT2, 9, 700, 0.8))
        outer.addWidget(head)
        outer.addWidget(_hline())

        self._grid_host = QWidget(self)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 2, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(6)
        outer.addWidget(self._grid_host)
        outer.addStretch(1)

        self._hint = QLabel('No parameters — use input(value, "label") in code.', self)
        self._hint.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
        outer.addWidget(self._hint)
        self._spins: dict[str, QDoubleSpinBox] = {}

    def refresh(self, code: str) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self._spins.clear()
        count = 0
        try:
            from strategy.language import compile_strategy

            compiled = compile_strategy(code)
            entries: list[tuple[str, str, float]] = []
            seen: set[str] = set()
            for spec in getattr(compiled, "param_specs", ()) or ():
                if spec.key in seen:
                    continue
                seen.add(spec.key)
                entries.append((spec.key, spec.label, float(spec.default)))
            if not entries:
                entries = [(k, k, float(v)) for k, v in compiled.param_defaults.items()]
            for key, label_text, default in entries:
                key_label = QLabel(label_text, self._grid_host)
                key_label.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
                spin = QDoubleSpinBox(self._grid_host)
                spin.setStyleSheet(t.INPUT_QSS)
                spin.setMinimumWidth(100)
                if "C1" in label_text or "c1" in key:
                    spin.setRange(0.5, 2.0)
                    spin.setDecimals(2)
                    spin.setSingleStep(0.05)
                elif "C4" in label_text or "c4" in key:
                    spin.setRange(0.3, 1.5)
                    spin.setDecimals(2)
                    spin.setSingleStep(0.05)
                elif "RSI" in label_text or "rsi" in key:
                    spin.setRange(50, 80)
                    spin.setDecimals(0)
                    spin.setSingleStep(1)
                else:
                    spin.setRange(0, 100000)
                    spin.setDecimals(2)
                spin.setValue(float(default))
                spin.valueChanged.connect(lambda v, k=key: self.param_changed.emit(k, float(v)))
                self._spins[key] = spin
                col = count % 6
                row = (count // 6) * 2
                self._grid.addWidget(key_label, row, col)
                self._grid.addWidget(spin, row + 1, col)
                count += 1
        except Exception:
            count = 0
        self._grid_host.setVisible(count > 0)
        self._hint.setVisible(count == 0)

    def set_params(self, params: dict[str, float]) -> None:
        for key, value in params.items():
            spin = self._spins.get(key)
            if spin is not None:
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)

    def current_params(self) -> dict[str, float]:
        return {key: float(spin.value()) for key, spin in self._spins.items()}


# ═══════════════════════════════════════════════════════════════════════
#  BACKTEST RUN PANEL — professional configuration section
# ═══════════════════════════════════════════════════════════════════════


class BacktestRunPanel(QWidget):
    """Backtest configuration — 4-column institutional control layout.

    UNIVERSE → TIMEFRAME → DATE RANGE → CAPITAL. Advanced costs behind
    one disclosure. Exactly one visually dominant action.
    """

    run_requested = Signal(object)
    timeframe_changed = Signal(str)
    busy_changed = Signal(bool)
    stop_requested = Signal()
    selection_changed = Signal(tuple)
    config_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        from app.ui.stock_ranking import StockRankingWidget

        self._ranking = StockRankingWidget(self)
        self._ranking.setVisible(False)
        self._busy = False
        self._stop_armed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(t.SP_XL, t.SP_MD, t.SP_XL, t.SP_MD)
        lay.setSpacing(t.SP_MD)

        # ── section header ──
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(t.SP_SM)
        title = QLabel("BACKTEST CONFIGURATION", self)
        title.setStyleSheet(t.label(t.TEXT2, 9, 700, 0.8))
        head.addWidget(title)
        head.addStretch(1)
        self._scope = QLabel("", self)
        self._scope.setStyleSheet(t.body(t.FS_SMALL, t.MUTED))
        head.addWidget(self._scope)
        lay.addLayout(head)

        # ── 4-column config grid ──
        grid_host = QWidget(self)
        grid_host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setVerticalSpacing(t.SP_XS)
        self._grid_captions = [
            self._lab(c) for c in ("UNIVERSE", "TIMEFRAME", "DATE RANGE", "CAPITAL")
        ]

        self._symbols = WatchlistMultiSelect(grid_host)
        self._tf_combo = QComboBox(grid_host)
        self._tf_combo.setStyleSheet(t.INPUT_QSS)
        self._tf_combo.setMinimumWidth(100)
        self._tf_combo.currentTextChanged.connect(self._on_timeframe_changed)

        range_row = QHBoxLayout()
        range_row.setSpacing(t.SP_XS)
        range_row.setContentsMargins(0, 0, 0, 0)
        self._from = self._date_edit(QDate(2023, 1, 1))
        self._to = self._date_edit(QDate.currentDate())
        arrow = QLabel("→", grid_host)
        arrow.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_BODY}px;")
        range_row.addWidget(self._from, 1)
        range_row.addWidget(arrow)
        range_row.addWidget(self._to, 1)
        range_host = QWidget(grid_host)
        range_host.setLayout(range_row)

        self._capital = QDoubleSpinBox(grid_host)
        self._capital.setRange(10000, 1e9)
        self._capital.setDecimals(0)
        self._capital.setValue(1000000)
        self._capital.setGroupSeparatorShown(True)
        self._capital.setPrefix("₹ ")
        self._capital.setLocale(QLocale(QLocale.Language.English, QLocale.Country.India))
        self._capital.setStyleSheet(t.INPUT_QSS)
        self._capital.setMinimumWidth(130)

        self._grid_fields = [self._symbols, self._tf_combo, range_host, self._capital]
        self._grid_cols = 0
        self._layout_config_grid(4)
        lay.addWidget(grid_host)
        self._grid_host = grid_host

        for widget in (self._from, self._to):
            widget.dateChanged.connect(lambda _d: self.config_changed.emit())
        self._capital.valueChanged.connect(lambda _v: self.config_changed.emit())

        # ── action row: advanced + run ──
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(t.SP_SM)
        self._advanced_toggle = QPushButton("Advanced ▾", self)
        self._advanced_toggle.setStyleSheet(t.QUIET_BUTTON_QSS)
        self._advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_toggle.setToolTip("Costs, slippage and execution assumptions")
        self._advanced_toggle.clicked.connect(self._toggle_advanced)
        action_row.addWidget(self._advanced_toggle)
        action_row.addStretch(1)

        self._run = QPushButton("▶  RUN BACKTEST", self)
        self._run.setMinimumHeight(34)
        self._run.setMinimumWidth(170)
        self._run.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run.setStyleSheet(t.PRIMARY_QSS)
        self._run.setToolTip("Execute the current experiment (Ctrl+Enter)")
        self._run.clicked.connect(self._emit)
        action_row.addWidget(self._run)
        lay.addLayout(action_row)

        self._advanced_host = QLabel(
            f"Costs — slippage {_DEFAULT_SLIPPAGE_PCT}% · "
            f"commission {_DEFAULT_COMMISSION_PCT}% (fixed) · "
            "position sizing: full capital per signal · one position at a time",
            self,
        )
        self._advanced_host.setStyleSheet(t.body(t.FS_SMALL, t.MUTED))
        self._advanced_host.setWordWrap(True)
        self._advanced_host.setVisible(False)
        lay.addWidget(self._advanced_host)

        self._symbols.selection_changed.connect(self._on_selection_for_ranking)
        self._symbols.selection_changed.connect(lambda _s: self.config_changed.emit())

        # ── inline error ──
        self._error = QLabel("", self)
        self._error.setStyleSheet(t.body(t.FS_SMALL, t.NEG, 600))
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        lay.addWidget(self._error)
        lay.addStretch(1)
        self._mode: StrategyViewMode = StrategyViewMode.BUY
        self._refresh_scope()

    def _refresh_scope(self) -> None:
        count = self._symbols.selected_count()
        noun = "stock" if count == 1 else "stocks"
        self._scope.setText(f"{count} {noun} selected" if count else "No universe selected")

    def _natural_width(self, cols: int) -> int:
        """Width the grid genuinely needs for *cols* columns, current font."""
        widest = [0] * cols
        for index, widget in enumerate(self._grid_fields):
            slot = index % cols
            need = max(
                widget.minimumSizeHint().width(),
                widget.minimumWidth(),
                self._grid_captions[index].sizeHint().width(),
            )
            widest[slot] = max(widest[slot], need)
        spacing = self._grid.horizontalSpacing()
        if spacing < 0:
            spacing = self._grid.spacing()
        return sum(widest) + spacing * (cols - 1)

    def _layout_config_grid(self, cols: int) -> None:
        if cols == self._grid_cols:
            return
        self._grid_cols = cols
        for widget in (*self._grid_captions, *self._grid_fields):
            self._grid.removeWidget(widget)
        for col in range(4):
            self._grid.setColumnStretch(col, 0)
            self._grid.setColumnMinimumWidth(col, 0)
        for index, widget in enumerate(self._grid_fields):
            row = (index // cols) * 2
            col = index % cols
            self._grid.addWidget(self._grid_captions[index], row, col)
            self._grid.addWidget(widget, row + 1, col)
        for col in range(cols):
            self._grid.setColumnStretch(col, 3 if col % 2 == 0 else 0)
        self._grid.setHorizontalSpacing(t.SP_XXL if cols == 4 else t.SP_LG)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_config_grid(4 if self.width() >= self._natural_width(4) else 2)

    def _on_timeframe_changed(self, timeframe: str) -> None:
        if timeframe:
            self.timeframe_changed.emit(timeframe)
        self.config_changed.emit()

    def _toggle_advanced(self) -> None:
        show = not self._advanced_host.isVisible()
        self._advanced_host.setVisible(show)
        self._advanced_toggle.setText("Advanced ▴" if show else "Advanced ▾")

    @staticmethod
    def _date_edit(date: QDate) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd MMM yyyy")
        edit.setDate(date)
        edit.setStyleSheet(t.INPUT_QSS)
        return edit

    def _lab(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet(t.label(t.MUTED, 9, 700, 0.8))
        return label

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        """Replace the available universe (Market Watchlist feed)."""
        self._symbols.set_symbols(symbols)

    def selected_symbols(self) -> tuple[str, ...]:
        """The selected symbols in selection order."""
        return self._symbols.selected_symbols()

    def set_selected_symbols(self, symbols: tuple[str, ...] | list[str]) -> None:
        """Replace the selection (used by tests and bulk actions)."""
        self._symbols.set_selected_symbols(symbols)

    @property
    def ranking(self):  # type: ignore[no-untyped-def]
        """The embedded stock-ranking table (selection-only universe)."""
        return self._ranking

    def _on_selection_for_ranking(self, selected: object) -> None:
        try:
            symbols = tuple(selected) if selected is not None else ()  # type: ignore[arg-type]
        except Exception:
            symbols = self._symbols.selected_symbols()
        try:
            self._ranking.set_universe(symbols)
        except Exception:
            pass
        self._refresh_scope()
        try:
            self.selection_changed.emit(tuple(symbols))
        except Exception:
            pass

    def set_ranking_results(
        self,
        base: object | None,
        errors: dict[str, str] | None = None,
        last_run: tuple[str, ...] | list[str] | None = None,
        mode_label: str = "",
    ) -> None:
        """Push a mode-consistent result into the ranking table."""
        try:
            self._ranking.set_results(base, errors, last_run, mode_label)
        except Exception:
            pass

    def set_timeframes(self, timeframes: tuple[str, ...]) -> None:
        current = self._tf_combo.currentText()
        self._tf_combo.blockSignals(True)
        self._tf_combo.clear()
        for timeframe in timeframes:
            self._tf_combo.addItem(timeframe)
        if current:
            idx = self._tf_combo.findText(current)
            if idx >= 0:
                self._tf_combo.setCurrentIndex(idx)
        self._tf_combo.blockSignals(False)

    def select_timeframe(self, timeframe: str) -> None:
        idx = self._tf_combo.findText(timeframe)
        if idx >= 0:
            self._tf_combo.setCurrentIndex(idx)

    def current_config(self) -> dict[str, object]:
        selected = self._symbols.selected_symbols()
        return {
            "symbol": selected[0] if selected else "",
            "symbols": selected,
            "timeframe": self._tf_combo.currentText().strip(),
            "start_date": self._from.date().toString("yyyy-MM-dd"),
            "end_date": self._to.date().toString("yyyy-MM-dd"),
            "initial_capital": float(self._capital.value()),
            "slippage_pct": _DEFAULT_SLIPPAGE_PCT,
            "commission_pct": _DEFAULT_COMMISSION_PCT,
        }

    def set_view_mode(self, mode: StrategyViewMode) -> None:
        self._mode = mode
        if self._busy and self._stop_armed:
            return
        if mode == StrategyViewMode.BUY:
            self._run.setText("▶  RUN BUY")
        elif mode == StrategyViewMode.SELL:
            self._run.setText("▶  RUN SELL")
        else:
            self._run.setText("▶  RUN ALL")

    def _emit(self) -> None:
        if self._busy and self._stop_armed:
            self.stop_requested.emit()
            return
        if self._busy:
            return
        if not self._symbols.has_selection():
            self._show_error("Select at least one symbol before running the backtest.")
            return
        if self._from.date() >= self._to.date():
            self._show_error("End date must be after start date.")
            return
        self._hide_error()
        self.run_requested.emit(self.current_config())

    def _show_error(self, text: str) -> None:
        self._error.setText(text)
        self._error.setVisible(True)

    def _hide_error(self) -> None:
        self._error.setVisible(False)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._run.setEnabled(True)
            self._run.setStyleSheet(t.PRIMARY_QSS)
            self._run.setText("⟳  RUNNING…")
            self._hide_error()
        else:
            self._stop_armed = False
            self._run.setEnabled(True)
            self._run.setStyleSheet(t.PRIMARY_QSS)
            self.set_view_mode(self._mode)
        self.busy_changed.emit(busy)

    def arm_stop(self) -> None:
        """Morph the run button into ■ STOP (batch confirmed in flight)."""
        if not self._busy:
            return
        self._stop_armed = True
        self._run.setStyleSheet(t.STOP_QSS)
        self._run.setText("■ STOP")

    def disarm_stop(self) -> None:
        self._stop_armed = False
        if not self._busy:
            self._run.setStyleSheet(t.PRIMARY_QSS)
            self.set_view_mode(self._mode)


# ═══════════════════════════════════════════════════════════════════════
#  EDITOR PANE — strategy header + CODE | PARAMETERS | BACKTEST tabs
# ═══════════════════════════════════════════════════════════════════════


class EditorPane(QWidget):
    """Center column — strategy header + CODE | PARAMETERS | BACKTEST tabs."""

    save_requested = Signal(str)
    compile_requested = Signal(str)
    tab_changed = Signal(str)
    dirty_changed = Signal(bool)
    param_changed = Signal(str, float)
    rename_affordance = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── header: strategy name + status ──
        header = QWidget(self)
        header.setObjectName("EditorHeader")
        header.setStyleSheet(
            f"QWidget#EditorHeader {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        header.setFixedHeight(28)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(12, 2, 12, 2)
        h_lay.setSpacing(8)

        self._name = QLabel("", header)
        self._name.setStyleSheet(f"color: {t.TEXT}; font-size: {t.FS_SMALL}px; font-weight: 600;")
        h_lay.addWidget(self._name)
        h_lay.addStretch(1)

        self._status = _StatusChip(header)
        h_lay.addWidget(self._status)

        edit_btn = QToolButton(header)
        edit_btn.setText("✎")
        edit_btn.setToolTip("Rename strategy")
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setStyleSheet(t.TOOL_QSS)
        edit_btn.clicked.connect(self.rename_affordance.emit)
        h_lay.addWidget(edit_btn)
        lay.addWidget(header)

        # ── tab bar: CODE | PARAMETERS | BACKTEST ──
        tab_bar = QWidget(self)
        tab_bar.setObjectName("CenterTabs")
        tab_bar.setStyleSheet(
            f"QWidget#CenterTabs {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        tab_bar.setFixedHeight(26)
        tb_lay = QHBoxLayout(tab_bar)
        tb_lay.setContentsMargins(12, 0, 12, 0)
        tb_lay.setSpacing(2)
        group = QButtonGroup(tab_bar)
        group.setExclusive(True)

        self._code_tab = QPushButton("CODE", tab_bar)
        self._params_tab = QPushButton("PARAMETERS", tab_bar)
        self._backtest_tab = QPushButton("BACKTEST", tab_bar)
        _tab_qss = (
            f"QPushButton {{ background: transparent; border: none;"
            f" border-bottom: 2px solid transparent; border-radius: 0;"
            f" padding: 5px 8px 3px; color: {t.TEXT2}; font-size: {t.FS_LABEL}px;"
            f" font-weight: 600; letter-spacing: 0.5px; }}"
            f"QPushButton:hover {{ color: {t.TEXT}; }}"
            f"QPushButton:checked {{ color: {t.TEXT};"
            f" border-bottom: 2px solid {t.ACCENT}; }}"
        )
        for index, button in enumerate((self._code_tab, self._params_tab, self._backtest_tab)):
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_tab_qss)
            group.addButton(button)
        self._code_tab.clicked.connect(lambda: self._switch(0))
        self._params_tab.clicked.connect(lambda: self._switch(1))
        self._backtest_tab.clicked.connect(lambda: self._switch(2))
        tb_lay.addWidget(self._code_tab)
        tb_lay.addWidget(self._params_tab)
        tb_lay.addWidget(self._backtest_tab)
        tb_lay.addStretch(1)
        lay.addWidget(tab_bar)

        # ── stacked content: editor / params / backtest ──
        self.editor = CodeEditor(self)
        self.params = ParamsPane(self)
        self.params.param_changed.connect(self.param_changed.emit)
        self.backtest_panel = BacktestRunPanel(self)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self.editor)
        self._stack.addWidget(self.params)
        self._stack.addWidget(self.backtest_panel)
        lay.addWidget(self._stack, 1)

        # ── status message bar ──
        self._status_msg = QLabel("", self)
        self._status_msg.setStyleSheet(
            f"color: {t.MUTED}; font-size: {t.FS_LABEL}px; padding: 2px 12px;"
            f" background: {t.BG0}; border-top: 1px solid {t.BORDER};"
        )
        self._status_msg.setWordWrap(True)
        self._status_msg.setVisible(False)
        lay.addWidget(self._status_msg)
        self._name_text = ""
        self._dirty = False
        self.editor.textChanged.connect(self._on_text_changed)

    def _switch(self, index: int) -> None:
        self._stack.setCurrentIndex(index)

    def show_parameters(self) -> None:
        self._switch(1)

    def show_backtest(self) -> None:
        self._switch(2)

    def _on_text_changed(self) -> None:
        if not self._dirty:
            self._dirty = True
            self._status.set_modified()
            self.dirty_changed.emit(True)

    def mark_saved(self) -> None:
        self._dirty = False
        self._status.set_ready()

    def rename_buffer(self, old: str, new: str) -> None:
        if self._name_text == old:
            self.set_strategy_name(new)

    def open_buffer(self, name: str, code: str) -> None:
        self.editor.blockSignals(True)
        self.editor.setPlainText(code)
        self.editor.blockSignals(False)
        self.editor.clear_error()
        self._status_msg.setVisible(False)
        self._dirty = False
        self._status.set_ready()
        self.params.refresh(code)
        self.set_strategy_name(name)
        self._switch(0)

    def get_code(self) -> str:
        return self.editor.toPlainText()

    def set_code(self, code: str) -> None:
        self.editor.setPlainText(code)
        self.params.refresh(code)

    def set_params(self, params: dict[str, float]) -> None:
        self.params.set_params(params)

    def current_params(self) -> dict[str, float]:
        return self.params.current_params()

    def _refresh_params_from_code(self, code: str) -> None:
        self.params.refresh(code)

    def set_strategy_name(self, name: str) -> None:
        self._name_text = name
        self._name.setText(name)
        self.tab_changed.emit(name)

    def current_tab_name(self) -> str:
        return self._name_text

    def show_compile_result(
        self, ok: bool, msg: str, line: int | None = None, col: int | None = None
    ) -> None:
        color = t.POS if ok else t.NEG
        prefix = "✓" if ok else "✕"
        clean = msg.lstrip()
        if clean[:1] in ("✓", "✕"):
            clean = clean[1:].lstrip()
        self._status_msg.setStyleSheet(
            f"color: {color}; font-size: {t.FS_LABEL}px; padding: 2px 12px;"
            f" background: {t.BG0}; border-top: 1px solid {t.BORDER};"
        )
        self._status_msg.setText(f"{prefix} {clean}")
        self._status_msg.setVisible(True)
        if ok:
            self.editor.clear_error()
            if not self._dirty:
                self._status.set_ready()
        else:
            self.editor.set_error(line, col, msg)
            self._status.set_error()


# ═══════════════════════════════════════════════════════════════════════
#  VIEW MODE SELECTOR — segmented BUY / SELL / COMPARE
# ═══════════════════════════════════════════════════════════════════════


class _ViewModeSelector(QWidget):
    """Compact segmented control: [ BUY ] [ SELL ] [ COMPARE ]."""

    mode_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewModeSelector")
        self.setFixedHeight(30)
        self.setStyleSheet(f"background: {t.BG1}; border-bottom: 1px solid {t.BORDER};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 2, 12, 2)
        lay.setSpacing(0)

        hint = QLabel("DIRECTION", self)
        hint.setStyleSheet(t.label(t.MUTED, 9, 600, 0.5))
        lay.addWidget(hint)
        lay.addSpacing(8)

        seg = QWidget(self)
        seg.setObjectName("Seg")
        seg.setStyleSheet(
            f"QWidget#Seg {{ background: {t.BG0}; border: 1px solid {t.BORDER}; border-radius: 4px; }}"
        )
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(2, 2, 2, 2)
        seg_lay.setSpacing(2)
        group = QButtonGroup(seg)
        group.setExclusive(True)
        self._group = group

        self._buy_btn = self._make_btn("BUY", seg)
        self._sell_btn = self._make_btn("SELL", seg)
        self._cmp_btn = self._make_btn("COMPARE", seg)
        for btn in (self._buy_btn, self._sell_btn, self._cmp_btn):
            group.addButton(btn)
            seg_lay.addWidget(btn)
        self._buy_btn.setChecked(True)
        self._buy_btn.clicked.connect(lambda: self._emit(StrategyViewMode.BUY))
        self._sell_btn.clicked.connect(lambda: self._emit(StrategyViewMode.SELL))
        self._cmp_btn.clicked.connect(lambda: self._emit(StrategyViewMode.COMPARE))
        lay.addWidget(seg)
        lay.addStretch(1)

        self._indicator = QLabel("● BUY — LONG", self)
        self._indicator.setStyleSheet(
            f"color: {_BUY_ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700;"
        )
        lay.addWidget(self._indicator)
        self._current: StrategyViewMode = StrategyViewMode.BUY
        self._apply_indicator()

    def _make_btn(self, text: str, parent: QWidget) -> QPushButton:
        btn = QPushButton(text, parent)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(22)
        _base = (
            f"QPushButton {{ background: transparent; border: none; border-radius: 3px;"
            f" padding: 4px 10px; color: {t.TEXT2}; font-size: {t.FS_LABEL}px; font-weight: 700;"
            f" letter-spacing: 0.3px; }}"
            f"QPushButton:hover {{ color: {t.TEXT}; background: {t.PANEL2}; }}"
            f"QPushButton:checked {{ background: {t.PANEL2}; color: {t.TEXT}; }}"
        )
        btn.setStyleSheet(_base)
        return btn

    def _emit(self, mode: StrategyViewMode) -> None:
        self._current = mode
        self._apply_indicator()
        self.mode_changed.emit(mode)

    def _apply_indicator(self) -> None:
        _base = (
            f"QPushButton {{ background: transparent; border: none; border-radius: 3px;"
            f" padding: 4px 10px; color: {t.TEXT2}; font-size: {t.FS_LABEL}px; font-weight: 700; }}"
            f"QPushButton:hover {{ color: {t.TEXT}; background: {t.PANEL2}; }}"
            f"QPushButton:checked {{ background: {t.PANEL2}; color: {t.TEXT}; }}"
        )
        if self._current == StrategyViewMode.BUY:
            self._indicator.setText("● BUY — LONG")
            self._indicator.setStyleSheet(
                f"color: {_BUY_ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700;"
            )
            self._buy_btn.setStyleSheet(
                f"QPushButton {{ background: {_BUY_ACCENT}; border: none; border-radius: 3px;"
                f" padding: 4px 10px; color: {t.ACCENT_DEEP}; font-size: {t.FS_LABEL}px; font-weight: 800; }}"
                f"QPushButton:checked {{ background: {_BUY_ACCENT}; color: {t.ACCENT_DEEP}; }}"
            )
            self._sell_btn.setStyleSheet(_base)
            self._cmp_btn.setStyleSheet(_base)
        elif self._current == StrategyViewMode.SELL:
            self._indicator.setText("● SELL — SHORT")
            self._indicator.setStyleSheet(
                f"color: {_SELL_ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700;"
            )
            self._sell_btn.setStyleSheet(
                f"QPushButton {{ background: {_SELL_ACCENT}; border: none; border-radius: 3px;"
                f" padding: 4px 10px; color: {t.TEXT}; font-size: {t.FS_LABEL}px; font-weight: 800; }}"
                f"QPushButton:checked {{ background: {_SELL_ACCENT}; color: {t.TEXT}; }}"
            )
            self._buy_btn.setStyleSheet(_base)
            self._cmp_btn.setStyleSheet(_base)
        else:
            self._indicator.setText("◐ COMPARE")
            self._indicator.setStyleSheet(
                f"color: {t.ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700;"
            )
            self._cmp_btn.setStyleSheet(
                f"QPushButton {{ background: {t.ACCENT}; border: none; border-radius: 3px;"
                f" padding: 4px 10px; color: {t.ACCENT_DEEP}; font-size: {t.FS_LABEL}px; font-weight: 800; }}"
                f"QPushButton:checked {{ background: {t.ACCENT}; color: {t.ACCENT_DEEP}; }}"
            )
            self._buy_btn.setStyleSheet(_base)
            self._sell_btn.setStyleSheet(_base)
        self._indicator.setVisible(True)

    @property
    def current_mode(self) -> StrategyViewMode:
        return self._current

    def set_mode(self, mode: StrategyViewMode) -> None:
        if mode == self._current:
            return
        self._current = mode
        self._buy_btn.blockSignals(True)
        self._sell_btn.blockSignals(True)
        self._cmp_btn.blockSignals(True)
        self._buy_btn.setChecked(mode == StrategyViewMode.BUY)
        self._sell_btn.setChecked(mode == StrategyViewMode.SELL)
        self._cmp_btn.setChecked(mode == StrategyViewMode.COMPARE)
        self._buy_btn.blockSignals(False)
        self._sell_btn.blockSignals(False)
        self._cmp_btn.blockSignals(False)
        self._apply_indicator()


# ═══════════════════════════════════════════════════════════════════════
#  VERDICT + METRICS TILES — decision hierarchy
# ═══════════════════════════════════════════════════════════════════════


def interpret_result(result: StrategyResult | None) -> tuple[str, str, str]:
    """Return ``(verdict, note, colour)`` for the result interpretation layer."""
    if result is None:
        return (
            "INSUFFICIENT DATA",
            "Run the backtest to analyze the selected universe.",
            t.MUTED,
        )
    metrics = result.metrics
    if not metrics.total_trades:
        return (
            "INSUFFICIENT DATA",
            "The strategy produced no closed trades in this range — nothing to evaluate.",
            t.MUTED,
        )
    net = float(metrics.net_profit or 0.0)
    pf = metrics.profit_factor
    sharpe = metrics.sharpe_ratio
    if net < 0:
        return (
            "UNPROFITABLE",
            f"Net loss of {_signed_inr(net)} across {metrics.total_trades} trades.",
            t.NEG,
        )
    if net == 0:
        return ("MIXED", "Flat result — the strategy neither gained nor lost.", t.WARN)
    strong = (pf is not None and pf >= 1.2) and (sharpe is None or sharpe >= 0.5)
    if strong:
        return (
            "PROFITABLE",
            f"Net gain of {_signed_inr(net)} with supportive quality metrics.",
            t.POS,
        )
    quality = f"profit factor {pf:.2f}" if pf is not None else "no profit factor"
    return (
        "MIXED",
        f"Positive net P&L ({_signed_inr(net)}) but weak quality — {quality}.",
        t.WARN,
    )


class MetricsTiles(QWidget):
    """3-tier performance hierarchy: verdict → hero P&L → quality → risk."""

    _TIER2 = ("WIN RATE", "PROFIT FACTOR", "EXPECTANCY", "SHARPE")
    _TIER3 = ("MAX DRAWDOWN", "TOTAL TRADES", "AVG TRADE")
    _KEYS = (
        "NET PROFIT",
        "TOTAL TRADES",
        "WIN RATE",
        "PROFIT FACTOR",
        "EXPECTANCY",
        "MAX DRAWDOWN",
        "SHARPE",
        "AVG TRADE",
    )
    _CAPTIONS = {
        "NET PROFIT": "NET P&L",
        "TOTAL TRADES": "TOTAL TRADES",
        "WIN RATE": "WIN RATE",
        "PROFIT FACTOR": "PROFIT FACTOR",
        "EXPECTANCY": "EXPECTANCY",
        "MAX DRAWDOWN": "MAX DRAWDOWN",
        "SHARPE": "SHARPE",
        "AVG TRADE": "AVG TRADE",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1}; border-bottom: 1px solid {t.BORDER};")
        self._grid_hosts: list[tuple[QWidget, list[tuple[QLabel, QLabel]], int]] = []
        self._reflow_cols: int | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(t.SP_XL, t.SP_MD, t.SP_XL, t.SP_MD)
        lay.setSpacing(t.SP_SM)

        # ── title row ──
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(t.SP_SM)
        title = QLabel("BACKTEST RESULT", self)
        title.setStyleSheet(t.label(t.TEXT2, 9, 700, 0.8))
        title_row.addWidget(title)
        self._dir_label = QLabel("BUY PERFORMANCE", self)
        self._dir_label.setStyleSheet(t.label(t.MUTED, 9, 700, 0.6))
        title_row.addWidget(self._dir_label)
        title_row.addStretch(1)
        self._context = QLabel("", self)
        self._context.setStyleSheet(t.body(t.FS_SMALL, t.TEXT2, 500))
        title_row.addWidget(self._context)
        self._status = QLabel("● READY", self)
        self._status.setStyleSheet(t.body(t.FS_SMALL, t.MUTED, 700))
        title_row.addWidget(self._status)
        lay.addLayout(title_row)

        # ── verdict row ──
        verdict_row = QHBoxLayout()
        verdict_row.setContentsMargins(0, 0, 0, 0)
        verdict_row.setSpacing(t.SP_SM)
        self._verdict = QLabel("INSUFFICIENT DATA", self)
        self._verdict.setStyleSheet(
            f"color: {t.MUTED}; font-size: {t.FS_TABLE}px; font-weight: 800;"
            f" letter-spacing: 0.8px; border: 1px solid {t.MUTED};"
            f" border-radius: 2px; padding: 2px 8px;"
        )
        verdict_row.addWidget(self._verdict)
        self._verdict_note = QLabel("Run the backtest to analyze the selected universe.", self)
        self._verdict_note.setStyleSheet(t.body(t.FS_SMALL, t.TEXT2))
        self._verdict_note.setWordWrap(True)
        verdict_row.addWidget(self._verdict_note, 1)
        lay.addLayout(verdict_row)

        # ── level 1: hero Net P&L ──
        self._vals: dict[str, QLabel] = {}
        hero = QHBoxLayout()
        hero.setContentsMargins(0, 0, 0, 0)
        hero.setSpacing(t.SP_SM)
        hero_cap = QLabel(self._CAPTIONS["NET PROFIT"], self)
        hero_cap.setStyleSheet(t.label(t.MUTED, 9, 700, 0.8))
        hero_value = QLabel("--", self)
        hero_value.setStyleSheet(t.metric(t.FS_HERO, t.TEXT, 800))
        self._vals["NET PROFIT"] = hero_value
        hero_block = QVBoxLayout()
        hero_block.setContentsMargins(0, 0, 0, 0)
        hero_block.setSpacing(0)
        hero_block.addWidget(hero_cap)
        hero_block.addWidget(hero_value)
        hero.addLayout(hero_block)
        hero.addStretch(1)
        lay.addLayout(hero)
        lay.addWidget(_hline())

        # ── level 2: quality metrics ──
        self._tier2_host = self._grid(self._TIER2, t.FS_METRIC)
        lay.addWidget(self._tier2_host)

        # ── level 3: risk / execution ──
        self._tier3_host = self._grid(self._TIER3, t.FS_METRIC_SM)
        lay.addWidget(self._tier3_host)

        self._extra = QLabel("", self)
        self._extra.setStyleSheet(t.body(t.FS_SMALL, t.MUTED))
        self._extra.setWordWrap(True)
        lay.addWidget(self._extra)
        self._mode: StrategyViewMode = StrategyViewMode.BUY

    def _grid(self, keys: tuple[str, ...], size: int) -> QWidget:
        host = QWidget(self)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(t.SP_XXL)
        grid.setVerticalSpacing(t.SP_XS)
        pairs: list[tuple[QLabel, QLabel]] = []
        for key in keys:
            caption = QLabel(self._CAPTIONS[key], host)
            caption.setStyleSheet(t.label(t.MUTED, 9, 700, 0.6))
            value = QLabel("--", host)
            value.setStyleSheet(t.metric(size, t.TEXT, 700))
            self._vals[key] = value
            pairs.append((caption, value))
        self._lay_grid_pairs(grid, pairs, len(keys))
        self._grid_hosts.append((host, pairs, len(keys)))
        return host

    @staticmethod
    def _lay_grid_pairs(grid: QGridLayout, pairs: list[tuple[QLabel, QLabel]], cols: int) -> None:
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                grid.removeWidget(widget)
        for index, (caption, value) in enumerate(pairs):
            grid.addWidget(caption, (index // cols) * 2, index % cols)
            grid.addWidget(value, (index // cols) * 2 + 1, index % cols)
        grid.setColumnStretch(cols, 1)

    def reflow(self, cols: int) -> None:
        """Re-lay metric tiers for narrow widths (4→2)."""
        if cols == getattr(self, "_reflow_cols", None):
            return
        self._reflow_cols = cols
        for host, pairs, full in self._grid_hosts:
            layout = host.layout()
            if layout is None or not isinstance(layout, QGridLayout):
                continue
            self._lay_grid_pairs(layout, pairs, min(cols, full))

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().resizeEvent(event)
        self.reflow(2 if 0 < self.width() < 620 else 4)

    @staticmethod
    def _value_style(color: str) -> str:
        return t.metric(t.FS_METRIC_SM, color, 700)

    def set_status(self, state: str) -> None:
        """Update the run-state pill."""
        if state == getattr(self, "_state", None):
            return
        self._state = state
        mapping = {
            "ready": ("● READY", t.MUTED),
            "running": ("● RUNNING…", t.ACCENT),
            "aggregating": ("◌ AGGREGATING…", t.ACCENT),
            "finalizing": ("◌ FINALIZING…", t.ACCENT),
            "complete": ("✓ COMPLETE", t.POS),
            "failed": ("✕ FAILED", t.NEG),
        }
        text, color = mapping.get(state, ("● READY", t.MUTED))
        self._status.setText(text)
        self._status.setStyleSheet(t.body(t.FS_SMALL, color, 700))

    def set_context(self, context: str) -> None:
        """Run-scope caption."""
        if context == getattr(self, "_context_text", None):
            return
        self._context_text = context
        self._context.setText(context)

    def set_view_mode(self, mode: StrategyViewMode) -> None:
        self._mode = mode
        if mode == StrategyViewMode.BUY:
            self._dir_label.setText("BUY — LONG")
            self._dir_label.setStyleSheet(
                f"color: {_BUY_ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700; letter-spacing: 0.5px;"
            )
        elif mode == StrategyViewMode.SELL:
            self._dir_label.setText("SELL — SHORT")
            self._dir_label.setStyleSheet(
                f"color: {_SELL_ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700; letter-spacing: 0.5px;"
            )
        else:
            self._dir_label.setText("PERFORMANCE")
            self._dir_label.setStyleSheet(t.label(t.MUTED, 9, 700, 0.6))

    def set_compact(self, compact: bool) -> None:
        if compact == getattr(self, "_compact", None):
            return
        self._compact = compact
        self._extra.setVisible(not compact)

    def _render_verdict(self, result: StrategyResult | None) -> None:
        verdict, note, color = interpret_result(result)
        self._verdict.setText(verdict)
        self._verdict.setStyleSheet(
            f"color: {color}; font-size: {t.FS_TABLE}px; font-weight: 800;"
            f" letter-spacing: 0.8px; border: 1px solid {color};"
            f" border-radius: 2px; padding: 2px 8px;"
        )
        self._verdict_note.setText(note)

    def set_result(self, result: StrategyResult | None) -> None:
        self._render_verdict(result)
        if result is None:
            for key, value in self._vals.items():
                value.setText("--")
                value.setStyleSheet(
                    t.metric(t.FS_HERO if key == "NET PROFIT" else t.FS_METRIC, t.TEXT, 700)
                )
            self._extra.setText("")
            return
        metrics = result.metrics
        net_color = t.POS if (metrics.net_profit or 0) >= 0 else t.NEG
        net_text = _signed_inr(metrics.net_profit) if metrics.total_trades else "--"
        avg_text = _signed_inr(metrics.avg_trade) if metrics.avg_trade is not None else "--"
        exp_text = _signed_inr(metrics.expectancy) if metrics.expectancy is not None else "--"
        mapping: dict[str, tuple[str, str]] = {
            "NET PROFIT": (net_text, net_color),
            "TOTAL TRADES": (str(metrics.total_trades), t.TEXT),
            "WIN RATE": (
                f"{metrics.win_rate * 100:.1f}%" if metrics.win_rate is not None else "--",
                t.TEXT,
            ),
            "PROFIT FACTOR": (
                f"{metrics.profit_factor:.2f}" if metrics.profit_factor is not None else "--",
                t.TEXT,
            ),
            "EXPECTANCY": (exp_text, t.TEXT),
            "MAX DRAWDOWN": (f"-{metrics.max_drawdown_pct:.2f}%", t.NEG),
            "SHARPE": (
                f"{metrics.sharpe_ratio:.2f}" if metrics.sharpe_ratio is not None else "--",
                t.TEXT,
            ),
            "AVG TRADE": (avg_text, t.TEXT),
        }
        for key, (text, color) in mapping.items():
            label = self._vals[key]
            label.setText(text)
            size = (
                t.FS_HERO
                if key == "NET PROFIT"
                else (t.FS_METRIC if key in self._TIER2 else t.FS_METRIC_SM)
            )
            label.setStyleSheet(t.metric(size, color, 700 if key != "NET PROFIT" else 800))
        trades = result.trades
        if not trades:
            self._extra.setText("")
            return
        wins = [trade for trade in trades if trade.winning]
        losses = [trade for trade in trades if not trade.winning]
        avg_win = sum(trade.pnl for trade in wins) / len(wins) if wins else 0
        avg_loss = sum(trade.pnl for trade in losses) / len(losses) if losses else 0
        self._extra.setText(
            f"Wins {len(wins)} · Losses {len(losses)}"
            f" · Avg win {_signed_inr(avg_win)} · Avg loss {_signed_inr(avg_loss)}"
        )


# ═══════════════════════════════════════════════════════════════════════
#  DUAL EQUITY VIEW — COMPARE overlay chart
# ═══════════════════════════════════════════════════════════════════════


class _DualEquityView(QWidget):
    """Dual equity chart for COMPARE — BUY (teal) + SELL (red) with labels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buy: StrategyResult | None = None
        self._sell: StrategyResult | None = None
        self.setMinimumHeight(140)

    def set_results(self, buy: StrategyResult | None, sell: StrategyResult | None) -> None:
        self._buy = buy
        self._sell = sell
        self.update()

    def set_result(self, result: StrategyResult | None) -> None:
        self._buy = result
        self._sell = None
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(t.BG1))
        from PySide6.QtCore import Qt as _Qt

        has_buy = self._buy is not None and bool(self._buy.equity_curve)
        has_sell = self._sell is not None and bool(self._sell.equity_curve)
        if not has_buy and not has_sell:
            painter.setPen(QColor(t.TEXT2))
            painter.drawText(
                self.rect(), _Qt.AlignmentFlag.AlignCenter, "No equity data — run a backtest"
            )
            return
        from backtest.ui.analytics_views import decimate_envelope

        curves = []  # type: ignore[var-annotated]
        if has_buy:
            curves.append((self._buy.equity_curve, QColor(_BUY_ACCENT), "BUY / LONG"))  # type: ignore[reportOptionalMemberAccess]
        if has_sell:
            curves.append((self._sell.equity_curve, QColor(_SELL_ACCENT), "SELL / SHORT"))  # type: ignore[reportOptionalMemberAccess]
        all_eq = []
        for curve, _, _ in curves:
            all_eq.extend(p.equity for p in curve)  # type: ignore[attr-defined]
        lo, hi = min(all_eq), max(all_eq)
        span = hi - lo or 1.0
        pad_l, pad_r, pad_t, pad_b = 48, 12, 16, 14
        plot = self.rect().adjusted(pad_l, pad_t, -pad_r, -pad_b)
        if plot.width() <= 0 or plot.height() <= 0:
            return
        budget = max(64, plot.width() * 2)
        painter.setPen(QPen(QColor(t.BORDER), 1))
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
        y_leg = 4
        x_leg = plot.left()
        for _, color, label in curves:
            painter.fillRect(int(x_leg), int(y_leg), 10, 3, color)
            painter.setPen(QColor(t.TEXT))
            painter.drawText(int(x_leg + 14), int(y_leg + 8), label)
            x_leg += 100
        for curve, color, _ in curves:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(color, 1.6))
            equities = decimate_envelope([p.equity for p in curve], budget)  # type: ignore[attr-defined]
            pts = []
            for i, equity in enumerate(equities):
                x = plot.left() + i / max(1, len(equities) - 1) * plot.width()
                y = plot.bottom() - (equity - lo) / span * plot.height()
                from PySide6.QtCore import QPointF as _QPointF

                pts.append(_QPointF(x, y))
            for i in range(len(pts) - 1):
                painter.drawLine(pts[i], pts[i + 1])
        if has_buy and self._buy is not None:
            y0 = plot.bottom() - (self._buy.metrics.starting_capital - lo) / span * plot.height()
            painter.setPen(QPen(QColor(t.BORDER), 1, _Qt.PenStyle.DashLine))
            painter.drawLine(plot.left(), int(y0), plot.right(), int(y0))


# ═══════════════════════════════════════════════════════════════════════
#  TRADE BLOTTER — dense trade table
# ═══════════════════════════════════════════════════════════════════════


class TradeBlotter(QWidget):
    """Dense trade table with filter and CSV export."""

    trade_clicked = Signal(int)
    trade_hovered = Signal(int)
    symbol_filter_changed = Signal(str)

    _ROW_CAP = 2000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 4, 12, 4)
        bar.setSpacing(4)
        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter trades")
        self._filter.setStyleSheet(t.INPUT_QSS)
        self._filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self._filter, 1)

        self._side_filter = QComboBox(self)
        self._side_filter.addItems(["ALL", "BUY", "SELL"])
        self._side_filter.setStyleSheet(t.INPUT_QSS)
        self._side_filter.setFixedWidth(80)
        self._side_filter.setVisible(False)
        self._side_filter.currentTextChanged.connect(lambda _: self._apply_filter(""))
        bar.addWidget(self._side_filter)

        self._symbol_filter = QComboBox(self)
        self._symbol_filter.addItem("ALL")
        self._symbol_filter.setStyleSheet(t.INPUT_QSS)
        self._symbol_filter.setFixedWidth(100)
        self._symbol_filter.setVisible(False)
        self._symbol_filter.setToolTip("Filter trades by symbol")
        self._symbol_filter.currentTextChanged.connect(self._on_symbol_filter_changed)
        bar.addWidget(self._symbol_filter)

        export_btn = QPushButton("CSV", self)
        export_btn.setStyleSheet(t.BUTTON_QSS)
        export_btn.setFixedWidth(50)
        export_btn.clicked.connect(self._export_csv)
        bar.addWidget(export_btn)
        lay.addLayout(bar)

        self._placeholder = QLabel("Run a backtest to see trades.", self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
        lay.addWidget(self._placeholder, 1)

        self._count_label = QLabel("", self)
        self._count_label.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
        self._count_label.setVisible(False)
        lay.addWidget(self._count_label)

        self._table = QTableWidget(self)
        self._table.setColumnCount(11)
        self._table.setHorizontalHeaderLabels(
            [
                "#",
                "SYMBOL",
                "SIDE",
                "ENTRY",
                "ENTRY PX",
                "EXIT",
                "EXIT PX",
                "P&L",
                "R",
                "BARS",
                "REASON",
            ]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        self._table.setStyleSheet(
            t.TABLE_QSS
            + f" QTableWidget::item:selected {{ background: {t.PANEL2}; border-left: 2px solid {t.ACCENT}; }}"
        )
        self._table.cellClicked.connect(self._on_cell)
        self._table.cellEntered.connect(self._on_hover)
        self._table.setMouseTracking(True)
        self._table.setVisible(False)
        self._table.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        lay.addWidget(self._table, 1)

        self._trades: list = []
        self._side_mode: str = "ALL"
        self._needle: str = ""
        self._selected_index: int | None = None
        self._table.installEventFilter(self)
        try:
            self._table.viewport().installEventFilter(self)
        except Exception:
            pass

    def set_side_filter_visible(self, visible: bool) -> None:
        self._side_filter.setVisible(visible)

    def set_side_mode(self, mode: str) -> None:
        """Set directional filter: ALL, BUY (LONG), SELL (SHORT)."""
        m = mode.upper()
        if m in ("BUY", "LONG"):
            self._side_mode = "LONG"
            self._side_filter.blockSignals(True)
            self._side_filter.setCurrentText("BUY")
            self._side_filter.blockSignals(False)
        elif m in ("SELL", "SHORT"):
            self._side_mode = "SHORT"
            self._side_filter.blockSignals(True)
            self._side_filter.setCurrentText("SELL")
            self._side_filter.blockSignals(False)
        else:
            self._side_mode = "ALL"
            self._side_filter.blockSignals(True)
            self._side_filter.setCurrentText("ALL")
            self._side_filter.blockSignals(False)
        self._apply_filter(self._needle)
        cur = self._side_filter.currentText().upper()
        if cur == "BUY":
            self._side_mode = "LONG"
        elif cur == "SELL":
            self._side_mode = "SHORT"
        else:
            if self._side_filter.isVisible():
                pass

    def set_result(self, result: StrategyResult | None) -> None:
        has = result is not None and bool(result.trades)
        self._placeholder.setVisible(not has)
        self._table.setVisible(has)
        self._table.setRowCount(0)
        self._trades = list(result.trades) if result else []
        self._selected_index = None
        self._table.clearSelection()
        self._count_label.setVisible(False)
        if not has:
            self._refresh_symbol_items([])
            return
        assert result is not None
        self._refresh_symbol_items([t.symbol for t in result.trades])
        self._refill()

    def _format_row(self, index: int, trade: object) -> list[str]:
        return [
            str(index + 1),
            trade.symbol,  # type: ignore[attr-defined]
            trade.side,  # type: ignore[attr-defined]
            trade.entry_time[:16],  # type: ignore[attr-defined]
            f"{trade.entry_price:.2f}",  # type: ignore[attr-defined]
            trade.exit_time[:16],  # type: ignore[attr-defined]
            f"{trade.exit_price:.2f}",  # type: ignore[attr-defined]
            f"{trade.pnl:+,.2f}",  # type: ignore[attr-defined]
            f"{trade.r_multiple:.2f}" if trade.r_multiple is not None else "--",  # type: ignore[attr-defined]
            str(trade.bars_held),  # type: ignore[attr-defined]
            trade.exit_reason,  # type: ignore[attr-defined]
        ]

    def _matching_indices(self) -> list[int]:
        if self._side_filter.isVisible():
            cur = self._side_filter.currentText().upper()
            if cur == "BUY":
                self._side_mode = "LONG"
            elif cur == "SELL":
                self._side_mode = "SHORT"
            else:
                self._side_mode = "ALL"
        only_symbol = self._symbol_filter.currentText()
        if not self._symbol_filter.isVisible():
            only_symbol = "ALL"
        needle = self._needle
        out: list[int] = []
        for i, trade in enumerate(self._trades):
            if only_symbol != "ALL" and trade.symbol != only_symbol:  # type: ignore[attr-defined]
                continue
            if self._side_mode != "ALL" and trade.side != self._side_mode:  # type: ignore[attr-defined]
                continue
            if needle and needle not in " ".join(self._format_row(i, trade)).lower():
                continue
            out.append(i)
        return out

    def _refill(self) -> None:
        matched = self._matching_indices()
        total = len(matched)
        shown = matched[: self._ROW_CAP]
        table = self._table
        table.setRowCount(0)
        table.setRowCount(len(shown))
        for row, i in enumerate(shown):
            trade = self._trades[i]
            values = self._format_row(i, trade)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, i)
                if col == 7:
                    from PySide6.QtGui import QColor

                    item.setForeground(QColor(t.POS if trade.winning else t.NEG))  # type: ignore[attr-defined]
                if col == 2:
                    from PySide6.QtGui import QColor

                    if trade.side == "LONG":  # type: ignore[attr-defined]
                        item.setForeground(QColor(_BUY_ACCENT))
                    elif trade.side == "SHORT":  # type: ignore[attr-defined]
                        item.setForeground(QColor(_SELL_ACCENT))
                table.setItem(row, col, item)
        if total > len(shown):
            self._count_label.setText(
                f"Showing {len(shown):,} of {total:,} trades — filter to narrow."
            )
            self._count_label.setVisible(True)
        else:
            self._count_label.setVisible(False)

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    def set_selected_index(self, index: int | None) -> None:
        self._selected_index = index
        if index is None:
            self._table.clearSelection()
            return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole)) == index:
                self._table.selectRow(row)
                self._table.scrollToItem(item)
                return

    @property
    def symbol_filter(self) -> str:
        return self._symbol_filter.currentText()

    def set_symbol_filter(self, symbol: str) -> None:
        combo = self._symbol_filter
        idx = combo.findText(symbol)
        if idx < 0:
            idx = 0
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(False)
        self._apply_filter(self._needle)

    def _refresh_symbol_items(self, symbols: list[str]) -> None:
        combo = self._symbol_filter
        current = combo.currentText()
        unique = sorted(set(symbols))
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("ALL")
            for symbol in unique:
                combo.addItem(symbol)
            combo.setCurrentIndex(combo.findText(current) if current in unique else 0)
        finally:
            combo.blockSignals(False)
        combo.setVisible(len(unique) > 1)

    def _on_symbol_filter_changed(self, text: str) -> None:
        self._apply_filter(self._needle)
        self.symbol_filter_changed.emit(text or "ALL")

    def _on_cell(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item is not None:
            idx = int(item.data(Qt.ItemDataRole.UserRole))
            self._selected_index = idx
            self._table.selectRow(row)
            self.trade_clicked.emit(idx)

    def _on_hover(self, row: int, _col: int) -> None:
        try:
            item = self._table.item(row, 0)
            if item is None:
                return
            idx = int(item.data(Qt.ItemDataRole.UserRole))
            if 0 <= idx < len(self._trades):
                tr = self._trades[idx]
                tip = (
                    f"TRADE #{idx + 1}  {tr.symbol} · {tr.side}\n"
                    f"{tr.entry_time[:16]} → {tr.exit_time[:16]}\n"
                    f"₹{tr.entry_price:.2f} → ₹{tr.exit_price:.2f}  P&L ₹{tr.pnl:+,.2f}"
                )
                self._table.setToolTip(tip)
                with contextlib.suppress(Exception):
                    self.trade_hovered.emit(idx)
        except Exception:
            pass

    def _step_selection(self, step: int) -> bool:
        if self._selected_index is None:
            if not self._trades:
                return False
            self.set_selected_index(0)
            self.trade_clicked.emit(0)
            return True
        nxt = self._next_visible(self._selected_index + step, step)
        if nxt is None:
            return False
        self._selected_index = nxt
        self.set_selected_index(nxt)
        self.trade_clicked.emit(nxt)
        return True

    def eventFilter(self, obj, event) -> bool:  # type: ignore[no-untyped-def]  # noqa: N802
        import contextlib

        is_table = obj is self._table or obj is self._table.viewport()
        if is_table and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == 16777235:  # Qt.Key_Up
                return self._step_selection(-1)
            if key == 16777237:  # Qt.Key_Down
                return self._step_selection(1)
            if key == 16777220:  # Enter
                if self._selected_index is not None:
                    self.trade_clicked.emit(self._selected_index)
                    return True
            elif key == 16777216:  # Escape
                with contextlib.suppress(Exception):
                    self._filter.clearFocus()
                    self._table.clearFocus()
                    self._table.clearSelection()
                return False
        return super().eventFilter(obj, event)

    def _next_visible(self, start: int, step: int) -> int | None:
        idx = start
        while 0 <= idx < len(self._trades):
            for row in range(self._table.rowCount()):
                it = self._table.item(row, 0)
                if it is not None and int(it.data(Qt.ItemDataRole.UserRole)) == idx:
                    if not self._table.isRowHidden(row):
                        return idx
                    break
            idx += step
        return None

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if event.key() in (16777235, 16777237, 16777220, 16777216) and self.eventFilter(
            self._table, event
        ):
            event.accept()
            return
        super().keyPressEvent(event)

    def _apply_filter(self, text: str) -> None:
        if text is not None:
            sender = self.sender()
            if sender is self._filter or text != "":
                self._needle = text.strip().lower() if isinstance(text, str) else ""
            if sender is self._side_filter or sender is self._symbol_filter:
                pass
            elif isinstance(text, str) and text != "" or sender is self._filter:
                self._needle = text.strip().lower() if isinstance(text, str) else ""
        self._refill()

    def _export_csv(self) -> None:
        if not self._trades:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export trades", "trades.csv", "CSV (*.csv)")
        if not path:
            return
        import csv

        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "#",
                    "symbol",
                    "side",
                    "entry_time",
                    "entry_price",
                    "exit_time",
                    "exit_price",
                    "qty",
                    "pnl",
                    "pnl_pct",
                    "r_multiple",
                    "bars",
                    "reason",
                ]
            )
            for i, trade in enumerate(self._trades, start=1):
                writer.writerow(
                    [
                        i,
                        trade.symbol,
                        trade.side,
                        trade.entry_time,
                        trade.entry_price,
                        trade.exit_time,
                        trade.exit_price,
                        trade.quantity,
                        trade.pnl,
                        trade.pnl_pct,
                        trade.r_multiple,
                        trade.bars_held,
                        trade.exit_reason,
                    ]
                )


# ═══════════════════════════════════════════════════════════════════════
#  COMPARISON helpers
# ═══════════════════════════════════════════════════════════════════════

_METRIC_ORDER = (
    "Net Profit",
    "Total Trades",
    "Win Rate",
    "Profit Factor",
    "Expectancy",
    "Max Drawdown",
    "Sharpe",
    "Avg Trade",
)


def _metric_values(result: StrategyResult | None) -> dict[str, float | None]:
    if result is None:
        return {k: None for k in _METRIC_ORDER}
    m = result.metrics
    return {
        "Net Profit": m.net_profit if m.total_trades else None,
        "Total Trades": float(m.total_trades) if m.total_trades is not None else None,
        "Win Rate": m.win_rate,
        "Profit Factor": m.profit_factor,
        "Expectancy": m.expectancy,
        "Max Drawdown": m.max_drawdown_pct,
        "Sharpe": m.sharpe_ratio,
        "Avg Trade": m.avg_trade,
    }


def _is_higher_better(key: str) -> bool:
    return key != "Max Drawdown"


def _format_metric(key: str, value: float | None) -> str:
    if value is None:
        return "--"
    if key == "Net Profit":
        return f"₹{_inr(float(value))}"
    if key == "Total Trades":
        return f"{int(value)}"
    if key == "Win Rate":
        return f"{value * 100:.1f}%"
    if key == "Profit Factor":
        return f"{value:.2f}"
    if key == "Expectancy":
        return f"₹{_inr(float(value))}"
    if key == "Max Drawdown":
        return f"-{float(value):.2f}%"
    if key == "Sharpe":
        return f"{value:.2f}"
    if key == "Avg Trade":
        return f"₹{_inr(float(value))}"
    return str(value)


def determine_stronger_side(
    buy: StrategyResult | None, sell: StrategyResult | None
) -> tuple[str, str]:
    """Return (verdict, reason). Verdict: BUY / LONG, SELL / SHORT, TOO CLOSE TO CALL, INSUFFICIENT."""
    if buy is None or sell is None:
        return "INSUFFICIENT", "Run both sides to compare"
    if buy.metrics.total_trades == 0 or sell.metrics.total_trades == 0:
        if buy.metrics.total_trades == 0 and sell.metrics.total_trades == 0:
            return "TOO CLOSE TO CALL", "Neither side produced trades"
        if buy.metrics.total_trades == 0:
            return "INSUFFICIENT", "BUY has no trades"
        if sell.metrics.total_trades == 0:
            return "INSUFFICIENT", "SELL has no trades"
    vals_buy = _metric_values(buy)
    vals_sell = _metric_values(sell)
    buy_wins = 0
    sell_wins = 0
    reasons_buy: list[str] = []
    reasons_sell: list[str] = []
    for key in _METRIC_ORDER:
        bv = vals_buy[key]
        sv = vals_sell[key]
        if bv is None or sv is None:
            continue
        if key == "Max Drawdown":
            if abs(bv - sv) < 0.05:
                continue
            if bv < sv:
                buy_wins += 1
                reasons_buy.append(f"lower drawdown ({bv:.2f}% vs {sv:.2f}%)")
            elif sv < bv:
                sell_wins += 1
                reasons_sell.append(f"lower drawdown ({sv:.2f}% vs {bv:.2f}%)")
        else:
            if key in ("Profit Factor", "Sharpe"):
                thresh = 0.03
            elif key == "Win Rate":
                thresh = 0.02
            elif key in ("Net Profit", "Expectancy", "Avg Trade"):
                base = max(abs(bv), abs(sv), 1.0)
                if abs(bv - sv) / base < 0.01:
                    continue
                thresh = 0.0
            else:
                thresh = 0.0
                if abs(bv - sv) <= thresh:
                    continue
            if key in ("Net Profit", "Expectancy", "Avg Trade") and thresh == 0.0:
                pass
            elif abs(bv - sv) <= thresh:
                continue
            if bv > sv:
                buy_wins += 1
                if key == "Net Profit":
                    reasons_buy.append("higher net profit")
                elif key == "Profit Factor":
                    reasons_buy.append("better profit factor")
                elif key == "Sharpe":
                    reasons_buy.append("better sharpe")
                elif key == "Win Rate":
                    reasons_buy.append("higher win rate")
                else:
                    reasons_buy.append(f"higher {key.lower()}")
            elif sv > bv:
                sell_wins += 1
                if key == "Net Profit":
                    reasons_sell.append("higher net profit")
                elif key == "Profit Factor":
                    reasons_sell.append("better profit factor")
                elif key == "Sharpe":
                    reasons_sell.append("better sharpe")
                elif key == "Win Rate":
                    reasons_sell.append("higher win rate")
                else:
                    reasons_sell.append(f"higher {key.lower()}")
    total = buy_wins + sell_wins
    if total == 0:
        return "TOO CLOSE TO CALL", "Metrics are effectively equal"
    diff = abs(buy_wins - sell_wins)
    if diff >= 2 or (total >= 3 and max(buy_wins, sell_wins) / total >= 0.6):
        if buy_wins > sell_wins:
            reason = ", ".join(reasons_buy[:3]) or "more winning metrics"
            return "BUY / LONG", reason
        else:
            reason = ", ".join(reasons_sell[:3]) or "more winning metrics"
            return "SELL / SHORT", reason
    return "TOO CLOSE TO CALL", "No side dominates across key metrics"


# ═══════════════════════════════════════════════════════════════════════
#  COMPARISON MATRIX
# ═══════════════════════════════════════════════════════════════════════


class _ComparisonMatrix(QWidget):
    """Grid: Metric | BUY | SELL with winner highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)
        title = QLabel("PERFORMANCE COMPARISON", self)
        title.setStyleSheet(t.label(t.TEXT2, 9, 700, 0.8))
        lay.addWidget(title)
        lay.addWidget(_hline())
        header = QGridLayout()
        header.setContentsMargins(0, 2, 0, 0)
        header.setHorizontalSpacing(12)
        header.setVerticalSpacing(2)
        for col, txt in enumerate(("", "BUY (LONG)", "SELL (SHORT)")):
            lbl = QLabel(txt, self)
            if col == 0:
                lbl.setStyleSheet(t.label(t.MUTED, 9, 600, 0.5))
            elif col == 1:
                lbl.setStyleSheet(
                    f"color: {_BUY_ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700;"
                )
            else:
                lbl.setStyleSheet(
                    f"color: {_SELL_ACCENT}; font-size: {t.FS_LABEL}px; font-weight: 700;"
                )
            header.addWidget(lbl, 0, col)
        lay.addLayout(header)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 2, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(4)
        self._buy_labels: dict[str, QLabel] = {}
        self._sell_labels: dict[str, QLabel] = {}
        self._metric_labels: dict[str, QLabel] = {}
        for row, key in enumerate(_METRIC_ORDER):
            m_lbl = QLabel(key, self)
            m_lbl.setStyleSheet(f"color: {t.TEXT2}; font-size: {t.FS_LABEL}px; font-weight: 600;")
            self._metric_labels[key] = m_lbl
            buy_lbl = QLabel("--", self)
            buy_lbl.setStyleSheet(
                f"color: {t.TEXT}; font-size: {t.FS_LABEL}px; font-weight: 600; background: transparent; padding: 1px 4px; border-radius: 2px;"
            )
            buy_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sell_lbl = QLabel("--", self)
            sell_lbl.setStyleSheet(
                f"color: {t.TEXT}; font-size: {t.FS_LABEL}px; font-weight: 600; background: transparent; padding: 1px 4px; border-radius: 2px;"
            )
            sell_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._buy_labels[key] = buy_lbl
            self._sell_labels[key] = sell_lbl
            self._grid.addWidget(m_lbl, row, 0)
            self._grid.addWidget(buy_lbl, row, 1)
            self._grid.addWidget(sell_lbl, row, 2)
        lay.addLayout(self._grid)
        self._buy = None
        self._sell = None

    def set_results(self, buy: StrategyResult | None, sell: StrategyResult | None) -> None:
        self._buy = buy
        self._sell = sell
        vals_buy = _metric_values(buy)
        vals_sell = _metric_values(sell)
        for key in _METRIC_ORDER:
            bv = vals_buy[key]
            sv = vals_sell[key]
            buy_lbl = self._buy_labels[key]
            sell_lbl = self._sell_labels[key]
            buy_lbl.setText(_format_metric(key, bv))
            sell_lbl.setText(_format_metric(key, sv))
            _cell_base = (
                f"color: {t.TEXT}; font-size: {t.FS_LABEL}px; font-weight: 600;"
                f" background: transparent; padding: 1px 4px; border-radius: 2px;"
            )
            buy_lbl.setStyleSheet(_cell_base)
            sell_lbl.setStyleSheet(_cell_base)
            if bv is None or sv is None:
                continue
            winner = None
            if key == "Max Drawdown":
                if abs(bv - sv) < 0.05:
                    continue
                winner = "BUY" if bv < sv else "SELL"
            else:
                if key in ("Profit Factor", "Sharpe"):
                    if abs(bv - sv) <= 0.03:
                        continue
                elif key == "Win Rate":
                    if abs(bv - sv) <= 0.02:
                        continue
                elif key in ("Net Profit", "Expectancy", "Avg Trade"):
                    base = max(abs(bv), abs(sv), 1.0)
                    if abs(bv - sv) / base < 0.01:
                        continue
                if bv > sv:
                    winner = "BUY"
                elif sv > bv:
                    winner = "SELL"
            if winner == "BUY":
                buy_lbl.setStyleSheet(
                    f"color: {t.ACCENT_DEEP}; font-size: {t.FS_LABEL}px; font-weight: 700;"
                    f" background: {_BUY_ACCENT}; padding: 1px 4px; border-radius: 2px;"
                )
            elif winner == "SELL":
                sell_lbl.setStyleSheet(
                    f"color: {t.TEXT}; font-size: {t.FS_LABEL}px; font-weight: 700;"
                    f" background: {_SELL_ACCENT}; padding: 1px 4px; border-radius: 2px;"
                )


# ═══════════════════════════════════════════════════════════════════════
#  STRONGER BANNER
# ═══════════════════════════════════════════════════════════════════════


class _StrongerBanner(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: 3px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(2)
        self._kicker = QLabel("STRONGER SIDE", self)
        self._kicker.setStyleSheet(t.label(t.MUTED, 9, 700, 0.5))
        self._kicker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._kicker)
        self._verdict = QLabel("TOO CLOSE TO CALL", self)
        self._verdict.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._verdict.setStyleSheet(
            f"color: {t.TEXT}; font-size: {t.FS_METRIC_SM}px; font-weight: 800;"
        )
        lay.addWidget(self._verdict)
        self._reason = QLabel("", self)
        self._reason.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet(f"color: {t.TEXT2}; font-size: {t.FS_LABEL}px;")
        lay.addWidget(self._reason)

    def set_verdict(self, verdict: str, reason: str) -> None:
        self._verdict.setText(verdict)
        self._reason.setText(reason)
        if verdict == "BUY / LONG":
            self._verdict.setStyleSheet(
                f"color: {_BUY_ACCENT}; font-size: {t.FS_METRIC_SM}px; font-weight: 800;"
            )
            self.setStyleSheet(
                f"background: {t.PANEL}; border: 1px solid {_BUY_ACCENT}; border-radius: 3px;"
            )
        elif verdict == "SELL / SHORT":
            self._verdict.setStyleSheet(
                f"color: {_SELL_ACCENT}; font-size: {t.FS_METRIC_SM}px; font-weight: 800;"
            )
            self.setStyleSheet(
                f"background: {t.PANEL}; border: 1px solid {_SELL_ACCENT}; border-radius: 3px;"
            )
        elif verdict == "INSUFFICIENT":
            self._verdict.setStyleSheet(
                f"color: {t.MUTED}; font-size: {t.FS_TABLE}px; font-weight: 700;"
            )
            self.setStyleSheet(
                f"background: {t.BG1}; border: 1px dashed {t.BORDER}; border-radius: 3px;"
            )
        else:
            self._verdict.setStyleSheet(
                f"color: {t.TEXT}; font-size: {t.FS_BODY}px; font-weight: 700;"
            )
            self.setStyleSheet(
                f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: 3px;"
            )


# ═══════════════════════════════════════════════════════════════════════
#  STOCK COMPARISON BOARD — column-per-stock
# ═══════════════════════════════════════════════════════════════════════

_COMPARE_COL_CAP = 6

_COMPARE_ROWS: tuple[tuple[str, str, str, bool | None], ...] = (
    ("NET P&L", "net_profit", "money", True),
    ("RETURN", "return_pct", "pct", True),
    ("WIN RATE", "win_rate", "rate", True),
    ("PROFIT FACTOR", "profit_factor", "ratio", True),
    ("MAX DD", "max_drawdown_pct", "dd", False),
    ("SHARPE", "sharpe_ratio", "ratio", True),
    ("TRADES", "total_trades", "count", None),
)


def _compare_cell(kind: str, value: float | None) -> str:
    if value is None:
        return "--"
    if kind == "money":
        return _signed_inr(float(value))
    if kind == "pct":
        return f"{float(value):+.2f}%"
    if kind == "rate":
        return f"{float(value) * 100:.1f}%"
    if kind == "dd":
        return f"-{float(value):.2f}%"
    if kind == "count":
        return f"{int(value)}"
    return f"{float(value):.2f}"


class _StockComparisonBoard(QWidget):
    """Column-per-stock comparison — the COMPARE decision surface."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(t.SECTION_QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(t.SP_LG, t.SP_MD, t.SP_LG, t.SP_LG)
        lay.setSpacing(t.SP_SM)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(t.SP_MD)
        self._title = QLabel("STOCK COMPARISON", self)
        self._title.setStyleSheet(t.section_title(12))
        head.addWidget(self._title)
        self._scope = QLabel("", self)
        self._scope.setStyleSheet(t.label(t.MUTED, t.FS_LABEL, 600, 0.6))
        head.addWidget(self._scope)
        head.addStretch(1)
        self._leader = QLabel("", self)
        self._leader.setStyleSheet(t.label(t.ACCENT, t.FS_LABEL, 700, 0.6))
        head.addWidget(self._leader)
        lay.addLayout(head)
        lay.addWidget(_hline())
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, t.SP_SM, 0, 0)
        self._grid.setHorizontalSpacing(t.SP_XL)
        self._grid.setVerticalSpacing(t.SP_SM)
        lay.addLayout(self._grid)
        self._empty = QLabel("", self)
        self._empty.setStyleSheet(t.body(t.FS_SMALL, t.MUTED, 500))
        self._empty.setWordWrap(True)
        lay.addWidget(self._empty)
        lay.addStretch(1)
        self._ranked: tuple[StockRankRow, ...] = ()
        self._stocks: tuple[StockRankRow, ...] = ()
        self._total = 0
        self._has_universe = False
        self._render()

    def set_rows(self, rows: tuple[StockRankRow, ...] | list[StockRankRow]) -> None:
        all_rows = tuple(rows or ())
        self._has_universe = bool(all_rows)
        self._ranked = tuple(
            row for row in all_rows if row.status == "ranked" and row.net_profit is not None
        )
        self._total = len(self._ranked)
        self._render()

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _wins(self) -> dict[str, int]:
        wins = {row.symbol: 0 for row in self._stocks}
        for _label, attr, _kind, higher in _COMPARE_ROWS:
            if higher is None:
                continue
            values = {
                row.symbol: value
                for row in self._stocks
                if (value := getattr(row, attr, None)) is not None
            }
            if len(values) < 2:
                continue
            best = max(values.values()) if higher else min(values.values())
            winners = [symbol for symbol, value in values.items() if value == best]
            if len(winners) == 1:
                wins[winners[0]] += 1
        return wins

    def _render(self) -> None:
        self._clear_grid()
        self._stocks = self._ranked[:_COMPARE_COL_CAP]
        stocks = self._stocks
        if not stocks:
            self._scope.setText("")
            self._leader.setText("")
            self._empty.setText(
                "SELECT STOCKS TO COMPARE\n"
                "Choose the universe in Backtest Configuration, then run the backtest."
                if not self._has_universe
                else "NO COMPARABLE RESULTS\n"
                "Run the backtest — no selected stock produced a usable result."
            )
            self._empty.setVisible(True)
            return
        self._empty.setVisible(False)
        shown = len(stocks)
        noun = "STOCK" if self._total == 1 else "STOCKS"
        self._scope.setText(
            f"{self._total} {noun} COMPARED"
            if self._total <= shown
            else f"TOP {shown} OF {self._total} BY NET P&L"
        )
        wins = self._wins()
        leader = None
        if wins:
            best_wins = max(wins.values())
            top = [symbol for symbol, count in wins.items() if count == best_wins]
            if best_wins >= 2 and len(top) == 1:
                leader = top[0]
        self._leader.setText(f"◆ LEADER  {leader}" if leader else "")
        for row, (label, _attr, _kind, _higher) in enumerate(_COMPARE_ROWS):
            lbl = QLabel(label, self)
            lbl.setStyleSheet(t.label(t.TEXT2, t.FS_LABEL, 700, 0.6))
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._grid.addWidget(lbl, row + 1, 0)
        for col, rank_row in enumerate(stocks, start=1):
            header = QWidget(self)
            header.setStyleSheet("background: transparent;")
            hl = QVBoxLayout(header)
            hl.setContentsMargins(0, 0, 0, t.SP_SM)
            hl.setSpacing(0)
            name = QLabel(rank_row.symbol, header)
            name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            name.setStyleSheet(
                f"color: {t.ACCENT if rank_row.symbol == leader else t.TEXT};"
                f" font-size: {t.FS_BODY}px; font-weight: 800; letter-spacing: 0.4px;"
            )
            hl.addWidget(name)
            sub = QLabel(f"{rank_row.total_trades} trades", header)
            sub.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sub.setStyleSheet(t.label(t.MUTED, t.FS_LABEL, 600, 0.4))
            hl.addWidget(sub)
            self._grid.addWidget(header, 0, col)
        for row, (_label, attr, kind, higher) in enumerate(_COMPARE_ROWS):
            values = {rank_row.symbol: getattr(rank_row, attr, None) for rank_row in stocks}
            present = [value for value in values.values() if value is not None]
            best: float | None = None
            if higher is not None and len(present) >= 2:
                candidate = max(present) if higher else min(present)
                if present.count(candidate) == 1:
                    best = candidate
            for col, rank_row in enumerate(stocks, start=1):
                value = values[rank_row.symbol]
                cell = QLabel(_compare_cell(kind, value), self)
                cell.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                size = t.FS_METRIC_SM if row == 0 else t.FS_TABLE
                if best is not None and value == best:
                    style = t.metric(size, t.semantic(value), 800)
                elif value is None:
                    style = t.metric(size, t.MUTED, 600)
                else:
                    style = t.metric(size, t.TEXT2, 600)
                cell.setStyleSheet(style)
                self._grid.addWidget(cell, row + 1, col)
        self._grid.setColumnStretch(0, 0)
        for col in range(1, shown + 1):
            self._grid.setColumnStretch(col, 1)
            self._grid.setColumnMinimumWidth(col, 104)


# ═══════════════════════════════════════════════════════════════════════
#  COMPARE VIEW — full comparison workspace
# ═══════════════════════════════════════════════════════════════════════


class _CompareView(QWidget):
    """Full comparison workspace for COMPARE mode — scrollable."""

    run_all_requested = Signal()
    run_buy_requested = Signal()
    run_sell_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG0};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {t.BG0}; border: none; }} {t.SCROLLBAR_QSS}"
        )
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        content.setStyleSheet(f"background: {t.BG0};")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        self._compare_header = QLabel("COMPARE — COMBINED", content)
        self._compare_header.setStyleSheet(t.label(t.TEXT, 10, 800, 0.7))
        self._compare_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._compare_header)
        lay.addWidget(_hline())
        self._banner = _StrongerBanner(content)
        lay.addWidget(self._banner)
        self._stale_notice = QLabel("", content)
        self._stale_notice.setWordWrap(True)
        self._stale_notice.setStyleSheet(
            f"color: {t.WARN}; font-size: {t.FS_SMALL}px; background: {t.WARN_DIM};"
            f" border: 1px solid {t.WARN}; padding: 4px 8px; border-radius: 2px;"
        )
        self._stale_notice.setVisible(False)
        lay.addWidget(self._stale_notice)
        self._comparison = _StockComparisonBoard(content)
        lay.addWidget(self._comparison)
        from app.ui.stock_ranking import StockRankingWidget

        self._ranking = StockRankingWidget(content)
        lay.addWidget(self._ranking)
        self._matrix = _ComparisonMatrix(content)
        lay.addWidget(self._matrix)
        eq_title = QLabel("EQUITY COMPARISON", content)
        eq_title.setStyleSheet(t.label(t.TEXT2, 9, 700, 0.7))
        lay.addWidget(eq_title)
        self._equity = _DualEquityView(content)
        self._equity.setMinimumHeight(160)
        lay.addWidget(self._equity)
        tr_title = QLabel("TRADE COMPARISON", content)
        tr_title.setStyleSheet(t.label(t.TEXT2, 9, 700, 0.7))
        lay.addWidget(tr_title)
        self._trade_summary = QLabel("", content)
        self._trade_summary.setStyleSheet(
            f"color: {t.MUTED}; font-size: {t.FS_LABEL}px; background: {t.BG1};"
            f" padding: 4px 8px; border: 1px solid {t.BORDER}; border-radius: 2px;"
        )
        self._trade_summary.setWordWrap(True)
        lay.addWidget(self._trade_summary)
        self._trade_blotter = TradeBlotter(content)
        self._trade_blotter.setMinimumHeight(160)
        self._trade_blotter.set_side_filter_visible(True)
        self._trade_blotter.set_side_mode("ALL")
        lay.addWidget(self._trade_blotter)
        self._empty_banner = QLabel("", content)
        self._empty_banner.setStyleSheet(
            f"color: {t.MUTED}; font-size: {t.FS_LABEL}px; background: {t.BG1};"
            f" border: 1px dashed {t.BORDER}; padding: 8px; border-radius: 3px;"
        )
        self._empty_banner.setWordWrap(True)
        self._empty_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_banner.setVisible(False)
        lay.addWidget(self._empty_banner)
        self._actions = QWidget(content)
        act_lay = QHBoxLayout(self._actions)
        act_lay.setContentsMargins(0, 0, 0, 0)
        act_lay.setSpacing(6)
        self._run_all = QPushButton("▶  RUN ALL", self._actions)
        self._run_all.setMinimumHeight(32)
        self._run_all.setStyleSheet(t.PRIMARY_QSS)
        self._run_buy = QPushButton("BUY only", self._actions)
        self._run_buy.setStyleSheet(t.QUIET_BUTTON_QSS)
        self._run_sell = QPushButton("SELL only", self._actions)
        self._run_sell.setStyleSheet(t.QUIET_BUTTON_QSS)
        self._run_buy.clicked.connect(self.run_buy_requested.emit)
        self._run_sell.clicked.connect(self.run_sell_requested.emit)
        self._run_all.clicked.connect(self.run_all_requested.emit)
        act_lay.addWidget(self._run_all)
        act_lay.addWidget(self._run_buy)
        act_lay.addWidget(self._run_sell)
        act_lay.addStretch(1)
        self._actions.setVisible(False)
        lay.addWidget(self._actions)
        lay.addStretch(1)
        content.setLayout(lay)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self._buy: StrategyResult | None = None
        self._sell: StrategyResult | None = None

    def set_stale_notice(self, text: str) -> None:
        self._stale_notice.setText(text)
        self._stale_notice.setVisible(bool(text))

    def set_results(
        self,
        buy: StrategyResult | None,
        sell: StrategyResult | None,
        full: StrategyResult | None = None,
    ) -> None:
        self._buy = buy
        self._sell = sell
        self._matrix.set_results(buy, sell)
        self._equity.set_results(buy, sell)
        verdict, reason = determine_stronger_side(buy, sell)
        if verdict == "INSUFFICIENT":
            if buy is None and sell is None:
                self._banner.set_verdict(
                    "NOTHING TO COMPARE YET", "Run BUY and SELL backtests to compare performance."
                )
            elif buy is None or (buy is not None and buy.metrics.total_trades == 0):
                self._banner.set_verdict("INSUFFICIENT — SELL AVAILABLE", reason)
            elif sell is None or (sell is not None and sell.metrics.total_trades == 0):
                self._banner.set_verdict("INSUFFICIENT — BUY AVAILABLE", reason)
            else:
                self._banner.set_verdict(verdict, reason)
        else:
            self._banner.set_verdict(verdict, reason)
        if buy is None and sell is None:
            self._trade_summary.setText("No trades to compare — run both backtests.")
        elif buy is None:
            self._trade_summary.setText(
                f"SELL: {len(sell.trades) if sell else 0} trades  ·  BUY: Not backtested yet"
            )
        elif sell is None:
            self._trade_summary.setText(
                f"BUY: {len(buy.trades) if buy else 0} trades  ·  SELL: Not backtested yet"
            )
        else:
            self._trade_summary.setText(
                f"BUY: {len(buy.trades)} trades  ·  SELL: {len(sell.trades)} trades  ·  TOTAL: {len(buy.trades) + len(sell.trades)}"
            )
        has_buy = buy is not None
        has_sell = sell is not None
        if not has_buy and not has_sell:
            self._empty_banner.setText(
                "NOTHING TO COMPARE YET\nRun BUY and SELL backtests to compare performance."
            )
            self._empty_banner.setVisible(True)
            self._actions.setVisible(True)
            self._run_buy.setVisible(True)
            self._run_sell.setVisible(True)
        elif not has_buy or (has_buy and buy.metrics.total_trades == 0 and has_sell):
            if has_sell:
                self._empty_banner.setText("BUY — Not backtested yet")
            else:
                self._empty_banner.setText("BUY — Not backtested yet")
            self._empty_banner.setVisible(True)
            self._actions.setVisible(True)
        elif not has_sell or (has_sell and sell.metrics.total_trades == 0 and has_buy):
            if has_buy:
                self._empty_banner.setText("SELL — Not backtested yet")
            self._empty_banner.setVisible(True)
            self._actions.setVisible(True)
        else:
            self._empty_banner.setVisible(False)
            self._actions.setVisible(True)
            self._run_buy.setVisible(True)
            self._run_sell.setVisible(True)
            self._run_all.setVisible(True)
        if not has_buy and not has_sell:
            self._run_all.setText("RUN ALL")
        elif not has_buy:
            self._run_buy.setVisible(True)
        elif not has_sell:
            self._run_sell.setVisible(True)
        try:
            combined: StrategyResult | None = None
            if full is not None:
                combined = full
            elif buy is not None and sell is not None:
                merged = tuple(
                    sorted(
                        (*buy.trades, *sell.trades), key=lambda tr: getattr(tr, "entry_index", 0)
                    )
                )
                from backtest.models.result import StrategyResult as _SR

                combined = _SR(
                    strategy_id=buy.strategy_id,
                    name=buy.name,
                    config=buy.config,
                    trades=merged,
                    equity_curve=buy.equity_curve,
                    metrics=buy.metrics,
                    bars_used=buy.bars_used,
                    period_start=buy.period_start,
                    period_end=buy.period_end,
                    chart_series=buy.chart_series,
                )
            elif buy is not None:
                combined = buy
            elif sell is not None:
                combined = sell
            self._trade_blotter.set_result(combined)
            self._trade_blotter.set_side_mode("ALL")
        except Exception:
            pass

    def set_ranking(
        self,
        base: object | None,
        errors: dict[str, str] | None = None,
        last_run: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        try:
            self._ranking.set_results(base, errors, last_run, "COMPARE — COMBINED")
        except Exception:
            pass
        try:
            self._comparison.set_rows(self._ranking.rows())
        except Exception:
            pass

    def sync_ranking(
        self,
        universe: tuple[str, ...] | list[str],
        base: object | None,
        errors: dict[str, str] | None = None,
        last_run: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        try:
            self._ranking.set_universe(universe)
        except Exception:
            pass
        self.set_ranking(base, errors, last_run)

    def set_stock_count(self, count: int) -> None:
        noun = "STOCK" if count == 1 else "STOCKS"
        self._compare_header.setText(f"COMPARE — COMBINED · {count} {noun}")
        symbols = f"{count} symbol" if count == 1 else f"{count} symbols"
        self._run_all.setText(f"▶  RUN ALL · {symbols}")


# ═══════════════════════════════════════════════════════════════════════
#  STRATEGY LAB WORKSPACE — main workspace container
# ═══════════════════════════════════════════════════════════════════════


class StrategyLabWorkspace(QWidget):
    """Two-column lab with BUY / SELL / COMPARE tri-mode results."""

    run_backtest = Signal(object)
    trade_focus = Signal(int)
    param_changed = Signal(str, float)
    save_requested_relay = Signal(str)
    compile_requested_relay = Signal(str)
    stop_requested = Signal()

    _result_stack: QStackedWidget
    _collapse_btn: QToolButton
    _center_empty: QWidget

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG0};")
        self._view_mode: StrategyViewMode = StrategyViewMode.BUY
        self._full_result: StrategyResult | None = None
        self._buy_result: StrategyResult | None = None
        self._sell_result: StrategyResult | None = None
        self._pending_side: StrategyViewMode | None = None
        self._buy_owner: dict[str, object] | None = None
        self._sell_owner: dict[str, object] | None = None
        self._stale_sides: tuple[str, ...] = ()
        self._symbol_windows: dict[str, int] = {}
        self._ranking_errors: dict[str, str] = {}
        self._last_run_symbols: tuple[str, ...] | None = None
        self._run_state: str = "ready"
        self._last_batch_pct = -1
        self._busy_topbar = False
        self._stop_armed = False
        self._topbar_hint_active = False
        # Empty-dock compaction (§20): fresh workspace keeps the pinned READY
        # dock contract but collapses analytical chrome until real content.
        # Starts False so the first _sync_empty_dock() applies geometry.
        self._empty_dock = False
        self._dock_pinned_open = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── topbar + context ──
        outer.addWidget(self._build_topbar())
        outer.addWidget(self._build_context_bar())

        # ── main: sidebar | scrolling workspace ──
        self._main = QSplitter(Qt.Orientation.Horizontal, self)
        self._main.setStyleSheet(t.SPLITTER_QSS)
        self._main.setHandleWidth(1)
        self._main.setChildrenCollapsible(False)

        self.left_nav = StrategyLibraryPanel(self)
        self.center_detail = EditorPane(self)

        self._mode_selector = _ViewModeSelector(self.center_detail)
        self._mode_selector.mode_changed.connect(self.set_view_mode)
        self.center_detail.layout().insertWidget(2, self._mode_selector)  # type: ignore[attr-defined]
        self._mode_selector.setVisible(False)
        self.center_detail._stack.currentChanged.connect(self._sync_mode_selector)
        self.center_detail.save_requested.connect(self.save_requested_relay)
        self.center_detail.compile_requested.connect(self.compile_requested_relay)
        self.center_detail.tab_changed.connect(self._crumb_name.setText)
        self.center_detail.rename_affordance.connect(
            lambda: self.left_nav.request_rename(self.current_tab_name())
        )
        self.center_detail.param_changed.connect(self.param_changed.emit)
        self.center_detail.param_changed.connect(lambda *_a: self._refresh_staleness())
        self.right_settings = self.center_detail.backtest_panel
        self.right_settings.run_requested.connect(self._on_panel_run)
        self.right_settings.busy_changed.connect(self._set_run_busy)
        self.right_settings.stop_requested.connect(self.stop_requested.emit)
        try:
            self.right_settings.selection_changed.connect(
                lambda _s=None: self._refresh_perf_context()
            )
        except Exception:
            pass
        try:
            self.right_settings.config_changed.connect(self._refresh_context)
        except Exception:
            pass
        try:
            self.right_settings.config_changed.connect(lambda: self._refresh_staleness())
        except Exception:
            pass
        try:
            self.right_settings.ranking.symbol_focused.connect(self._on_ranking_focus)
        except Exception:
            pass

        self._center_empty = self._build_center_empty()
        self._center_stack = QStackedWidget(self)
        self._center_stack.addWidget(self._center_empty)
        self._center_stack.addWidget(self.center_detail)
        self._center_stack.setCurrentIndex(0)
        self._center_stack.setMinimumHeight(220)

        self._main.addWidget(self.left_nav)

        self._page_scroll = QScrollArea(self)
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._page_scroll.setStyleSheet(f"QScrollArea {{ background: {t.BG0}; border: none; }}")
        self._page = QWidget(self._page_scroll)
        self._page_lay = QVBoxLayout(self._page)
        self._page_lay.setContentsMargins(0, 0, 0, 0)
        self._page_lay.setSpacing(0)
        self._page_lay.addWidget(self._center_stack)
        self._page_scroll.setWidget(self._page)
        self._main.addWidget(self._page_scroll)
        self._main.setStretchFactor(0, 0)
        self._main.setStretchFactor(1, 1)

        self._build_results()
        assert isinstance(self._result_stack, QStackedWidget)
        assert isinstance(self._collapse_btn, QToolButton)
        results_dock = self._result_stack.parentWidget()
        assert results_dock is not None
        self._results_dock = results_dock

        self._single_host = QWidget(self._page)
        single_lay = QVBoxLayout(self._single_host)
        single_lay.setContentsMargins(0, 0, 0, 0)
        single_lay.setSpacing(0)
        self._stale_banner.setParent(self._single_host)
        self._results_dock.setParent(self._single_host)
        single_lay.addWidget(self._stale_banner)
        single_lay.addWidget(self._results_dock)
        self._page_lay.addWidget(self._single_host)

        self._compare_view = _CompareView(self)
        self._compare_view.run_all_requested.connect(self._emit_run_all)
        self._compare_view.run_buy_requested.connect(self._emit_run_buy)
        self._compare_view.run_sell_requested.connect(self._emit_run_sell)
        self._compare_view._trade_blotter.trade_clicked.connect(self.trade_focus.emit)
        self._compare_view._trade_blotter.symbol_filter_changed.connect(
            self._on_compare_symbol_filter
        )
        try:
            self._compare_view._ranking.symbol_focused.connect(self._on_ranking_focus)
        except Exception:
            pass
        self._compare_view.setVisible(False)
        self._results_container = self._compare_view  # type: ignore[assignment]

        outer.addWidget(self._main, 1)
        outer.addWidget(self._compare_view, 1)
        self._setup_shortcuts()
        self._main.setSizes([240, 880])
        self._apply_view_mode()
        self._sync_empty_dock()

    def _sync_mode_selector(self, index: int) -> None:
        try:
            self._mode_selector.setVisible(index == 2)
        except Exception:
            pass

    def _on_panel_run(self, cfg: object) -> None:
        if not self._has_open_strategy():
            with contextlib.suppress(Exception):
                self.right_settings._show_error("Open a strategy before running a backtest.")
            return
        if self._view_mode == StrategyViewMode.BUY:
            self._pending_side = StrategyViewMode.BUY
        elif self._view_mode == StrategyViewMode.SELL:
            self._pending_side = StrategyViewMode.SELL
        else:
            self._pending_side = StrategyViewMode.COMPARE
        self.run_backtest.emit(cfg)

    def _has_open_strategy(self) -> bool:
        stack = getattr(self, "_center_stack", None)
        return stack is not None and stack.currentIndex() == 1

    def _hint_open_strategy(self) -> None:
        self._topbar_hint_active = True
        self._topbar_status.setText("○ Open a strategy to run a backtest.")
        self._topbar_status.setVisible(True)

    def _clear_open_strategy_hint(self) -> None:
        if getattr(self, "_topbar_hint_active", False):
            self._topbar_hint_active = False
            self._topbar_status.setVisible(False)

    def _emit_run_buy(self) -> None:
        if not self._has_open_strategy():
            self._hint_open_strategy()
            return
        self.set_view_mode(StrategyViewMode.BUY)
        self._pending_side = StrategyViewMode.BUY
        self.run_backtest.emit(self.right_settings.current_config())

    def _emit_run_sell(self) -> None:
        if not self._has_open_strategy():
            self._hint_open_strategy()
            return
        self.set_view_mode(StrategyViewMode.SELL)
        self._pending_side = StrategyViewMode.SELL
        self.run_backtest.emit(self.right_settings.current_config())

    def _emit_run_all(self) -> None:
        if not self._has_open_strategy():
            self._hint_open_strategy()
            return
        self._pending_side = StrategyViewMode.COMPARE
        self.run_backtest.emit(self.right_settings.current_config())

    def _build_center_empty(self) -> QWidget:
        widget = QWidget(self)
        widget.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(24, 36, 24, 36)
        lay.setSpacing(8)
        lay.addStretch(1)
        icon = QLabel("◧", widget)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_HERO}px;")
        lay.addWidget(icon)
        title = QLabel("No strategy selected", widget)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {t.TEXT}; font-size: {t.FS_TABLE}px; font-weight: 600;")
        lay.addWidget(title)
        sub = QLabel(
            "Select an existing strategy from the list or\ncreate a new strategy to get started.",
            widget,
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {t.MUTED}; font-size: {t.FS_LABEL}px;")
        lay.addWidget(sub)
        lay.addSpacing(6)
        button = QPushButton("+  NEW STRATEGY", widget)
        button.setStyleSheet(
            f"QPushButton {{ background: {t.ACCENT}; border: none; border-radius: 3px;"
            f" color: {t.ACCENT_DEEP}; padding: 6px 14px; font-size: {t.FS_LABEL}px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {t.ACCENT_DIM}; }}"
        )
        button.clicked.connect(self.left_nav.new_strategy_requested.emit)
        lay.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        return widget

    def _set_center_empty(self, empty: bool) -> None:
        self._center_stack.setCurrentIndex(0 if empty else 1)
        if empty:
            self._crumb_name.setText("")
            self._full_result = None
            self._buy_result = None
            self._sell_result = None
            self._buy_owner = None
            self._sell_owner = None
            self._stale_sides = ()
            self._last_run_symbols = None
            self._ranking_errors = {}
            self.metrics.set_result(None)
            self._equity_view.set_result(None)
            self._drawdown_view.set_result(None)  # type: ignore[attr-defined]
            self.journal.set_result(None)
            try:
                self._compare_view.set_results(None, None)
            except Exception:
                pass
            self._update_equity_summary(None)
            self.set_run_state("ready")
            self._refresh_staleness()
            self._push_ranking()

    def _build_topbar(self) -> QWidget:
        """Strategy command center: identity + lifecycle actions."""
        bar = QWidget(self)
        bar.setObjectName("LabTopBar")
        bar.setFixedHeight(t.H_TOPBAR)
        bar.setStyleSheet(
            f"QWidget#LabTopBar {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(t.SP_XL, t.SP_XS, t.SP_LG, t.SP_XS)
        lay.setSpacing(t.SP_SM)
        brand = QLabel("VAYREN", bar)
        brand.setStyleSheet(
            f"color: {t.ACCENT}; font-size: {t.FS_SMALL}px; font-weight: 800; letter-spacing: 1px;"
        )
        lay.addWidget(brand)
        crumb = QLabel("/  STRATEGY LAB  /", bar)
        crumb.setStyleSheet(t.body(t.FS_SMALL, t.MUTED))
        lay.addWidget(crumb)
        self._crumb_name = QLabel("", bar)
        self._crumb_name.setStyleSheet(
            f"color: {t.TEXT}; font-size: {t.FS_SMALL}px; font-weight: 700;"
        )
        lay.addWidget(self._crumb_name)
        # Strategy identity (§14): version + last modified from the library
        # record. Empty when unknown — never invented.
        self._crumb_meta = QLabel("", bar)
        self._crumb_meta.setStyleSheet(t.body(t.FS_LABEL, t.MUTED))
        lay.addWidget(self._crumb_meta)
        lay.addStretch(1)
        self._topbar_status = QLabel("", bar)
        self._topbar_status.setStyleSheet(t.body(t.FS_SMALL, t.ACCENT, 700))
        self._topbar_status.setVisible(False)
        lay.addWidget(self._topbar_status)
        save_btn = QPushButton("Save", bar)
        save_btn.setStyleSheet(t.BUTTON_QSS)
        save_btn.setToolTip("Save the strategy source (Ctrl+S)")
        save_btn.clicked.connect(
            lambda: self.save_requested_relay.emit(self.center_detail.get_code())
        )
        compile_btn = QPushButton("Compile", bar)
        compile_btn.setStyleSheet(t.BUTTON_QSS)
        compile_btn.setToolTip("Compile the strategy and surface any errors")
        compile_btn.clicked.connect(
            lambda: self.compile_requested_relay.emit(self.center_detail.get_code())
        )
        self._topbar_run = QPushButton("▶  RUN", bar)
        self._topbar_run.setStyleSheet(t.TOP_RUN_QSS)
        self._topbar_run.setMinimumHeight(24)
        self._topbar_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self._topbar_run.setToolTip("Run the current experiment (Ctrl+Enter)")
        self._topbar_run.clicked.connect(self._on_topbar_run)
        lay.addWidget(save_btn)
        lay.addWidget(compile_btn)
        lay.addWidget(self._topbar_run)
        return bar

    def _build_context_bar(self) -> QWidget:
        self._context_bar = ExperimentContextBar(self)
        self._refresh_context()
        return self._context_bar

    def _refresh_context(self) -> None:
        bar = getattr(self, "_context_bar", None)
        if bar is None:
            return
        try:
            panel = self.right_settings
            selected = panel.selected_symbols()
            count = len(selected)
            noun = "STOCK" if count == 1 else "STOCKS"
            universe = f"{count} {noun}" if count else "NO UNIVERSE"
            timeframe = panel._tf_combo.currentText().strip() or "—"
            dates = (
                f"{_short_date(panel._from.date().toString('yyyy-MM-dd'))} → "
                f"{_short_date(panel._to.date().toString('yyyy-MM-dd'))}"
            )
            capital = _compact_inr(float(panel._capital.value()))
        except Exception:
            universe, timeframe, dates, capital = "—", "—", "—", "—"
        mode = self._ranking_mode_label()
        bar.set_context(
            strategy=self._crumb_name.text(),
            mode=mode,
            universe=universe,
            timeframe=timeframe,
            dates=dates,
            capital=capital,
            state=self._run_state,
        )

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().resizeEvent(event)
        compact = self.height() < 820
        with contextlib.suppress(Exception):
            self._context_bar.set_compact(compact)
        with contextlib.suppress(Exception):
            self.metrics.set_compact(compact)

    def _build_results(self) -> None:
        dock = QWidget(self)
        dock.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(dock)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._dock_lay = lay

        self._stale_banner = self._build_stale_banner(dock)
        self._stale_banner.setVisible(False)
        lay.addWidget(self._stale_banner)
        self.metrics = MetricsTiles(dock)
        lay.addWidget(self.metrics)

        strip = QWidget(dock)
        strip.setObjectName("ResultStrip")
        strip.setStyleSheet(
            f"QWidget#ResultStrip {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        strip.setFixedHeight(32)
        strip_lay = QHBoxLayout(strip)
        strip_lay.setContentsMargins(t.SP_LG, 0, t.SP_SM, 0)
        strip_lay.setSpacing(t.SP_XS)
        group = QButtonGroup(strip)
        group.setExclusive(True)
        stack = QStackedWidget(dock)
        from backtest.ui.analytics_views import DrawdownView, EquityCurveView

        perf_page = QWidget(dock)
        perf_lay = QVBoxLayout(perf_page)
        perf_lay.setContentsMargins(0, 0, 0, 0)
        perf_lay.setSpacing(0)
        self._single_ranking = self.right_settings.ranking
        self._single_ranking.setMinimumHeight(340)
        perf_lay.addWidget(self._single_ranking, 1)
        self._single_ranking.setVisible(True)
        stack.addWidget(perf_page)

        self.journal = TradeBlotter(dock)
        self.journal.setMinimumHeight(280)
        self.journal.trade_clicked.connect(self.trade_focus.emit)
        self.journal.symbol_filter_changed.connect(self._on_symbol_filter)
        stack.addWidget(self.journal)

        equity_page = QWidget(dock)
        equity_lay = QVBoxLayout(equity_page)
        equity_lay.setContentsMargins(0, 0, 0, 0)
        equity_lay.setSpacing(0)
        self._equity_summary = QLabel("", equity_page)
        self._equity_summary.setStyleSheet(
            f"color: {t.TEXT2}; font-size: {t.FS_SMALL}px; padding: 4px 12px;"
            f" background: {t.BG0}; border-bottom: 1px solid {t.BORDER};"
        )
        equity_lay.addWidget(self._equity_summary)
        self._equity_view = EquityCurveView(equity_page)
        self._equity_view.setMinimumHeight(280)
        equity_lay.addWidget(self._equity_view, 1)
        stack.addWidget(equity_page)

        self._drawdown_view = DrawdownView(dock)
        self._drawdown_view.setMinimumHeight(280)
        stack.addWidget(self._drawdown_view)

        _tab_qss = t.TAB_QSS
        for index, text in enumerate(("PERFORMANCE", "TRADES", "EQUITY", "DRAWDOWN")):
            button = QPushButton(text, strip)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_tab_qss)
            button.clicked.connect(lambda _c=False, i=index: self.show_result(i, expand=True))
            group.addButton(button)
            strip_lay.addWidget(button)
        strip_lay.addStretch(1)
        collapse = QToolButton(strip)
        collapse.setText("▴")
        collapse.setToolTip("Expand / collapse analytical views")
        collapse.setStyleSheet(t.TOOL_QSS)
        collapse.clicked.connect(lambda: self.toggle_results())
        strip_lay.addWidget(collapse)

        lay.addWidget(strip)
        lay.addWidget(stack, 1)
        stack.setVisible(True)
        self._result_stack = stack
        self._strip = strip
        self._collapse_btn = collapse
        self._collapse_btn.setText("▾")
        # Analytical page minimums — captured once so empty-dock compaction
        # can collapse/restore geometry without hardcoding heights twice.
        self._dock_page_min = {
            self._single_ranking: self._single_ranking.minimumHeight(),
            self.journal: self.journal.minimumHeight(),
            self._equity_view: self._equity_view.minimumHeight(),
            self._drawdown_view: self._drawdown_view.minimumHeight(),
        }

    def _build_stale_banner(self, parent: QWidget) -> QWidget:
        host = QWidget(parent)
        host.setStyleSheet(
            f"background: {t.BG1}; border: 1px solid {t.BORDER};"
            f" border-left: 3px solid {t.WARN}; border-radius: 2px;"
        )
        lay = QHBoxLayout(host)
        lay.setContentsMargins(t.SP_SM, t.SP_XS, t.SP_SM, t.SP_XS)
        lay.setSpacing(t.SP_SM)
        self._stale_icon = QLabel("⚠", host)
        self._stale_icon.setStyleSheet(f"color: {t.WARN}; font-size: {t.FS_SMALL}px;")
        lay.addWidget(self._stale_icon)
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(0)
        self._stale_title = QLabel("RESULTS OUT OF DATE", host)
        self._stale_title.setStyleSheet(
            f"color: {t.WARN}; font-size: {t.FS_SMALL}px; font-weight: 700;"
        )
        text_box.addWidget(self._stale_title)
        self._stale_detail = QLabel("", host)
        self._stale_detail.setWordWrap(True)
        self._stale_detail.setStyleSheet(f"color: {t.TEXT2}; font-size: {t.FS_LABEL}px;")
        text_box.addWidget(self._stale_detail)
        lay.addLayout(text_box, 1)
        self._stale_run = QPushButton("RUN AGAIN", host)
        self._stale_run.setStyleSheet(t.QUIET_BUTTON_QSS)
        self._stale_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stale_run.setToolTip("Run the backtest for the current configuration")
        self._stale_run.clicked.connect(self._on_topbar_run)
        lay.addWidget(self._stale_run)
        return host

    # ── result ownership ──────────────────────────────────────────────

    def _current_selection(self) -> tuple[str, ...]:
        try:
            return tuple(self.right_settings.selected_symbols() or ())
        except Exception:
            return ()

    def _current_fingerprint(self) -> dict[str, object]:
        try:
            raw = self.right_settings.current_config()
        except Exception:
            raw = None
        cfg: dict[str, object] = raw if isinstance(raw, dict) else {}
        symbols: tuple[str, ...] = ()
        with contextlib.suppress(Exception):
            claimed = cfg.get("symbols") or ()
            if isinstance(claimed, (tuple, list)):
                symbols = tuple(sorted(str(s) for s in claimed))
        try:
            params = tuple(sorted((self.center_detail.current_params() or {}).items()))
        except Exception:
            params = ()
        return {
            "strategy": self._crumb_name.text().strip(),
            "symbols": symbols,
            "timeframe": str(cfg.get("timeframe") or "").strip(),
            "start_date": str(cfg.get("start_date") or ""),
            "end_date": str(cfg.get("end_date") or ""),
            "initial_capital": cfg.get("initial_capital"),
            "slippage_pct": cfg.get("slippage_pct"),
            "commission_pct": cfg.get("commission_pct"),
            "params": params,
        }

    def _capture_owner(self, result: StrategyResult | None) -> dict[str, object] | None:
        if result is None:
            return None
        if self._last_run_symbols:
            symbols = tuple(sorted(str(s) for s in self._last_run_symbols))
        else:
            single = str(getattr(getattr(result, "config", None), "symbol", "") or "")
            if single and single != "MULTI":
                symbols = (single,)
            else:
                symbols = tuple(sorted(self._current_selection()))
        cfg = getattr(result, "config", None)
        try:
            params = tuple(sorted((self.center_detail.current_params() or {}).items()))
        except Exception:
            params = ()
        return {
            "strategy": self._crumb_name.text().strip(),
            "symbols": symbols,
            "timeframe": str(getattr(cfg, "timeframe", "") or "").strip(),
            "start_date": str(getattr(cfg, "start_date", "") or ""),
            "end_date": str(getattr(cfg, "end_date", "") or ""),
            "initial_capital": getattr(cfg, "initial_capital", None),
            "slippage_pct": getattr(cfg, "slippage_pct", None),
            "commission_pct": getattr(cfg, "commission_pct", None),
            "params": params,
        }

    @staticmethod
    def _owner_matches(owner: dict[str, object] | None, current: dict[str, object]) -> bool:
        if owner is None:
            return True
        for key in (
            "strategy",
            "symbols",
            "timeframe",
            "start_date",
            "end_date",
            "initial_capital",
            "slippage_pct",
            "commission_pct",
        ):
            if owner.get(key) != current.get(key):
                return False
        owner_params = owner.get("params") or ()
        current_params = current.get("params") or ()
        if not owner_params or not current_params:
            return True
        return bool(owner_params == current_params)

    def _refresh_staleness(self) -> None:
        banner = getattr(self, "_stale_banner", None)
        if banner is None:
            return
        if self._run_state in ("running", "aggregating", "finalizing"):
            self._stale_sides = ()
            banner.setVisible(False)
            self._sync_empty_dock()
            return
        sides: list[tuple[str, StrategyResult | None, dict[str, object] | None]]
        if self._view_mode == StrategyViewMode.COMPARE:
            sides = [
                ("BUY", self._buy_result, self._buy_owner),
                ("SELL", self._sell_result, self._sell_owner),
            ]
        elif self._view_mode == StrategyViewMode.SELL:
            sides = [("SELL", self._sell_result, self._sell_owner)]
        else:
            sides = [("BUY", self._buy_result, self._buy_owner)]
        if not any(result is not None for _, result, _ in sides):
            self._stale_sides = ()
            banner.setVisible(False)
            self._sync_empty_dock()
            return
        current = self._current_fingerprint()
        stale = [
            label
            for label, result, owner in sides
            if result is not None and not self._owner_matches(owner, current)
        ]
        historical = self._run_state == "failed"
        if not stale and not historical:
            self._stale_sides = ()
            banner.setVisible(False)
            with contextlib.suppress(Exception):
                self._compare_view.set_stale_notice("")
            self._sync_empty_dock()
            return
        self._stale_sides = tuple(stale)
        if historical and not stale:
            title = "LAST RUN FAILED — SHOWING PREVIOUS RESULTS"
            detail = (
                "The latest run did not finish. These results are historical, "
                "not the current state. Retry for the configuration above."
            )
        else:
            scope = " / ".join(stale) if stale else "RESULTS"
            title = f"⚠ {scope} OUT OF DATE" if stale else "⚠ RESULTS OUT OF DATE"
            detail = (
                "Configuration changed since this backtest — "
                "strategy, universe, timeframe, dates, capital or parameters. "
                "Run again to generate results for the current configuration."
            )
        in_compare = self._view_mode == StrategyViewMode.COMPARE
        self._stale_title.setText(title)
        self._stale_detail.setText(detail)
        banner.setVisible(not in_compare)
        with contextlib.suppress(Exception):
            self._compare_view.set_stale_notice(f"{title} — {detail}" if in_compare else "")
        self._sync_empty_dock()

    # ── empty-dock compaction ─────────────────────────────────────────
    # Fresh workspace (no open strategy, no results) keeps the pinned READY
    # dock contract — results_open() True, metrics READY/-- — but collapses
    # the analytical chrome (tab strip hidden, stack zero-height) so ~600px
    # of empty KPI grid/tabs/ranking stop dominating the page (§20).
    # Geometry only: the stack stays visible, no state/ownership change.

    def _sync_empty_dock(self) -> None:
        stack = getattr(self, "_result_stack", None)
        if stack is None or self._view_mode == StrategyViewMode.COMPARE:
            return
        empty = (
            not self._has_open_strategy()
            and self._buy_result is None
            and self._sell_result is None
            and self._full_result is None
            and self._run_state == "ready"
        )
        if empty and self._dock_pinned_open:
            return
        if empty == self._empty_dock:
            return
        self._empty_dock = empty
        if empty:
            self._compact_dock_geometry()
        else:
            self._dock_pinned_open = False
            self._restore_dock_geometry()

    def _compact_dock_geometry(self) -> None:
        strip = getattr(self, "_strip", None)
        if strip is not None:
            strip.setVisible(False)
        for view in getattr(self, "_dock_page_min", {}):
            with contextlib.suppress(Exception):
                view.setMinimumHeight(0)
        # The stack stays *visible* (pinned results_open() contract) but is
        # clamped to zero height — page sizeHints alone would keep it sprawled.
        with contextlib.suppress(Exception):
            self._result_stack.setMaximumHeight(0)
        lay = getattr(self, "_dock_lay", None)
        if lay is not None:
            with contextlib.suppress(Exception):
                lay.setStretchFactor(self._result_stack, 0)

    def _restore_dock_geometry(self) -> None:
        strip = getattr(self, "_strip", None)
        if strip is not None and self._result_stack.isVisible():
            strip.setVisible(True)
        for view, height in getattr(self, "_dock_page_min", {}).items():
            with contextlib.suppress(Exception):
                view.setMinimumHeight(height)
        with contextlib.suppress(Exception):
            self._result_stack.setMaximumHeight(16777215)
        lay = getattr(self, "_dock_lay", None)
        if lay is not None:
            with contextlib.suppress(Exception):
                lay.setStretchFactor(self._result_stack, 1)

    def show_result(self, index: int, expand: bool) -> None:
        self._result_stack.setCurrentIndex(index)
        if expand and not self._result_stack.isVisible():
            self.toggle_results(open_it=True)
        if expand and getattr(self, "_empty_dock", False):
            self._dock_pinned_open = True
            self._restore_dock_geometry()

    def results_open(self) -> bool:
        if self._view_mode == StrategyViewMode.COMPARE:
            return self._results_container.isVisible()  # type: ignore[attr-defined]
        return self._result_stack.isVisible()

    def toggle_results(self, open_it: bool | None = None) -> None:
        if self._view_mode == StrategyViewMode.COMPARE:
            opening = open_it if open_it is not None else not self._results_container.isVisible()
            self._results_container.setVisible(opening)
            self._collapse_btn.setText("▾" if opening else "▴")
            return
        opening = open_it if open_it is not None else not self._result_stack.isVisible()
        strip = getattr(self, "_strip", None)
        self._result_stack.setVisible(opening)
        if strip is not None:
            strip.setVisible(opening)
        self._collapse_btn.setText("▾" if opening else "▴")
        if opening and getattr(self, "_empty_dock", False):
            self._dock_pinned_open = True
            self._restore_dock_geometry()
        elif not opening:
            self._dock_pinned_open = False

    def _set_run_busy(self, busy: bool) -> None:
        self._busy_topbar = busy
        self._topbar_run.setEnabled(True)
        if busy:
            self._topbar_run.setText("RUNNING…")
            self.set_run_state("running")
        else:
            self._stop_armed = False
            self._topbar_status.setVisible(False)
            self._topbar_run.setStyleSheet(t.TOP_RUN_QSS)
            self._apply_top_run_label()
            if self._run_state == "running":
                self.set_run_state("ready")

    def _on_topbar_run(self) -> None:
        if getattr(self, "_busy_topbar", False) and getattr(self, "_stop_armed", False):
            self.stop_requested.emit()
            return
        self._emit_run()

    def _apply_top_run_label(self) -> None:
        if self._view_mode == StrategyViewMode.BUY:
            self._topbar_run.setText("▶  RUN BUY")
        elif self._view_mode == StrategyViewMode.SELL:
            self._topbar_run.setText("▶  RUN SELL")
        else:
            self._topbar_run.setText("▶  RUN ALL")

    def _emit_run(self) -> None:
        if not self._has_open_strategy():
            self._hint_open_strategy()
            return
        if self._view_mode == StrategyViewMode.BUY:
            self._pending_side = StrategyViewMode.BUY
        elif self._view_mode == StrategyViewMode.SELL:
            self._pending_side = StrategyViewMode.SELL
        else:
            self._pending_side = StrategyViewMode.COMPARE
        self.run_backtest.emit(self.right_settings.current_config())

    def _setup_shortcuts(self) -> None:
        from PySide6.QtGui import QAction, QKeySequence

        def shortcut(seq: str, slot: object) -> None:
            action = QAction(self)
            action.setShortcut(QKeySequence(seq))
            action.triggered.connect(slot)  # type: ignore[arg-type]
            self.addAction(action)

        shortcut(
            "Ctrl+S",
            lambda: self.save_requested_relay.emit(self.center_detail.get_code()),
        )
        shortcut("Ctrl+Return", self._emit_run)

    def set_strategy_name(self, name: str) -> None:
        self._crumb_name.setText(name)
        self.center_detail.set_strategy_name(name)
        self.left_nav.select_name(name)
        self._set_center_empty(False)
        self._clear_open_strategy_hint()
        self._refresh_context()

    def current_tab_name(self) -> str:
        return self.center_detail.current_tab_name()

    def _render_strategy_meta(self, meta: dict[str, str] | None) -> None:
        """Identity line (§14): version + last modified, record-backed only."""
        label = getattr(self, "_crumb_meta", None)
        if label is None:
            return
        version = (meta or {}).get("version", "").strip()
        updated = (meta or {}).get("updated_at", "").strip()
        parts = []
        if version:
            parts.append(f"v{version}")
        if updated:
            with contextlib.suppress(Exception):
                updated = _short_date(updated[:10])
            parts.append(f"modified {updated}")
        label.setText(" · ".join(parts))
        label.setVisible(bool(parts))

    def open_strategy(self, name: str, code: str, meta: dict[str, str] | None = None) -> None:
        self.center_detail.open_buffer(name, code)
        self.set_strategy_name(name)
        self._render_strategy_meta(meta)
        with contextlib.suppress(Exception):
            self.right_settings._hide_error()
        self._full_result = None
        self._buy_result = None
        self._sell_result = None
        self._buy_owner = None
        self._sell_owner = None
        self._stale_sides = ()
        self._last_run_symbols = None
        self._ranking_errors = {}
        self.set_run_state("ready")
        self._refresh_directional_views()
        self._refresh_staleness()
        self._push_ranking()

    def select_next_after_delete(self, deleted: str) -> None:
        names = [name for name in self.left_nav.names() if name != deleted]
        target = next((name for name in names if name > deleted), None)
        if target is None and names:
            target = names[-1]
        if target is not None:
            self.strategy_open_requested.emit(target)
        else:
            self._set_center_empty(True)
            self._crumb_name.setText("")
            self._render_strategy_meta(None)
            self._full_result = None
            self._buy_result = None
            self._sell_result = None
            self._buy_owner = None
            self._sell_owner = None
            self._stale_sides = ()
            self._last_run_symbols = None
            self._ranking_errors = {}
            self.set_run_state("ready")
            self._refresh_directional_views()
            self._refresh_staleness()
            self._push_ranking()

    strategy_open_requested = Signal(str)

    # ── directional view mode ─────────────────────────────────────────

    @property
    def view_mode(self) -> StrategyViewMode:
        return self._view_mode

    @property
    def mode_selector(self) -> _ViewModeSelector:
        return self._mode_selector

    @property
    def buy_result(self) -> StrategyResult | None:
        return self._buy_result

    @property
    def sell_result(self) -> StrategyResult | None:
        return self._sell_result

    @property
    def full_result(self) -> StrategyResult | None:
        return self._full_result

    def set_view_mode(self, mode: StrategyViewMode | str) -> None:
        if isinstance(mode, str):
            try:
                mode = StrategyViewMode(mode)
            except Exception:
                return
        if mode == self._view_mode:
            return
        self._view_mode = mode  # type: ignore[assignment]
        self._mode_selector.set_mode(mode)  # type: ignore[arg-type]
        self._apply_view_mode()

    def _apply_view_mode(self) -> None:
        mode = self._view_mode
        self._mode_selector.set_mode(mode)
        self.right_settings.set_view_mode(mode)
        self.metrics.set_view_mode(mode)
        self._apply_top_run_label()
        if mode == StrategyViewMode.COMPARE:
            try:
                self._single_host.setVisible(False)
                self._compare_view.setVisible(True)
                self._collapse_btn.setText("▾")
            except Exception:
                pass
            self._refresh_directional_views()
        else:
            try:
                self._single_host.setVisible(True)
                self._compare_view.setVisible(False)
            except Exception:
                pass
            self._refresh_directional_views()
        self._push_ranking()
        self._refresh_staleness()
        self.update()

    def set_result(self, result: StrategyResult | None) -> None:
        pending = self._pending_side
        self._pending_side = None
        self._full_result = result
        if result is None:
            self._buy_result = None
            self._sell_result = None
            self._buy_owner = None
            self._sell_owner = None
        else:
            owner = self._capture_owner(result)
            try:
                from backtest import derive_directional_result

                if pending == StrategyViewMode.BUY:
                    self._buy_result = derive_directional_result(result, "LONG")
                    self._buy_owner = owner
                elif pending == StrategyViewMode.SELL:
                    self._sell_result = derive_directional_result(result, "SHORT")
                    self._sell_owner = owner
                else:
                    self._buy_result = derive_directional_result(result, "LONG")
                    self._sell_result = derive_directional_result(result, "SHORT")
                    self._buy_owner = owner
                    self._sell_owner = owner
            except Exception:
                if pending == StrategyViewMode.SELL:
                    self._sell_result = result
                    self._sell_owner = owner
                else:
                    self._buy_result = result
                    self._buy_owner = owner
            try:
                symbol = getattr(getattr(result, "config", None), "symbol", "")
                if symbol and symbol != "MULTI":
                    self._last_run_symbols = (str(symbol),)
                    self._ranking_errors = {}
            except Exception:
                pass
        self._refresh_directional_views()
        if result is not None:
            self.show_result(0, expand=True)
            try:
                self._compare_view.set_results(self._buy_result, self._sell_result, result)
            except Exception:
                pass
            self.set_run_state("complete")
        self._refresh_staleness()
        self._push_ranking()

    def set_results(self, buy: StrategyResult | None, sell: StrategyResult | None) -> None:
        """Explicitly set per-side results."""
        self._buy_result = buy
        self._sell_result = sell
        self._buy_owner = self._capture_owner(buy)
        self._sell_owner = self._capture_owner(sell)
        if buy is not None and sell is None:
            self._full_result = buy
        elif sell is not None and buy is None:
            self._full_result = sell
        elif buy is not None and sell is not None:
            pass
        self._refresh_directional_views()
        self._refresh_staleness()
        try:
            self._compare_view.set_results(buy, sell, self._full_result)
        except Exception:
            pass
        self._push_ranking()

    def set_symbol_windows(self, mapping: dict[str, int] | None) -> None:
        self._symbol_windows = dict(mapping) if mapping else {}

    def set_ranking_errors(self, errors: dict[str, str] | None, refresh: bool = True) -> None:
        self._ranking_errors = dict(errors) if errors else {}
        if refresh:
            self._push_ranking()

    def set_last_run_symbols(
        self, symbols: tuple[str, ...] | list[str] | None, refresh: bool = True
    ) -> None:
        self._last_run_symbols = tuple(symbols) if symbols else None
        if refresh:
            self._push_ranking()

    def _ranking_base(self) -> StrategyResult | None:
        if self._view_mode == StrategyViewMode.BUY:
            return self._buy_result
        if self._view_mode == StrategyViewMode.SELL:
            return self._sell_result
        return self._full_result

    def _ranking_mode_label(self) -> str:
        if self._view_mode == StrategyViewMode.BUY:
            return "BUY — LONG"
        if self._view_mode == StrategyViewMode.SELL:
            return "SELL — SHORT"
        return "COMPARE — COMBINED"

    def _refresh_perf_context(self) -> None:
        try:
            universe = self.right_settings.selected_symbols()
        except Exception:
            universe = ()
        try:
            count = len(tuple(universe or ()))
        except Exception:
            count = 0
        try:
            direction = (self._ranking_mode_label() or "").replace(" — ", " · ")
            noun = "STOCK" if count == 1 else "STOCKS"
            scope = f"{count} {noun} ANALYZED" if universe else "NO STOCKS SELECTED"
            context = f"{direction} · {scope}" if direction else scope
            self.metrics.set_context(context)
        except Exception:
            pass

    def _push_ranking(self) -> None:
        try:
            universe = self.right_settings.selected_symbols()
        except Exception:
            universe = ()
        self._refresh_perf_context()
        try:
            self.right_settings.set_ranking_results(
                self._ranking_base(),
                dict(self._ranking_errors),
                self._last_run_symbols,
                self._ranking_mode_label(),
            )
        except Exception:
            pass
        try:
            self._compare_view.sync_ranking(
                universe,
                self._full_result,
                dict(self._ranking_errors),
                self._last_run_symbols,
            )
        except Exception:
            pass
        try:
            self._compare_view.set_stock_count(len(universe))
        except Exception:
            pass

    def set_run_state(self, state: str) -> None:
        self._run_state = state
        self._last_batch_pct = -1
        try:
            self.metrics.set_status(state)
        except Exception:
            pass
        self._refresh_staleness()

    def set_batch_progress(self, done: int, total: int) -> None:
        if total <= 0 or done < 0:
            return
        pct = min(100, int(done * 100 / total))
        if pct == self._last_batch_pct:
            return
        self._last_batch_pct = pct
        try:
            self._topbar_status.setText(f"● {done} / {total} · {pct}%")
            self._topbar_status.setVisible(True)
        except Exception:
            pass
        self._stop_armed = True
        try:
            self._topbar_run.setStyleSheet(t.STOP_QSS)
            self._topbar_run.setText("■ STOP")
        except Exception:
            pass
        try:
            self.right_settings.arm_stop()
        except Exception:
            pass

    def _on_ranking_focus(self, symbol: str) -> None:
        if not symbol:
            return
        try:
            if self._view_mode == StrategyViewMode.COMPARE:
                blotter = getattr(self._compare_view, "_trade_blotter", None)
                if blotter is not None and hasattr(blotter, "set_symbol_filter"):
                    blotter.set_symbol_filter(symbol)
                self._on_compare_symbol_filter(symbol)
            else:
                if hasattr(self.journal, "set_symbol_filter"):
                    self.journal.set_symbol_filter(symbol)
                self._on_symbol_filter(symbol)
        except Exception:
            pass

    def _on_symbol_filter(self, symbol: str) -> None:
        if self._view_mode == StrategyViewMode.COMPARE:
            return
        base = self._buy_result if self._view_mode == StrategyViewMode.BUY else self._sell_result
        if base is None:
            base = self._full_result
        if base is None:
            return
        view = base if symbol == "ALL" else self._derive_symbol_view(base, symbol)
        if view is None:
            return
        self.metrics.set_result(view)
        self._equity_view.set_result(view)
        self._drawdown_view.set_result(view)  # type: ignore[attr-defined]
        self._update_equity_summary(view, context=f"{symbol} — EQUITY CURVE")

    def _on_compare_symbol_filter(self, symbol: str) -> None:
        try:
            from backtest import derive_symbol_result
        except Exception:
            return
        buy = self._buy_result
        sell = self._sell_result
        if symbol != "ALL":
            if buy is not None:
                buy = derive_symbol_result(buy, symbol)
            if sell is not None:
                sell = derive_symbol_result(sell, symbol)
        try:
            self._compare_view.set_results(buy, sell, self._full_result)
        except Exception:
            pass

    def _derive_symbol_view(self, base: StrategyResult, symbol: str) -> StrategyResult | None:
        try:
            from backtest import derive_symbol_result
        except Exception:
            return None
        view = derive_symbol_result(base, symbol)
        if view is None:
            return None
        window = self._symbol_windows.get(symbol)
        if window is not None:
            try:
                from backtest.models.result import StrategyResult as _SR

                return _SR(
                    strategy_id=view.strategy_id,
                    name=view.name,
                    config=view.config,
                    trades=view.trades,
                    equity_curve=view.equity_curve,
                    metrics=view.metrics,
                    bars_used=window,
                    period_start=view.period_start,
                    period_end=view.period_end,
                    chart_series=view.chart_series,
                )
            except Exception:
                return view
        return view

    def _refresh_directional_views(self) -> None:
        mode = self._view_mode
        active: StrategyResult | None
        if mode == StrategyViewMode.BUY:
            active = self._buy_result
            if (
                active is None
                and self._buy_result is None
                and self._sell_result is None
                and self._full_result is None
            ):
                pass
        elif mode == StrategyViewMode.SELL:
            active = self._sell_result
        else:
            try:
                self._compare_view.set_results(
                    self._buy_result, self._sell_result, self._full_result
                )
            except Exception:
                pass
            return
        has = active is not None
        self.metrics.set_result(active)
        self._equity_view.set_result(active)
        self._drawdown_view.set_result(active)  # type: ignore[attr-defined]
        self.journal.set_result(active)
        if mode == StrategyViewMode.BUY:
            self.journal.set_side_mode("BUY")
            self.journal.set_side_filter_visible(False)
        elif mode == StrategyViewMode.SELL:
            self.journal.set_side_mode("SELL")
            self.journal.set_side_filter_visible(False)
        try:
            universe = self.right_settings.selected_symbols()
        except Exception:
            universe = ()
        self._update_equity_summary(active, context=self._equity_context(universe))
        if not has:
            if mode == StrategyViewMode.BUY:
                self.journal._placeholder.setText(
                    "BUY BACKTEST NOT RUN\nConfigure your strategy and run the BUY backtest."
                )
            else:
                self.journal._placeholder.setText(
                    "SELL BACKTEST NOT RUN\nConfigure your strategy and run the SELL backtest."
                )

    def _equity_context(self, universe: object) -> str:
        try:
            count = len(tuple(universe or ()))  # type: ignore[arg-type]
        except Exception:
            count = 0
        mode = self._view_mode
        if mode == StrategyViewMode.BUY:
            side = "BUY (LONG)"
        elif mode == StrategyViewMode.SELL:
            side = "SELL (SHORT)"
        else:
            side = "COMBINED"
        noun = "STOCK" if count == 1 else "STOCKS"
        return f"STRATEGY EQUITY CURVE · {count} {noun} · {side}"

    def _update_equity_summary(self, result: StrategyResult | None, context: str = "") -> None:
        points = result.equity_curve if result is not None else ()
        caption = f"{context} — " if context else ""
        if len(points) >= 2:
            start = float(points[0].equity)
            end = float(points[-1].equity)
            ret = (end - start) / start * 100 if start else 0.0
            color = t.POS if ret >= 0 else t.NEG
            dir_tag = (
                "BUY"
                if self._view_mode == StrategyViewMode.BUY
                else "SELL"
                if self._view_mode == StrategyViewMode.SELL
                else ""
            )
            scope = f"{dir_tag} " if dir_tag and not context else ""
            self._equity_summary.setText(
                f"{caption}{scope}START ₹{_inr(start)}   ·   END ₹{_inr(end)}   ·   "
                f"<span style='color:{color};'>RETURN {ret:+.2f}%</span>"
            )
        elif len(points) == 1:
            start = float(points[0].equity)
            self._equity_summary.setText(f"{caption}START ₹{_inr(start)}   ·   No trades yet")
        else:
            if self._view_mode == StrategyViewMode.BUY:
                self._equity_summary.setText("No BUY backtest — run BUY backtest to see equity.")
            elif self._view_mode == StrategyViewMode.SELL:
                self._equity_summary.setText("No SELL backtest — run SELL backtest to see equity.")
            else:
                self._equity_summary.setText("No backtest results yet.")

    def clear(self) -> None:
        self._full_result = None
        self._buy_result = None
        self._sell_result = None
        self._buy_owner = None
        self._sell_owner = None
        self._stale_sides = ()
        self._symbol_windows = {}
        self._ranking_errors = {}
        self._last_run_symbols = None
        try:
            self.right_settings.disarm_stop()
        except Exception:
            pass
        try:
            self.right_settings.set_busy(False)
        except Exception:
            pass
        self.metrics.set_result(None)
        self._equity_view.set_result(None)
        self._drawdown_view.set_result(None)  # type: ignore[attr-defined]
        self.journal.set_result(None)
        try:
            self._compare_view.set_results(None, None)
        except Exception:
            pass
        self._update_equity_summary(None)
        self.set_run_state("ready")
        self._push_ranking()


__all__ = ["StrategyLabWorkspace", "StrategyLibraryPanel", "BacktestRunPanel", "StrategyViewMode"]
