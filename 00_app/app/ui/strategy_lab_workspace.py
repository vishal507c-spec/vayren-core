"""Strategy Lab Workspace — BUY / SELL / COMPARE tri-mode research screen."""

from __future__ import annotations

import contextlib
from datetime import datetime
from enum import StrEnum

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

_DEFAULT_SLIPPAGE_PCT = 0.02
_DEFAULT_COMMISSION_PCT = 0.03

_BUY_ACCENT = "#00C7B7"
_SELL_ACCENT = "#F05A67"


class StrategyViewMode(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    COMPARE = "COMPARE"


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


# ---------------------------------------------------------------------------
# Library row etc. — unchanged from original (preserved for test compat)
# ---------------------------------------------------------------------------
class _StrategyRow(QWidget):
    """One library row: status dot + name · modified · three-dot menu."""

    menu_requested = Signal()

    def __init__(self, name: str, stamp: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 2, 4)
        lay.setSpacing(6)
        dot = QLabel("●", self)
        dot.setStyleSheet(f"color: {t.MUTED}; font-size: 7px;")
        lay.addWidget(dot)
        name_label = QLabel(name, self)
        name_label.setStyleSheet(f"color: {t.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(name_label, 1)
        if stamp:
            time_label = QLabel(stamp, self)
            time_label.setStyleSheet(f"color: {t.MUTED}; font-size: 9px;")
            lay.addWidget(time_label)
        more = QToolButton(self)
        more.setText("⋮")
        more.setFixedSize(18, 18)
        more.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; border-radius: 3px;"
            f" color: {t.MUTED}; font-size: 11px; padding: 0; }}"
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
            f"QLabel {{ color: {t.TEXT}; font-size: 12px; }}"
            f"QPushButton {{ {t.BUTTON_QSS} }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(8)
        head = QLabel("Delete strategy?", self)
        head.setStyleSheet(f"color: {t.TEXT}; font-size: 13px; font-weight: 600;")
        lay.addWidget(head)
        sub = QLabel(f'"{name}" — this action cannot be undone.')
        sub.setObjectName("sub")
        sub.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
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
        self.setMinimumWidth(220)
        self.setMaximumWidth(420)
        self.setStyleSheet(f"background: {t.BG0}; border-right: 1px solid {t.BORDER};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        head = QLabel("STRATEGIES", self)
        head.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.8))
        lay.addWidget(head)
        self._new_btn = QPushButton("+  NEW STRATEGY", self)
        self._new_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {t.BORDER};"
            f" color: {t.TEXT}; padding: 7px; border-radius: 3px; font-size: 11px;"
            f" font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: {t.ACCENT_DIM}; color: {t.ACCENT}; }}"
        )
        self._new_btn.clicked.connect(self.new_strategy_requested.emit)
        lay.addWidget(self._new_btn)
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        icon = QLabel("🔍", self)
        icon.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")
        search_row.addWidget(icon)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search strategies...")
        self._search.setStyleSheet(t.INPUT_QSS)
        self._search.textChanged.connect(self._refilter)
        search_row.addWidget(self._search, 1)
        lay.addLayout(search_row)
        self._list = QListWidget(self)
        self._list.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: none; }"
            f"QListWidget::item {{ border: 1px solid transparent;"
            f" border-radius: 3px; margin: 1px 0; }}"
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
        empty_lay.setContentsMargins(12, 32, 12, 32)
        empty_lay.setSpacing(8)
        empty_lay.addStretch(1)
        icon_label = QLabel("◧", self._empty)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"color: {t.MUTED}; font-size: 28px;")
        empty_lay.addWidget(icon_label)
        msg = QLabel("No strategies yet", self._empty)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(f"color: {t.TEXT}; font-size: 12px; font-weight: 600;")
        empty_lay.addWidget(msg)
        sub = QLabel(
            "Create your first strategy to start\nbuilding, backtesting and analyzing.",
            self._empty,
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        empty_lay.addWidget(sub)
        empty_lay.addSpacing(8)
        empty_btn = QPushButton("+  NEW STRATEGY", self._empty)
        empty_btn.setStyleSheet(
            f"QPushButton {{ background: {t.ACCENT}; border: none; border-radius: 3px;"
            f" color: #04211E; padding: 8px 16px; font-size: 11px; font-weight: 700; }}"
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

    def current_name(self) -> str | None:
        item = self._list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def request_rename(self, name: str) -> None:
        self._ask_rename(name)

    def _refilter(self, text: str) -> None:
        self._filter_text = text
        needle = text.strip().lower()
        self._list.clear()
        for name, mtime in self._items:
            if needle and needle not in name.lower():
                continue
            stamp = _relative_time(mtime) if mtime else ""
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(item)
            row = _StrategyRow(name, stamp, self._list)
            row.menu_requested.connect(lambda n=name: self._row_menu_for(n))
            self._list.setItemWidget(item, row)
            item.setSizeHint(row.sizeHint())
        empty = not self._items
        self._empty.setVisible(empty)
        self._list.setVisible(not empty)
        self._new_btn.setVisible(not empty)
        self._search.setVisible(not empty or bool(needle))

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


class _StatusChip(QLabel):
    """Compact ● STATUS indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("● READY", parent)
        self.set_ready()

    def set_ready(self) -> None:
        self.setText("● READY")
        self.setStyleSheet(f"color: {t.MUTED}; font-size: 10px; font-weight: 600;")

    def set_modified(self) -> None:
        self.setText("● MODIFIED")
        self.setStyleSheet(f"color: {t.WARN}; font-size: 10px; font-weight: 600;")

    def set_error(self) -> None:
        self.setText("● ERROR")
        self.setStyleSheet(f"color: {t.NEG}; font-size: 10px; font-weight: 600;")


class ParamsPane(QWidget):
    """Full-page parameter grid for the compiled strategy inputs."""

    param_changed = Signal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)
        head = QLabel("PARAMETERS", self)
        head.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.8))
        outer.addWidget(head)
        outer.addWidget(_hline())
        self._grid_host = QWidget(self)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 4, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(10)
        outer.addWidget(self._grid_host)
        outer.addStretch(1)
        self._hint = QLabel('No parameters defined — use input(value, "label") in code.', self)
        self._hint.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
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
                key_label.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")
                spin = QDoubleSpinBox(self._grid_host)
                spin.setStyleSheet(t.INPUT_QSS)
                spin.setMinimumWidth(120)
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


class BacktestRunPanel(QWidget):
    """The four run inputs and the primary action — now a tab, not a drawer."""

    run_requested = Signal(object)
    timeframe_changed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(self._lab("SYMBOL"))
        self._symbol_combo = QComboBox(self)
        self._symbol_combo.setEditable(True)
        self._symbol_combo.setStyleSheet(t.INPUT_QSS)
        lay.addWidget(self._symbol_combo)
        lay.addWidget(self._lab("TIMEFRAME"))
        self._tf_combo = QComboBox(self)
        self._tf_combo.setStyleSheet(t.INPUT_QSS)
        self._tf_combo.currentTextChanged.connect(
            lambda tf: self.timeframe_changed.emit(tf) if tf else None
        )
        lay.addWidget(self._tf_combo)
        lay.addWidget(self._lab("DATE RANGE"))
        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        self._from = self._date_edit(QDate(2023, 1, 1))
        self._to = self._date_edit(QDate.currentDate())
        arrow = QLabel("→", self)
        arrow.setStyleSheet(f"color: {t.MUTED};")
        range_row.addWidget(self._from, 1)
        range_row.addWidget(arrow)
        range_row.addWidget(self._to, 1)
        lay.addLayout(range_row)
        lay.addWidget(self._lab("INITIAL CAPITAL"))
        self._capital = QDoubleSpinBox(self)
        self._capital.setRange(10000, 1e9)
        self._capital.setDecimals(0)
        self._capital.setValue(1000000)
        self._capital.setGroupSeparatorShown(True)
        self._capital.setPrefix("₹ ")
        self._capital.setLocale(QLocale(QLocale.Language.English, QLocale.Country.India))
        self._capital.setStyleSheet(t.INPUT_QSS)
        lay.addWidget(self._capital)
        lay.addStretch(1)
        self._error = QLabel("", self)
        self._error.setStyleSheet(f"color: {t.NEG}; font-size: 10px;")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        lay.addWidget(self._error)
        self._run = QPushButton("▶  RUN BACKTEST", self)
        self._run.setStyleSheet(t.PRIMARY_QSS)
        self._run.clicked.connect(self._emit)
        lay.addWidget(self._run)
        self._mode: StrategyViewMode = StrategyViewMode.BUY

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
        label.setStyleSheet(t.label(t.MUTED, 9, 600, 0.6))
        return label

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        current = self._symbol_combo.currentText()
        self._symbol_combo.clear()
        for symbol in symbols:
            self._symbol_combo.addItem(symbol)
        if current:
            idx = self._symbol_combo.findText(current)
            if idx >= 0:
                self._symbol_combo.setCurrentIndex(idx)

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
        return {
            "symbol": self._symbol_combo.currentText().strip(),
            "timeframe": self._tf_combo.currentText().strip(),
            "start_date": self._from.date().toString("yyyy-MM-dd"),
            "end_date": self._to.date().toString("yyyy-MM-dd"),
            "initial_capital": float(self._capital.value()),
            "slippage_pct": _DEFAULT_SLIPPAGE_PCT,
            "commission_pct": _DEFAULT_COMMISSION_PCT,
        }

    def set_view_mode(self, mode: StrategyViewMode) -> None:
        self._mode = mode
        # Update the single RUN button label per mode for COMPARE/BUY/SELL clarity
        if mode == StrategyViewMode.BUY:
            self._run.setText("▶  RUN BUY BACKTEST")
        elif mode == StrategyViewMode.SELL:
            self._run.setText("▶  RUN SELL BACKTEST")
        else:
            self._run.setText("▶  RUN ALL")

    def _emit(self) -> None:
        symbol = self._symbol_combo.currentText().strip()
        if not symbol:
            self._show_error("Select a symbol before running the backtest.")
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
        self._run.setEnabled(not busy)
        if busy:
            self._run.setText("RUNNING BACKTEST…")
            self._hide_error()
        else:
            # restore per-mode label
            self.set_view_mode(self._mode)
        self.busy_changed.emit(busy)


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
        header = QWidget(self)
        header.setObjectName("EditorHeader")
        header.setStyleSheet(
            f"QWidget#EditorHeader {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        header.setFixedHeight(34)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(14, 4, 14, 4)
        h_lay.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(0)
        cap = QLabel("STRATEGY NAME", header)
        cap.setStyleSheet(t.label(t.MUTED, 8, 600, 0.8))
        self._name = QLabel("", header)
        self._name.setStyleSheet(f"color: {t.TEXT}; font-size: 13px; font-weight: 600;")
        title_box.addWidget(cap)
        title_box.addWidget(self._name)
        h_lay.addLayout(title_box)
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
        tab_bar = QWidget(self)
        tab_bar.setObjectName("CenterTabs")
        tab_bar.setStyleSheet(
            f"QWidget#CenterTabs {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        tab_bar.setFixedHeight(30)
        tb_lay = QHBoxLayout(tab_bar)
        tb_lay.setContentsMargins(14, 0, 14, 0)
        tb_lay.setSpacing(2)
        group = QButtonGroup(tab_bar)
        group.setExclusive(True)
        self._code_tab = QPushButton("CODE", tab_bar)
        self._params_tab = QPushButton("PARAMETERS", tab_bar)
        self._backtest_tab = QPushButton("BACKTEST", tab_bar)
        for index, button in enumerate((self._code_tab, self._params_tab, self._backtest_tab)):
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none;"
                f" border-bottom: 2px solid transparent; border-radius: 0;"
                f" padding: 7px 10px 5px; color: {t.TEXT2}; font-size: 10px;"
                f" font-weight: 600; letter-spacing: 0.6px; }}"
                f"QPushButton:hover {{ color: {t.TEXT}; }}"
                f"QPushButton:checked {{ color: {t.TEXT};"
                f" border-bottom: 2px solid {t.ACCENT}; }}"
            )
            group.addButton(button)
        self._code_tab.clicked.connect(lambda: self._switch(0))
        self._params_tab.clicked.connect(lambda: self._switch(1))
        self._backtest_tab.clicked.connect(lambda: self._switch(2))
        tb_lay.addWidget(self._code_tab)
        tb_lay.addWidget(self._params_tab)
        tb_lay.addWidget(self._backtest_tab)
        tb_lay.addStretch(1)
        lay.addWidget(tab_bar)
        self.editor = CodeEditor(self)
        self.params = ParamsPane(self)
        self.params.param_changed.connect(self.param_changed.emit)
        self.backtest_panel = BacktestRunPanel(self)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self.editor)
        self._stack.addWidget(self.params)
        self._stack.addWidget(self.backtest_panel)
        lay.addWidget(self._stack, 1)
        self._status_msg = QLabel("", self)
        self._status_msg.setStyleSheet(
            f"color: {t.MUTED}; font-size: 10px; padding: 3px 14px;"
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
        self._status_msg.setStyleSheet(
            f"color: {color}; font-size: 10px; padding: 3px 14px;"
            f" background: {t.BG0}; border-top: 1px solid {t.BORDER};"
        )
        self._status_msg.setText(f"{prefix} {msg}")
        self._status_msg.setVisible(True)
        if ok:
            self.editor.clear_error()
            if not self._dirty:
                self._status.set_ready()
        else:
            self.editor.set_error(line, col, msg)
            self._status.set_error()


class _ViewModeSelector(QWidget):
    """Segmented control: [ BUY (LONG) ] [ SELL (SHORT) ] [ COMPARE ]."""

    mode_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewModeSelector")
        self.setFixedHeight(44)
        self.setStyleSheet(f"background: {t.BG1}; border-bottom: 1px solid {t.BORDER};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(0)
        # breadcrumb hint
        hint = QLabel("VIEW", self)
        hint.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.6))
        lay.addWidget(hint)
        lay.addSpacing(12)
        # segmented container — prominent pill
        seg = QWidget(self)
        seg.setObjectName("Seg")
        seg.setStyleSheet(
            f"QWidget#Seg {{ background: {t.BG0}; border: 1px solid {t.BORDER}; border-radius: 6px; }}"
        )
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(3)
        group = QButtonGroup(seg)
        group.setExclusive(True)
        self._group = group
        self._buy_btn = self._make_btn("BUY (LONG)", seg, _BUY_ACCENT)
        self._sell_btn = self._make_btn("SELL (SHORT)", seg, _SELL_ACCENT)
        self._cmp_btn = self._make_btn("COMPARE", seg, t.ACCENT)
        for btn in (self._buy_btn, self._sell_btn, self._cmp_btn):
            group.addButton(btn)
            seg_lay.addWidget(btn)
        self._buy_btn.setChecked(True)
        self._buy_btn.clicked.connect(lambda: self._emit(StrategyViewMode.BUY))
        self._sell_btn.clicked.connect(lambda: self._emit(StrategyViewMode.SELL))
        self._cmp_btn.clicked.connect(lambda: self._emit(StrategyViewMode.COMPARE))
        lay.addWidget(seg)
        lay.addStretch(1)
        # direction indicator dot — textual + color
        self._indicator = QLabel("●  BUY  —  LONG", self)
        self._indicator.setStyleSheet(f"color: {_BUY_ACCENT}; font-size: 11px; font-weight: 800;")
        lay.addWidget(self._indicator)
        self._current: StrategyViewMode = StrategyViewMode.BUY
        self._apply_indicator()

    def _make_btn(self, text: str, parent: QWidget, accent: str) -> QPushButton:  # noqa: ARG002
        btn = QPushButton(text, parent)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(28)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: 4px;"
            f" padding: 7px 14px; color: {t.TEXT2}; font-size: 11px; font-weight: 700;"
            f" letter-spacing: 0.3px; }}"
            f"QPushButton:hover {{ color: {t.TEXT}; background: {t.PANEL2}; }}"
            f"QPushButton:checked {{ background: {t.PANEL2}; color: {t.TEXT}; }}"
        )
        return btn

    def _emit(self, mode: StrategyViewMode) -> None:
        self._current = mode
        self._apply_indicator()
        self.mode_changed.emit(mode)

    def _apply_indicator(self) -> None:
        if self._current == StrategyViewMode.BUY:
            self._indicator.setText("●  BUY  —  LONG")
            self._indicator.setStyleSheet(
                f"color: {_BUY_ACCENT}; font-size: 11px; font-weight: 800;"
            )
            self._buy_btn.setStyleSheet(
                f"QPushButton {{ background: {_BUY_ACCENT}; border: none; border-radius: 4px;"
                f" padding: 7px 14px; color: #04211E; font-size: 11px; font-weight: 800; }}"
                f"QPushButton:checked {{ background: {_BUY_ACCENT}; color: #04211E; }}"
            )
            self._sell_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; border-radius: 4px;"
                f" padding: 7px 14px; color: {t.TEXT2}; font-size: 11px; font-weight: 700; }}"
                f"QPushButton:hover {{ color: {t.TEXT}; background: {t.PANEL2}; }}"
                f"QPushButton:checked {{ background: {t.PANEL2}; color: {t.TEXT}; }}"
            )
            self._cmp_btn.setStyleSheet(self._sell_btn.styleSheet())
        elif self._current == StrategyViewMode.SELL:
            self._indicator.setText("●  SELL  —  SHORT")
            self._indicator.setStyleSheet(
                f"color: {_SELL_ACCENT}; font-size: 11px; font-weight: 800;"
            )
            self._sell_btn.setStyleSheet(
                f"QPushButton {{ background: {_SELL_ACCENT}; border: none; border-radius: 4px;"
                f" padding: 7px 14px; color: #FFFFFF; font-size: 11px; font-weight: 800; }}"
                f"QPushButton:checked {{ background: {_SELL_ACCENT}; color: #FFFFFF; }}"
            )
            self._buy_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; border-radius: 4px;"
                f" padding: 7px 14px; color: {t.TEXT2}; font-size: 11px; font-weight: 700; }}"
                f"QPushButton:hover {{ color: {t.TEXT}; background: {t.PANEL2}; }}"
                f"QPushButton:checked {{ background: {t.PANEL2}; color: {t.TEXT}; }}"
            )
            self._cmp_btn.setStyleSheet(self._buy_btn.styleSheet())
        else:
            self._indicator.setText("◐  COMPARE")
            self._indicator.setStyleSheet(f"color: {t.ACCENT}; font-size: 11px; font-weight: 800;")
            self._cmp_btn.setStyleSheet(
                f"QPushButton {{ background: {t.ACCENT}; border: none; border-radius: 4px;"
                f" padding: 7px 14px; color: #04211E; font-size: 11px; font-weight: 800; }}"
                f"QPushButton:checked {{ background: {t.ACCENT}; color: #04211E; }}"
            )
            self._buy_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; border-radius: 4px;"
                f" padding: 7px 14px; color: {t.TEXT2}; font-size: 11px; font-weight: 700; }}"
                f"QPushButton:hover {{ color: {t.TEXT}; background: {t.PANEL2}; }}"
                f"QPushButton:checked {{ background: {t.PANEL2}; color: {t.TEXT}; }}"
            )
            self._sell_btn.setStyleSheet(self._buy_btn.styleSheet())
        self._indicator.setVisible(True)

    @property
    def current_mode(self) -> StrategyViewMode:
        return self._current

    def set_mode(self, mode: StrategyViewMode) -> None:
        if mode == self._current:
            return
        self._current = mode
        # block signals to avoid loop
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


class MetricsTiles(QWidget):
    """Compact metric tiles + secondary statistics strip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1}; border-bottom: 1px solid {t.BORDER};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 8)
        lay.setSpacing(4)
        # directional header — impossible to misunderstand
        self._dir_label = QLabel("BUY PERFORMANCE", self)
        self._dir_label.setStyleSheet(t.label(t.MUTED, 9, 700, 0.7))
        lay.addWidget(self._dir_label)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(2)
        self._vals: dict[str, QLabel] = {}
        keys = (
            "NET PROFIT",
            "TOTAL TRADES",
            "WIN RATE",
            "PROFIT FACTOR",
            "EXPECTANCY",
            "MAX DRAWDOWN",
            "SHARPE",
            "AVG TRADE",
        )
        for col, key in enumerate(keys):
            key_label = QLabel(key, self)
            key_label.setStyleSheet(t.label(t.MUTED, 9, 600, 0.5))
            value = QLabel("--", self)
            value.setStyleSheet(self._value_style(t.TEXT))
            self._vals[key] = value
            grid.addWidget(key_label, 0, col)
            grid.addWidget(value, 1, col)
        lay.addLayout(grid)
        self._extra = QLabel("", self)
        self._extra.setStyleSheet(f"color: {t.MUTED}; font-size: 10px;")
        self._extra.setWordWrap(True)
        lay.addWidget(self._extra)
        self._mode: StrategyViewMode = StrategyViewMode.BUY

    @staticmethod
    def _value_style(color: str) -> str:
        return f"color: {color}; font-size: 15px; font-weight: 700;"

    def set_view_mode(self, mode: StrategyViewMode) -> None:
        self._mode = mode
        if mode == StrategyViewMode.BUY:
            self._dir_label.setText("BUY PERFORMANCE  —  LONG")
            self._dir_label.setStyleSheet(
                f"color: {_BUY_ACCENT}; font-size: 9px; font-weight: 700; letter-spacing: 0.6px;"
            )
        elif mode == StrategyViewMode.SELL:
            self._dir_label.setText("SELL PERFORMANCE  —  SHORT")
            self._dir_label.setStyleSheet(
                f"color: {_SELL_ACCENT}; font-size: 9px; font-weight: 700; letter-spacing: 0.6px;"
            )
        else:
            self._dir_label.setText("PERFORMANCE")
            self._dir_label.setStyleSheet(t.label(t.MUTED, 9, 700, 0.7))

    def set_result(self, result: StrategyResult | None) -> None:
        if result is None:
            for value in self._vals.values():
                value.setText("--")
                value.setStyleSheet(self._value_style(t.TEXT))
            self._extra.setText("")
            return
        metrics = result.metrics
        net_color = t.POS if (metrics.net_profit or 0) >= 0 else t.NEG
        net_text = f"₹{_inr(metrics.net_profit)}" if metrics.total_trades else "--"
        avg_text = f"₹{_inr(metrics.avg_trade)}" if metrics.avg_trade is not None else "--"
        exp_text = f"₹{_inr(metrics.expectancy)}" if metrics.expectancy is not None else "--"
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
            label.setStyleSheet(self._value_style(color))
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
            f" · Avg win ₹{_inr(avg_win)} · Avg loss ₹{_inr(avg_loss)}"
        )


class _DualEquityView(QWidget):
    """Dual equity chart for COMPARE — BUY (teal) + SELL (red) with labels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buy: StrategyResult | None = None
        self._sell: StrategyResult | None = None
        self.setMinimumHeight(160)

    def set_results(self, buy: StrategyResult | None, sell: StrategyResult | None) -> None:
        self._buy = buy
        self._sell = sell
        self.update()

    def set_result(self, result: StrategyResult | None) -> None:  # compat single
        self._buy = result
        self._sell = None
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101418"))
        # legend always visible
        from PySide6.QtCore import Qt as _Qt

        has_buy = self._buy is not None and bool(self._buy.equity_curve)
        has_sell = self._sell is not None and bool(self._sell.equity_curve)
        if not has_buy and not has_sell:
            painter.setPen(QColor("#8a93a6"))
            painter.drawText(
                self.rect(), _Qt.AlignmentFlag.AlignCenter, "No equity data — run a backtest"
            )
            return
        # collect curves
        curves = []  # type: ignore[var-annotated]
        if has_buy:
            curves.append((self._buy.equity_curve, QColor(_BUY_ACCENT), "BUY / LONG"))  # type: ignore[reportOptionalMemberAccess]
        if has_sell:
            curves.append((self._sell.equity_curve, QColor(_SELL_ACCENT), "SELL / SHORT"))  # type: ignore[reportOptionalMemberAccess]
        # global span
        all_eq = []
        for curve, _, _ in curves:
            all_eq.extend(p.equity for p in curve)  # type: ignore[attr-defined]
        lo, hi = min(all_eq), max(all_eq)
        span = hi - lo or 1.0
        pad_l, pad_r, pad_t, pad_b = 48, 12, 20, 18
        plot = self.rect().adjusted(pad_l, pad_t, -pad_r, -pad_b)
        if plot.width() <= 0 or plot.height() <= 0:
            return
        # grid
        painter.setPen(QPen(QColor("#232936"), 1))
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
        # legend
        y_leg = 4
        x_leg = plot.left()
        for _, color, label in curves:
            painter.fillRect(int(x_leg), int(y_leg), 10, 3, color)
            painter.setPen(QColor("#cfd8dc"))
            painter.drawText(int(x_leg + 14), int(y_leg + 8), label)
            x_leg += 110
        # draw curves
        for curve, color, _ in curves:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(color, 1.6))
            pts = []
            for i, p in enumerate(curve):
                x = plot.left() + i / max(1, len(curve) - 1) * plot.width()
                y = plot.bottom() - (p.equity - lo) / span * plot.height()
                from PySide6.QtCore import QPointF as _QPointF

                pts.append(_QPointF(x, y))
            for i in range(len(pts) - 1):
                painter.drawLine(pts[i], pts[i + 1])
        # baseline
        if has_buy and self._buy is not None:
            y0 = plot.bottom() - (self._buy.metrics.starting_capital - lo) / span * plot.height()
            painter.setPen(QPen(QColor("#3b4659"), 1, _Qt.PenStyle.DashLine))
            painter.drawLine(plot.left(), int(y0), plot.right(), int(y0))


class TradeBlotter(QWidget):
    """Dense trade table with filter and CSV export. Supports directional filtering.

    Enhancements for instant trade→chart (spec §5, §28, §29):
    - Single-click row → trade_clicked (no confirmation)
    - Keyboard: ↑/↓ to step, Enter to re-focus, Esc to clear filter focus
    - Hover tooltip preview (tiny trade summary, no chart init)
    - Professional selected-row highlight (thin accent, not heavy fill)
    """

    trade_clicked = Signal(int)
    trade_hovered = Signal(int)  # optional preview hook

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 8, 14, 8)
        bar.setSpacing(6)
        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter trades")
        self._filter.setStyleSheet(t.INPUT_QSS)
        self._filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self._filter, 1)
        # COMPARE filter: ALL / BUY / SELL — visible only in COMPARE
        self._side_filter = QComboBox(self)
        self._side_filter.addItems(["ALL", "BUY", "SELL"])
        self._side_filter.setStyleSheet(t.INPUT_QSS)
        self._side_filter.setFixedWidth(90)
        self._side_filter.setVisible(False)
        self._side_filter.currentTextChanged.connect(lambda _: self._apply_filter(""))
        bar.addWidget(self._side_filter)
        export_btn = QPushButton("EXPORT CSV", self)
        export_btn.setStyleSheet(t.BUTTON_QSS)
        export_btn.clicked.connect(self._export_csv)
        bar.addWidget(export_btn)
        lay.addLayout(bar)
        self._placeholder = QLabel("Run a backtest to see trades.", self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(self._placeholder, 1)
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
        # keyboard navigation is context-aware (§28) — only active when blotter has focus or when chart panel not interfering
        self._table.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        lay.addWidget(self._table, 1)
        self._trades: list = []
        self._side_mode: str = "ALL"  # ALL / LONG / SHORT
        self._needle: str = ""
        self._selected_index: int | None = None
        # enable Arrow/Page navigation handling (table + viewport both route keys here)
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
        # When user explicitly changes combo, update side mode
        # connect handler does _apply_filter; ensure side_mode sync
        cur = self._side_filter.currentText().upper()
        if cur == "BUY":
            self._side_mode = "LONG"
        elif cur == "SELL":
            self._side_mode = "SHORT"
        else:
            if self._side_filter.isVisible():
                # user-driven change — already set via lambda, need sync
                pass

    def set_result(self, result: StrategyResult | None) -> None:
        has = result is not None and bool(result.trades)
        self._placeholder.setVisible(not has)
        self._table.setVisible(has)
        self._table.setRowCount(0)
        self._trades = list(result.trades) if result else []
        self._selected_index = None
        self._table.clearSelection()
        if not has:
            return
        assert result is not None
        self._table.setRowCount(len(result.trades))
        for i, trade in enumerate(result.trades):
            values = [
                str(i + 1),
                trade.symbol,
                trade.side,
                trade.entry_time[:16],
                f"{trade.entry_price:.2f}",
                trade.exit_time[:16],
                f"{trade.exit_price:.2f}",
                f"{trade.pnl:+,.2f}",
                f"{trade.r_multiple:.2f}" if trade.r_multiple is not None else "--",
                str(trade.bars_held),
                trade.exit_reason,
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, i)
                if col == 7:
                    from PySide6.QtGui import QColor

                    item.setForeground(QColor(t.POS if trade.winning else t.NEG))
                if col == 2:
                    # Direction column textual identity — not color alone
                    from PySide6.QtGui import QColor

                    if trade.side == "LONG":
                        item.setForeground(QColor(_BUY_ACCENT))
                    elif trade.side == "SHORT":
                        item.setForeground(QColor(_SELL_ACCENT))
                self._table.setItem(i, col, item)
        self._apply_filter(self._needle)

    # ── instant trade selection helpers ───────────────────────

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    def set_selected_index(self, index: int | None) -> None:
        """Highlight the given trade row (0-based) with subtle selected state."""
        self._selected_index = index
        if index is None:
            self._table.clearSelection()
            return
        # find row that holds that UserRole index (table may be filtered)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole)) == index:
                self._table.selectRow(row)
                self._table.scrollToItem(item)
                # tooltip style selected remains via QSS accent border
                return

    def _on_cell(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item is not None:
            idx = int(item.data(Qt.ItemDataRole.UserRole))
            self._selected_index = idx
            # subtle professional selected state via selectRow
            self._table.selectRow(row)
            self.trade_clicked.emit(idx)

    def _on_hover(self, row: int, _col: int) -> None:
        """Hover preview (§30): tiny tooltip with trade summary, no chart init."""
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
                # do not emit hover as chart overlay; panel preview optional cheap
                # we still emit trade_hovered for future extensions but not used for chart
                with contextlib.suppress(Exception):
                    self.trade_hovered.emit(idx)
        except Exception:
            pass

    def _step_selection(self, step: int) -> bool:
        """Move selection by `step` rows (skipping hidden), emit trade_clicked."""
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
            # Arrow navigation — rapid trade switching (§23, §28)
            if key == 16777235:  # Qt.Key_Up
                return self._step_selection(-1)
            if key == 16777237:  # Qt.Key_Down
                return self._step_selection(1)
            if key == 16777220:  # Enter
                if self._selected_index is not None:
                    self.trade_clicked.emit(self._selected_index)
                    return True
            elif key == 16777216:  # Escape
                # return focus to list filter / clear
                with contextlib.suppress(Exception):
                    self._filter.clearFocus()
                    self._table.clearFocus()
                    self._table.clearSelection()
                return False
        return super().eventFilter(obj, event)

    def _next_visible(self, start: int, step: int) -> int | None:
        """Walk in direction step until a non-hidden row is found."""
        idx = start
        while 0 <= idx < len(self._trades):
            # find table row for this trade index
            for row in range(self._table.rowCount()):
                it = self._table.item(row, 0)
                if it is not None and int(it.data(Qt.ItemDataRole.UserRole)) == idx:
                    if not self._table.isRowHidden(row):
                        return idx
                    break
            idx += step
        return None

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        # container-level arrows when focus is on blotter itself
        if event.key() in (16777235, 16777237, 16777220, 16777216) and self.eventFilter(
            self._table, event
        ):
            event.accept()
            return
        super().keyPressEvent(event)

    def _apply_filter(self, text: str) -> None:
        # track needle
        if text is not None:
            # called via textChanged with actual needle, or via side change with ""
            # We need to distinguish: if text == "" from side combo we keep previous needle
            # Heuristic: if sender is filter line edit, update needle; if combo, keep.
            sender = self.sender()
            if sender is self._filter or text != "":
                self._needle = text.strip().lower() if isinstance(text, str) else ""
            # for combo change we pass "" but should retain needle
            if sender is self._side_filter:
                # keep existing needle
                pass
            elif isinstance(text, str) and text != "" or sender is self._filter:
                self._needle = text.strip().lower() if isinstance(text, str) else ""
        needle = self._needle
        # need side mode from combo if visible (user may have changed it)
        if self._side_filter.isVisible():
            cur = self._side_filter.currentText().upper()
            if cur == "BUY":
                self._side_mode = "LONG"
            elif cur == "SELL":
                self._side_mode = "SHORT"
            else:
                self._side_mode = "ALL"
        for row in range(self._table.rowCount()):
            # side filter first
            if self._side_mode != "ALL":
                side_item = self._table.item(row, 2)
                side = side_item.text().upper() if side_item else ""
                # table stores LONG/SHORT
                if side != self._side_mode:
                    self._table.setRowHidden(row, True)
                    continue
            if not needle:
                self._table.setRowHidden(row, False)
                continue
            match = False
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None and needle in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match)

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


# ---------------------------------------------------------------------------
# Comparison helpers — metrics direction, stronger side, highlighting
# ---------------------------------------------------------------------------
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
        "Max Drawdown": m.max_drawdown_pct,  # stored positive, lower is better
        "Sharpe": m.sharpe_ratio,
        "Avg Trade": m.avg_trade,
    }


def _is_higher_better(key: str) -> bool:
    # Lower absolute drawdown is better (smaller number)
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
    """Return (verdict, reason).

    Verdict is one of "BUY / LONG", "SELL / SHORT", "TOO CLOSE TO CALL", "INSUFFICIENT".
    Reason is a short human explanation for the banner subtitle.
    """
    if buy is None or sell is None:
        return "INSUFFICIENT", "Run both sides to compare"
    # Need at least one trade on each side to declare a winner — else insufficient
    if buy.metrics.total_trades == 0 or sell.metrics.total_trades == 0:
        # If one side has zero trades and other has trades, the traded side is clearly active
        # But we still declare INSUFFICIENT per spec's "Not backtested yet" — avoid fake winner
        # We only declare when both have at least 1 trade
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
        # consider tie tolerance for near-equal values
        if key == "Max Drawdown":
            # lower is better
            if abs(bv - sv) < 0.05:  # tie within 0.05%
                continue
            if bv < sv:
                buy_wins += 1
                reasons_buy.append(f"lower drawdown ({bv:.2f}% vs {sv:.2f}%)")
            elif sv < bv:
                sell_wins += 1
                reasons_sell.append(f"lower drawdown ({sv:.2f}% vs {bv:.2f}%)")
        else:
            # higher better; need tolerance
            if key in ("Profit Factor", "Sharpe"):
                thresh = 0.03
            elif key == "Win Rate":
                thresh = 0.02
            elif key in ("Net Profit", "Expectancy", "Avg Trade"):
                # relative tolerance 1%
                base = max(abs(bv), abs(sv), 1.0)
                if abs(bv - sv) / base < 0.01:
                    continue
                thresh = 0.0  # already handled via relative
            else:
                thresh = 0.0
                if abs(bv - sv) <= thresh:
                    continue
            if key in ("Net Profit", "Expectancy", "Avg Trade") and thresh == 0.0:
                # already checked relative
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
    # Require clear majority: at least 2 more wins than opponent, or 60% majority
    if diff >= 2 or (total >= 3 and max(buy_wins, sell_wins) / total >= 0.6):
        if buy_wins > sell_wins:
            reason = ", ".join(reasons_buy[:3]) or "more winning metrics"
            return "BUY / LONG", reason
        else:
            reason = ", ".join(reasons_sell[:3]) or "more winning metrics"
            return "SELL / SHORT", reason
    return "TOO CLOSE TO CALL", "No side dominates across key metrics"


class _ComparisonMatrix(QWidget):
    """Grid: Metric | BUY | SELL with winner highlighting per metric direction."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        title = QLabel("PERFORMANCE COMPARISON", self)
        title.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.8))
        lay.addWidget(title)
        lay.addWidget(_hline())
        # header row
        header = QGridLayout()
        header.setContentsMargins(0, 4, 0, 0)
        header.setHorizontalSpacing(16)
        header.setVerticalSpacing(4)
        for col, txt in enumerate(("", "BUY (LONG)", "SELL (SHORT)")):
            lbl = QLabel(txt, self)
            if col == 0:
                lbl.setStyleSheet(t.label(t.MUTED, 9, 600, 0.6))
            elif col == 1:
                lbl.setStyleSheet(f"color: {_BUY_ACCENT}; font-size: 9px; font-weight: 700;")
            else:
                lbl.setStyleSheet(f"color: {_SELL_ACCENT}; font-size: 9px; font-weight: 700;")
            header.addWidget(lbl, 0, col)
        lay.addLayout(header)
        # rows
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 2, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(6)
        self._buy_labels: dict[str, QLabel] = {}
        self._sell_labels: dict[str, QLabel] = {}
        self._metric_labels: dict[str, QLabel] = {}
        for row, key in enumerate(_METRIC_ORDER):
            m_lbl = QLabel(key, self)
            m_lbl.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px; font-weight: 600;")
            self._metric_labels[key] = m_lbl
            buy_lbl = QLabel("--", self)
            buy_lbl.setStyleSheet(
                f"color: {t.TEXT}; font-size: 11px; font-weight: 600; background: transparent; padding: 2px 6px; border-radius: 3px;"
            )
            buy_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sell_lbl = QLabel("--", self)
            sell_lbl.setStyleSheet(
                f"color: {t.TEXT}; font-size: 11px; font-weight: 600; background: transparent; padding: 2px 6px; border-radius: 3px;"
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
            # reset styles
            buy_lbl.setStyleSheet(
                f"color: {t.TEXT}; font-size: 11px; font-weight: 600; background: transparent; padding: 2px 6px; border-radius: 3px;"
            )
            sell_lbl.setStyleSheet(
                f"color: {t.TEXT}; font-size: 11px; font-weight: 600; background: transparent; padding: 2px 6px; border-radius: 3px;"
            )
            if bv is None or sv is None:
                continue
            # Determine per-row winner for highlighting (higher better except drawdown)
            winner = None
            if key == "Max Drawdown":
                if abs(bv - sv) < 0.05:
                    continue
                winner = "BUY" if bv < sv else "SELL"
            else:
                # handle thresholds
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
                    f"color: #04211E; font-size: 11px; font-weight: 700; background: {_BUY_ACCENT}; padding: 2px 6px; border-radius: 3px;"
                )
            elif winner == "SELL":
                sell_lbl.setStyleSheet(
                    f"color: #FFFFFF; font-size: 11px; font-weight: 700; background: {_SELL_ACCENT}; padding: 2px 6px; border-radius: 3px;"
                )


class _StrongerBanner(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: 4px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        self._kicker = QLabel("STRONGER SIDE", self)
        self._kicker.setStyleSheet(t.label(t.MUTED, 9, 700, 0.6))
        self._kicker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._kicker)
        self._verdict = QLabel("TOO CLOSE TO CALL", self)
        self._verdict.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._verdict.setStyleSheet(f"color: {t.TEXT}; font-size: 18px; font-weight: 800;")
        lay.addWidget(self._verdict)
        self._reason = QLabel("", self)
        self._reason.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet(f"color: {t.TEXT2}; font-size: 10px;")
        lay.addWidget(self._reason)

    def set_verdict(self, verdict: str, reason: str) -> None:
        self._verdict.setText(verdict)
        self._reason.setText(reason)
        if verdict == "BUY / LONG":
            self._verdict.setStyleSheet(f"color: {_BUY_ACCENT}; font-size: 18px; font-weight: 800;")
            self.setStyleSheet(
                f"background: {t.PANEL}; border: 1px solid {_BUY_ACCENT}; border-radius: 4px;"
            )
        elif verdict == "SELL / SHORT":
            self._verdict.setStyleSheet(
                f"color: {_SELL_ACCENT}; font-size: 18px; font-weight: 800;"
            )
            self.setStyleSheet(
                f"background: {t.PANEL}; border: 1px solid {_SELL_ACCENT}; border-radius: 4px;"
            )
        elif verdict == "INSUFFICIENT":
            self._verdict.setStyleSheet(f"color: {t.MUTED}; font-size: 14px; font-weight: 700;")
            self.setStyleSheet(
                f"background: {t.BG1}; border: 1px dashed {t.BORDER}; border-radius: 4px;"
            )
        else:
            self._verdict.setStyleSheet(f"color: {t.TEXT}; font-size: 16px; font-weight: 700;")
            self.setStyleSheet(
                f"background: {t.PANEL}; border: 1px solid {t.BORDER}; border-radius: 4px;"
            )


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
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(14)
        # Header
        hdr = QLabel("STRATEGY COMPARISON", content)
        hdr.setStyleSheet(t.label(t.TEXT, 11, 800, 0.8))
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hdr)
        vs_row = QHBoxLayout()
        vs_row.setSpacing(12)
        self._buy_hdr = QLabel("BUY (LONG)", content)
        self._buy_hdr.setStyleSheet(f"color: {_BUY_ACCENT}; font-size: 11px; font-weight: 700;")
        self._buy_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vs = QLabel("VS", content)
        vs.setStyleSheet(t.label(t.MUTED, 10, 700, 0.6))
        vs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sell_hdr = QLabel("SELL (SHORT)", content)
        self._sell_hdr.setStyleSheet(f"color: {_SELL_ACCENT}; font-size: 11px; font-weight: 700;")
        self._sell_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vs_row.addWidget(self._buy_hdr, 1)
        vs_row.addWidget(vs)
        vs_row.addWidget(self._sell_hdr, 1)
        lay.addLayout(vs_row)
        lay.addWidget(_hline())
        # Stronger side banner
        self._banner = _StrongerBanner(content)
        lay.addWidget(self._banner)
        # Performance matrix
        self._matrix = _ComparisonMatrix(content)
        lay.addWidget(self._matrix)
        # Equity comparison
        eq_title = QLabel("EQUITY COMPARISON", content)
        eq_title.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.8))
        lay.addWidget(eq_title)
        self._equity = _DualEquityView(content)
        self._equity.setMinimumHeight(180)
        lay.addWidget(self._equity)
        # Risk strip (max drawdown already in matrix, add simple risk row)
        risk_title = QLabel("RISK COMPARISON", content)
        risk_title.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.8))
        lay.addWidget(risk_title)
        self._risk_grid = QWidget(content)
        self._risk_grid.setStyleSheet(
            f"background: {t.BG1}; border: 1px solid {t.BORDER}; border-radius: 4px;"
        )
        rg_lay = QGridLayout(self._risk_grid)
        rg_lay.setContentsMargins(10, 8, 10, 8)
        rg_lay.setHorizontalSpacing(16)
        rg_lay.setVerticalSpacing(4)
        self._risk_buy_dd = QLabel("--", self._risk_grid)
        self._risk_sell_dd = QLabel("--", self._risk_grid)
        for col, txt in enumerate(("METRIC", "BUY", "SELL")):
            lbl = QLabel(txt, self._risk_grid)
            lbl.setStyleSheet(t.label(t.MUTED, 9, 600, 0.6))
            rg_lay.addWidget(lbl, 0, col)
        rg_lay.addWidget(QLabel("Max Drawdown", self._risk_grid), 1, 0)
        rg_lay.addWidget(self._risk_buy_dd, 1, 1)
        rg_lay.addWidget(self._risk_sell_dd, 1, 2)
        self._risk_buy_sh = QLabel("--", self._risk_grid)
        self._risk_sell_sh = QLabel("--", self._risk_grid)
        rg_lay.addWidget(QLabel("Sharpe", self._risk_grid), 2, 0)
        rg_lay.addWidget(self._risk_buy_sh, 2, 1)
        rg_lay.addWidget(self._risk_sell_sh, 2, 2)
        lay.addWidget(self._risk_grid)
        # Trade comparison strip
        tr_title = QLabel("TRADE COMPARISON", content)
        tr_title.setStyleSheet(t.label(t.TEXT2, 10, 700, 0.8))
        lay.addWidget(tr_title)
        self._trade_summary = QLabel("", content)
        self._trade_summary.setStyleSheet(
            f"color: {t.MUTED}; font-size: 10px; background: {t.BG1}; padding: 6px 10px; border: 1px solid {t.BORDER}; border-radius: 3px;"
        )
        self._trade_summary.setWordWrap(True)
        lay.addWidget(self._trade_summary)
        self._trade_blotter = TradeBlotter(content)
        self._trade_blotter.setMinimumHeight(180)
        self._trade_blotter.set_side_filter_visible(True)
        self._trade_blotter.set_side_mode("ALL")
        lay.addWidget(self._trade_blotter)
        # Empty / missing banners
        self._empty_banner = QLabel("", content)
        self._empty_banner.setStyleSheet(
            f"color: {t.MUTED}; font-size: 11px; background: {t.BG1}; border: 1px dashed {t.BORDER}; padding: 12px; border-radius: 4px;"
        )
        self._empty_banner.setWordWrap(True)
        self._empty_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_banner.setVisible(False)
        lay.addWidget(self._empty_banner)
        self._actions = QWidget(content)
        act_lay = QHBoxLayout(self._actions)
        act_lay.setContentsMargins(0, 0, 0, 0)
        act_lay.setSpacing(8)
        self._run_buy = QPushButton("RUN BUY BACKTEST", self._actions)
        self._run_buy.setStyleSheet(
            f"QPushButton {{ background: {_BUY_ACCENT}; border: none; border-radius: 3px; padding: 6px 14px; color: #04211E; font-size: 10px; font-weight: 700;}} QPushButton:hover {{ background: {t.ACCENT_DIM};}}"
        )
        self._run_sell = QPushButton("RUN SELL BACKTEST", self._actions)
        self._run_sell.setStyleSheet(
            f"QPushButton {{ background: {_SELL_ACCENT}; border: none; border-radius: 3px; padding: 6px 14px; color: #FFF; font-size: 10px; font-weight: 700;}} QPushButton:hover {{ background: #D64552;}}"
        )
        self._run_all = QPushButton("RUN ALL", self._actions)
        self._run_all.setStyleSheet(t.PRIMARY_QSS)
        self._run_buy.clicked.connect(self.run_buy_requested.emit)
        self._run_sell.clicked.connect(self.run_sell_requested.emit)
        self._run_all.clicked.connect(self.run_all_requested.emit)
        act_lay.addWidget(self._run_buy)
        act_lay.addWidget(self._run_sell)
        act_lay.addWidget(self._run_all)
        act_lay.addStretch(1)
        self._actions.setVisible(False)
        lay.addWidget(self._actions)
        lay.addStretch(1)
        content.setLayout(lay)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self._buy: StrategyResult | None = None
        self._sell: StrategyResult | None = None

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
        # risk grid
        if buy is not None:
            self._risk_buy_dd.setText(f"-{buy.metrics.max_drawdown_pct:.2f}%")
            self._risk_buy_sh.setText(
                f"{buy.metrics.sharpe_ratio:.2f}" if buy.metrics.sharpe_ratio is not None else "--"
            )
        else:
            self._risk_buy_dd.setText("--")
            self._risk_buy_sh.setText("--")
        if sell is not None:
            self._risk_sell_dd.setText(f"-{sell.metrics.max_drawdown_pct:.2f}%")
            self._risk_sell_sh.setText(
                f"{sell.metrics.sharpe_ratio:.2f}"
                if sell.metrics.sharpe_ratio is not None
                else "--"
            )
        else:
            self._risk_sell_dd.setText("--")
            self._risk_sell_sh.setText("--")
        # verdict
        verdict, reason = determine_stronger_side(buy, sell)
        # Map INSUFFICIENT to user-friendly
        if verdict == "INSUFFICIENT":
            if buy is None and sell is None:
                self._banner.set_verdict(
                    "NOTHING TO COMPARE YET", "Run BUY and SELL backtests to compare performance."
                )
            elif buy is None or (buy is not None and buy.metrics.total_trades == 0):
                # sell available but buy missing — keep sell verdict? Show insufficient banner but not claim stronger
                self._banner.set_verdict("INSUFFICIENT — SELL AVAILABLE", reason)
            elif sell is None or (sell is not None and sell.metrics.total_trades == 0):
                self._banner.set_verdict("INSUFFICIENT — BUY AVAILABLE", reason)
            else:
                self._banner.set_verdict(verdict, reason)
        else:
            self._banner.set_verdict(verdict, reason)
        # trade summary
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
        # empty / action banner for partial data
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
            # buy missing or empty
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
            # In full compare, hide the per-side run buttons, keep RUN ALL
            self._actions.setVisible(True)
            # show all three but RUN ALL is primary
            self._run_buy.setVisible(True)
            self._run_sell.setVisible(True)
            self._run_all.setVisible(True)
        # Adjust actions: when both missing, emphasize RUN ALL; when one missing, show its run
        if not has_buy and not has_sell:
            self._run_all.setText("RUN ALL")
        elif not has_buy:
            self._run_buy.setVisible(True)
        elif not has_sell:
            self._run_sell.setVisible(True)
        # Trade blotter — show combined trades for ALL / BUY / SELL filtering
        try:
            combined: StrategyResult | None = None
            if full is not None:
                combined = full
            elif buy is not None and sell is not None:
                # Merge buy + sell trades chronologically by entry_index
                merged = tuple(
                    sorted(
                        (*buy.trades, *sell.trades), key=lambda tr: getattr(tr, "entry_index", 0)
                    )
                )
                # Use buy's config/metrics as placeholder, but blotter only uses trades
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


class StrategyLabWorkspace(QWidget):
    """Two-column lab with BUY / SELL / COMPARE tri-mode results."""

    run_backtest = Signal(object)
    trade_focus = Signal(int)
    param_changed = Signal(str, float)
    save_requested_relay = Signal(str)
    compile_requested_relay = Signal(str)

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_topbar())
        self._root = QSplitter(Qt.Orientation.Vertical, self)
        self._root.setStyleSheet(t.SPLITTER_QSS)
        self._root.setHandleWidth(1)
        self._root.setChildrenCollapsible(False)
        self._main = QSplitter(Qt.Orientation.Horizontal, self)
        self._main.setStyleSheet(t.SPLITTER_QSS)
        self._main.setHandleWidth(1)
        self._main.setChildrenCollapsible(False)
        self.left_nav = StrategyLibraryPanel(self)
        self.center_detail = EditorPane(self)
        # Prominent BUY/SELL/COMPARE selector — inside BACKTEST workspace, directly below CODE|PARAMETERS|BACKTEST
        self._mode_selector = _ViewModeSelector(self.center_detail)
        self._mode_selector.mode_changed.connect(self.set_view_mode)
        # EditorPane layout: header(0), tab_bar(1), _stack(2), _status_msg(3) → insert selector at 2
        self.center_detail.layout().insertWidget(2, self._mode_selector)  # type: ignore[attr-defined]
        self.center_detail.save_requested.connect(self.save_requested_relay)
        self.center_detail.compile_requested.connect(self.compile_requested_relay)
        self.center_detail.tab_changed.connect(self._crumb_name.setText)
        self.center_detail.rename_affordance.connect(
            lambda: self.left_nav.request_rename(self.current_tab_name())
        )
        self.center_detail.param_changed.connect(self.param_changed.emit)
        self.right_settings = self.center_detail.backtest_panel
        self.right_settings.run_requested.connect(self._on_panel_run)
        self.right_settings.busy_changed.connect(self._set_run_busy)
        self._center_empty = self._build_center_empty()
        self._center_stack = QStackedWidget(self)
        self._center_stack.addWidget(self._center_empty)
        self._center_stack.addWidget(self.center_detail)
        self._center_stack.setCurrentIndex(0)
        self._main.addWidget(self.left_nav)
        self._main.addWidget(self._center_stack)
        self._main.setStretchFactor(0, 0)
        self._main.setStretchFactor(1, 1)
        self._build_results()
        assert isinstance(self._result_stack, QStackedWidget)
        assert isinstance(self._collapse_btn, QToolButton)
        results_dock = self._result_stack.parentWidget()
        assert results_dock is not None
        self._results_dock = results_dock  # keep reference for visibility toggling
        self._root.addWidget(self._main)
        self._root.addWidget(results_dock)
        self._root.setStretchFactor(0, 1)
        self._root.setStretchFactor(1, 0)
        # Compare view — stacked outside _result_stack so test count==4 preserved
        self._compare_view = _CompareView(self)
        self._compare_view.run_all_requested.connect(self._emit_run_all)
        self._compare_view.run_buy_requested.connect(self._emit_run_buy)
        self._compare_view.run_sell_requested.connect(self._emit_run_sell)
        self._compare_view.setVisible(False)
        # _results_container kept for backward compat (some tests check attribute existence)
        self._results_container = self._compare_view  # type: ignore[assignment]
        outer.addWidget(self._root, 1)
        outer.addWidget(self._compare_view, 1)
        self._setup_shortcuts()
        self._main.setSizes([280, 880])
        self._apply_view_mode()

    def _on_panel_run(self, cfg: object) -> None:
        # Track which side was requested so set_result can retain the other side's state
        if self._view_mode == StrategyViewMode.BUY:
            self._pending_side = StrategyViewMode.BUY
        elif self._view_mode == StrategyViewMode.SELL:
            self._pending_side = StrategyViewMode.SELL
        else:
            self._pending_side = StrategyViewMode.COMPARE
        self.run_backtest.emit(cfg)

    def _emit_run_buy(self) -> None:
        self.set_view_mode(StrategyViewMode.BUY)
        self._pending_side = StrategyViewMode.BUY
        self.run_backtest.emit(self.right_settings.current_config())

    def _emit_run_sell(self) -> None:
        self.set_view_mode(StrategyViewMode.SELL)
        self._pending_side = StrategyViewMode.SELL
        self.run_backtest.emit(self.right_settings.current_config())

    def _emit_run_all(self) -> None:
        self._pending_side = StrategyViewMode.COMPARE
        self.run_backtest.emit(self.right_settings.current_config())

    def _build_center_empty(self) -> QWidget:
        widget = QWidget(self)
        widget.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(32, 48, 32, 48)
        lay.setSpacing(12)
        lay.addStretch(1)
        icon = QLabel("◧", widget)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color: {t.MUTED}; font-size: 36px;")
        lay.addWidget(icon)
        title = QLabel("No strategy selected", widget)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {t.TEXT}; font-size: 14px; font-weight: 600;")
        lay.addWidget(title)
        sub = QLabel(
            "Select an existing strategy from the list or\ncreate a new strategy to get started.",
            widget,
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(sub)
        lay.addSpacing(8)
        button = QPushButton("+  NEW STRATEGY", widget)
        button.setStyleSheet(
            f"QPushButton {{ background: {t.ACCENT}; border: none; border-radius: 3px;"
            f" color: #04211E; padding: 8px 18px; font-size: 11px; font-weight: 700; }}"
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

    def _build_topbar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("LabTopBar")
        bar.setFixedHeight(38)
        bar.setStyleSheet(
            f"QWidget#LabTopBar {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 5, 10, 5)
        lay.setSpacing(8)
        brand = QLabel("VAYREN", bar)
        brand.setStyleSheet(
            f"color: {t.ACCENT}; font-size: 12px; font-weight: 800; letter-spacing: 1.2px;"
        )
        lay.addWidget(brand)
        crumb = QLabel("/  STRATEGY LAB  /", bar)
        crumb.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(crumb)
        self._crumb_name = QLabel("", bar)
        self._crumb_name.setStyleSheet(f"color: {t.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(self._crumb_name)
        lay.addStretch(1)
        save_btn = QPushButton("SAVE", bar)
        save_btn.setStyleSheet(t.BUTTON_QSS)
        save_btn.clicked.connect(
            lambda: self.save_requested_relay.emit(self.center_detail.get_code())
        )
        compile_btn = QPushButton("COMPILE", bar)
        compile_btn.setStyleSheet(t.BUTTON_QSS)
        compile_btn.clicked.connect(
            lambda: self.compile_requested_relay.emit(self.center_detail.get_code())
        )
        self._topbar_run = QPushButton("▶  RUN BUY BACKTEST", bar)
        self._topbar_run.setStyleSheet(t.PRIMARY_QSS)
        self._topbar_run.clicked.connect(self._emit_run)
        lay.addWidget(save_btn)
        lay.addWidget(compile_btn)
        lay.addWidget(self._topbar_run)
        return bar

    def _build_results(self) -> None:
        dock = QWidget(self)
        dock.setStyleSheet(f"background: {t.BG1}; border-top: 1px solid {t.BORDER};")
        lay = QVBoxLayout(dock)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        strip = QWidget(dock)
        strip.setObjectName("ResultStrip")
        strip.setStyleSheet(
            f"QWidget#ResultStrip {{ background: {t.BG0}; border-bottom: 1px solid {t.BORDER}; }}"
        )
        strip.setFixedHeight(30)
        strip_lay = QHBoxLayout(strip)
        strip_lay.setContentsMargins(14, 0, 10, 0)
        strip_lay.setSpacing(2)
        group = QButtonGroup(strip)
        group.setExclusive(True)
        stack = QStackedWidget(dock)
        from backtest.ui.analytics_views import DrawdownView, EquityCurveView

        self.metrics = MetricsTiles(dock)
        perf_page = QWidget(dock)
        perf_lay = QVBoxLayout(perf_page)
        perf_lay.setContentsMargins(0, 0, 0, 0)
        perf_lay.setSpacing(0)
        perf_lay.addWidget(self.metrics)
        perf_lay.addWidget(EquityCurveView(perf_page), 1)
        # keep reference to perf equity for backward compat updates
        self._perf_equity = perf_lay.itemAt(1).widget()  # type: ignore[assignment]
        stack.addWidget(perf_page)
        self.journal = TradeBlotter(dock)
        self.journal.trade_clicked.connect(self.trade_focus.emit)
        stack.addWidget(self.journal)
        equity_page = QWidget(dock)
        equity_lay = QVBoxLayout(equity_page)
        equity_lay.setContentsMargins(0, 0, 0, 0)
        equity_lay.setSpacing(0)
        self._equity_summary = QLabel("", equity_page)
        self._equity_summary.setStyleSheet(
            f"color: {t.TEXT2}; font-size: 10px; padding: 6px 14px;"
            f" background: {t.BG0}; border-bottom: 1px solid {t.BORDER};"
        )
        equity_lay.addWidget(self._equity_summary)
        self._equity_view = EquityCurveView(equity_page)
        equity_lay.addWidget(self._equity_view, 1)
        stack.addWidget(equity_page)
        self._drawdown_view = DrawdownView(dock)
        stack.addWidget(self._drawdown_view)
        for index, text in enumerate(("PERFORMANCE", "TRADES", "EQUITY", "DRAWDOWN")):
            button = QPushButton(text, strip)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none;"
                f" border-bottom: 2px solid transparent; border-radius: 0;"
                f" padding: 6px 10px 4px; color: {t.TEXT2}; font-size: 10px;"
                f" font-weight: 600; letter-spacing: 0.6px; }}"
                f"QPushButton:hover {{ color: {t.TEXT}; }}"
                f"QPushButton:checked {{ color: {t.TEXT};"
                f" border-bottom: 2px solid {t.ACCENT}; }}"
            )
            button.clicked.connect(lambda _c=False, i=index: self.show_result(i, expand=True))
            group.addButton(button)
            strip_lay.addWidget(button)
        strip_lay.addStretch(1)
        collapse = QToolButton(strip)
        collapse.setText("▴")
        collapse.setToolTip("Expand / collapse results")
        collapse.setStyleSheet(t.TOOL_QSS)
        collapse.clicked.connect(lambda: self.toggle_results())
        strip_lay.addWidget(collapse)
        lay.addWidget(strip)
        lay.addWidget(stack, 1)
        stack.setVisible(False)
        self._result_stack = stack
        self._collapse_btn = collapse

    def show_result(self, index: int, expand: bool) -> None:
        self._result_stack.setCurrentIndex(index)
        if expand and not self._result_stack.isVisible():
            self.toggle_results(open_it=True)

    def results_open(self) -> bool:
        # In COMPARE mode, results are shown via compare view container
        if self._view_mode == StrategyViewMode.COMPARE:
            return self._results_container.isVisible()  # type: ignore[attr-defined]
        return self._result_stack.isVisible()

    def toggle_results(self, open_it: bool | None = None) -> None:
        # Delegate to container visibility when in COMPARE, else stack
        if self._view_mode == StrategyViewMode.COMPARE:
            opening = open_it if open_it is not None else not self._results_container.isVisible()
            self._results_container.setVisible(opening)
            self._collapse_btn.setText("▾" if opening else "▴")
            return
        opening = open_it if open_it is not None else not self._result_stack.isVisible()
        if opening:
            height = self._results_height
            self._result_stack.show()
            self._collapse_btn.setText("▾")
            self._animate_max(self._result_stack, b"maximumHeight", 0, height)
        else:
            sizes = self._root.sizes()
            if len(sizes) > 1 and sizes[1] > 60:
                self._results_height = sizes[1]
            self._collapse_btn.setText("▴")
            self._animate_max(
                self._result_stack,
                b"maximumHeight",
                self._result_stack.height(),
                0,
                done=self._result_stack.hide,
            )

    _results_height = 280

    def _animate_max(
        self, widget: QWidget, prop: bytes, start: int, end: int, done: object = None
    ) -> None:
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation

        anim = QPropertyAnimation(widget, prop, self)
        anim.setDuration(160)
        anim.setEasingCurve(QEasingCurve.Type.OutCurve)
        anim.setStartValue(start)
        anim.setEndValue(end)
        if done is not None:
            anim.finished.connect(done)  # type: ignore[arg-type]
        self._anim = anim
        anim.start()

    _anim: object = None

    def _set_run_busy(self, busy: bool) -> None:
        self._topbar_run.setEnabled(not busy)
        if busy:
            self._topbar_run.setText("RUNNING BACKTEST…")
        else:
            self._apply_top_run_label()
        # Note: right_settings already reflects busy via its own set_busy emission;
        # do NOT call set_busy here or we recurse via busy_changed.

    def _apply_top_run_label(self) -> None:
        if self._view_mode == StrategyViewMode.BUY:
            self._topbar_run.setText("▶  RUN BUY BACKTEST")
        elif self._view_mode == StrategyViewMode.SELL:
            self._topbar_run.setText("▶  RUN SELL BACKTEST")
        else:
            self._topbar_run.setText("▶  RUN ALL")

    def _emit_run(self) -> None:
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

    def current_tab_name(self) -> str:
        return self.center_detail.current_tab_name()

    def open_strategy(self, name: str, code: str) -> None:
        self.center_detail.open_buffer(name, code)
        self.set_strategy_name(name)
        # Preserve directional state across strategy switches? Per spec, switching
        # strategy should likely reset directional results. Keep current mode but clear old results
        # The caller (bootstrap) will repopulate library; we keep view mode.
        self._full_result = None
        self._buy_result = None
        self._sell_result = None
        self._refresh_directional_views()

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
            self._full_result = None
            self._buy_result = None
            self._sell_result = None
            self._refresh_directional_views()

    strategy_open_requested = Signal(str)

    # ------------------------------------------------------------------
    # Directional view mode API — buffered BUY/SELL state
    # ------------------------------------------------------------------
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
                dock = self._results_dock
                dock.setVisible(False)
                self._compare_view.setVisible(True)
                self._collapse_btn.setText("▾")
            except Exception:
                pass
            self._refresh_directional_views()
        else:
            try:
                dock = self._results_dock
                dock.setVisible(True)
                self._compare_view.setVisible(False)
            except Exception:
                pass
            self._refresh_directional_views()
        self.update()

    def set_result(self, result: StrategyResult | None) -> None:
        # Derive per-side results via filtered replay — same engine numbers, different view
        # Respect pending side so COMPARE can show “Not backtested yet” after a single-side run
        pending = self._pending_side
        self._pending_side = None
        self._full_result = result
        if result is None:
            self._buy_result = None
            self._sell_result = None
        else:
            try:
                from backtest import derive_directional_result

                if pending == StrategyViewMode.BUY:
                    self._buy_result = derive_directional_result(result, "LONG")
                elif pending == StrategyViewMode.SELL:
                    self._sell_result = derive_directional_result(result, "SHORT")
                else:
                    # COMPARE / initial / backward-compat direct call → derive both
                    self._buy_result = derive_directional_result(result, "LONG")
                    self._sell_result = derive_directional_result(result, "SHORT")
            except Exception:
                # Fallback: raw split
                if pending == StrategyViewMode.SELL:
                    self._sell_result = result
                else:
                    self._buy_result = result
        self._refresh_directional_views()
        # For backward compat, also expand results if we have any result
        if result is not None:
            self.show_result(0, expand=True)
            # Ensure compare view gets latest (pass full for combined blotter)
            try:
                self._compare_view.set_results(self._buy_result, self._sell_result, result)
            except Exception:
                pass

    def set_results(self, buy: StrategyResult | None, sell: StrategyResult | None) -> None:
        """Explicitly set per-side results (used for isolated BUY/SELL runs)."""
        self._buy_result = buy
        self._sell_result = sell
        # Keep full as the side that last ran or a synthetic aggregate
        if buy is not None and sell is None:
            self._full_result = buy
        elif sell is not None and buy is None:
            self._full_result = sell
        elif buy is not None and sell is not None:
            # keep existing full if present
            pass
        self._refresh_directional_views()
        try:
            self._compare_view.set_results(buy, sell, self._full_result)
        except Exception:
            pass

    def _refresh_directional_views(self) -> None:
        mode = self._view_mode
        # Pick active result for single-mode panels
        active: StrategyResult | None
        if mode == StrategyViewMode.BUY:
            active = self._buy_result
            # If buy has never been run but full exists, show buy filtered (may be empty trades)
            # If both None -> empty state placeholder
            if (
                active is None
                and self._buy_result is None
                and self._sell_result is None
                and self._full_result is None
            ):
                # truly nothing
                pass
        elif mode == StrategyViewMode.SELL:
            active = self._sell_result
        else:
            # COMPARE — push to compare view, keep metrics etc hidden
            try:
                self._compare_view.set_results(
                    self._buy_result, self._sell_result, self._full_result
                )
            except Exception:
                pass
            return
        # Empty states per spec §26
        has = active is not None
        # Metrics / Equity / Trade views always get active (may be zero-trade result)
        # When active is None, show directional empty placeholder via metrics placeholder "--"
        self.metrics.set_result(active)
        self._equity_view.set_result(active)
        if hasattr(self, "_perf_equity"):
            try:
                self._perf_equity.set_result(active)  # type: ignore[attr-defined]
            except Exception:
                pass
        self._drawdown_view.set_result(active)  # type: ignore[attr-defined]
        self.journal.set_result(active)
        # Side filter for trade blotter
        if mode == StrategyViewMode.BUY:
            self.journal.set_side_mode("BUY")
            self.journal.set_side_filter_visible(False)
        elif mode == StrategyViewMode.SELL:
            self.journal.set_side_mode("SELL")
            self.journal.set_side_filter_visible(False)
        # Equity summary — show START/END per side
        self._update_equity_summary(active)
        # If active is None (never run), show directional placeholder text in journal placeholder
        if not has:
            # customize placeholder text per direction
            if mode == StrategyViewMode.BUY:
                self.journal._placeholder.setText(
                    "BUY BACKTEST NOT RUN\nConfigure your strategy and run the BUY backtest."
                )
            else:
                self.journal._placeholder.setText(
                    "SELL BACKTEST NOT RUN\nConfigure your strategy and run the SELL backtest."
                )
            # metrics already shows "--"

    def _update_equity_summary(self, result: StrategyResult | None) -> None:
        points = result.equity_curve if result is not None else ()
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
            prefix = f"{dir_tag} " if dir_tag else ""
            self._equity_summary.setText(
                f"{prefix}START ₹{_inr(start)}   ·   END ₹{_inr(end)}   ·   "
                f"<span style='color:{color};'>RETURN {ret:+.2f}%</span>"
            )
        elif len(points) == 1:
            start = float(points[0].equity)
            self._equity_summary.setText(f"START ₹{_inr(start)}   ·   No trades yet")
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
        self.metrics.set_result(None)
        self._equity_view.set_result(None)
        try:
            self._perf_equity.set_result(None)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._drawdown_view.set_result(None)  # type: ignore[attr-defined]
        self.journal.set_result(None)
        try:
            self._compare_view.set_results(None, None)
        except Exception:
            pass
        self._update_equity_summary(None)


__all__ = ["StrategyLabWorkspace", "StrategyLibraryPanel", "BacktestRunPanel", "StrategyViewMode"]
