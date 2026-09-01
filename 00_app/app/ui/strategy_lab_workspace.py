"""Strategy Lab Workspace — clean two-column research screen.

LEFT (25%): strategy library. MAIN (75%): strategy workspace
(CODE | PARAMETERS | BACKTEST). BOTTOM: results. Empty states are
intentional and the hierarchy STRATEGY → CODE → PARAMETERS → BACKTEST
is immediately obvious.
"""

from __future__ import annotations

from datetime import datetime

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
        # Empty state — centered icon + text + single primary CTA
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
        """Open the inline rename prompt (shared by row menu and title edit)."""
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
        # Spec: exactly ONE primary NEW STRATEGY in empty state
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
            # Prefer declared specs (key + label) — param_defaults duplicates
            # every param under both label and key, which would double the rows.
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
                spin.valueChanged.connect(
                    lambda v, k=key: self.param_changed.emit(k, float(v))
                )
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
        self._run.setText("RUNNING BACKTEST…" if busy else "▶  RUN BACKTEST")
        if busy:
            self._hide_error()
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


class MetricsTiles(QWidget):
    """Compact metric tiles + secondary statistics strip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1}; border-bottom: 1px solid {t.BORDER};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 8)
        lay.setSpacing(4)
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

    @staticmethod
    def _value_style(color: str) -> str:
        return f"color: {color}; font-size: 15px; font-weight: 700;"

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


class TradeBlotter(QWidget):
    """Dense trade table with filter and CSV export."""

    trade_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {t.BG1};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 8, 14, 8)
        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter trades")
        self._filter.setStyleSheet(t.INPUT_QSS)
        self._filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self._filter, 1)
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
        self._table.setStyleSheet(t.TABLE_QSS)
        self._table.cellClicked.connect(self._on_cell)
        self._table.setVisible(False)
        lay.addWidget(self._table, 1)
        self._trades: list = []

    def set_result(self, result: StrategyResult | None) -> None:
        has = result is not None and bool(result.trades)
        self._placeholder.setVisible(not has)
        self._table.setVisible(has)
        self._table.setRowCount(0)
        self._trades = list(result.trades) if result else []
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
                self._table.setItem(i, col, item)

    def _on_cell(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item is not None:
            self.trade_clicked.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self._table.rowCount()):
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


class StrategyLabWorkspace(QWidget):
    """Two-column lab: library (25%) | strategy workspace (75%), results below."""

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
        self.center_detail.save_requested.connect(self.save_requested_relay)
        self.center_detail.compile_requested.connect(self.compile_requested_relay)
        self.center_detail.tab_changed.connect(self._crumb_name.setText)
        self.center_detail.rename_affordance.connect(
            lambda: self.left_nav.request_rename(self.current_tab_name())
        )
        self.center_detail.param_changed.connect(self.param_changed.emit)
        # Backtest panel lives inside EditorPane's BACKTEST tab; expose alias
        self.right_settings = self.center_detail.backtest_panel
        self.right_settings.run_requested.connect(self.run_backtest.emit)
        self.right_settings.busy_changed.connect(self._set_run_busy)
        # Center stack: empty state vs editor
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
        self._root.addWidget(self._main)
        self._root.addWidget(results_dock)
        self._root.setStretchFactor(0, 1)
        self._root.setStretchFactor(1, 0)
        outer.addWidget(self._root, 1)
        self._setup_shortcuts()
        self._main.setSizes([280, 880])

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
        # breadcrumb reflects state — empty shows nothing
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
        self._topbar_run = QPushButton("▶  RUN BACKTEST", bar)
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
        return self._result_stack.isVisible()

    def toggle_results(self, open_it: bool | None = None) -> None:
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
        self._topbar_run.setText("RUNNING BACKTEST…" if busy else "▶  RUN BACKTEST")

    def _emit_run(self) -> None:
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

    def select_next_after_delete(self, deleted: str) -> None:
        """Open the neighbour of a deleted strategy; fall back to empty state."""
        names = [name for name in self.left_nav.names() if name != deleted]
        target = next((name for name in names if name > deleted), None)
        if target is None and names:
            target = names[-1]
        if target is not None:
            self.strategy_open_requested.emit(target)
        else:
            self._set_center_empty(True)
            self._crumb_name.setText("")

    strategy_open_requested = Signal(str)

    def set_result(self, result: StrategyResult | None) -> None:
        self.metrics.set_result(result)
        self._equity_view.set_result(result)
        self._drawdown_view.set_result(result)
        self.journal.set_result(result)
        self._update_equity_summary(result)
        if result is not None:
            self.show_result(0, expand=True)

    def _update_equity_summary(self, result: StrategyResult | None) -> None:
        points = result.equity_curve if result is not None else ()
        if len(points) >= 2:
            start = float(points[0].equity)
            end = float(points[-1].equity)
            ret = (end - start) / start * 100 if start else 0.0
            color = t.POS if ret >= 0 else t.NEG
            self._equity_summary.setText(
                f"START ₹{_inr(start)}   ·   END ₹{_inr(end)}   ·   "
                f"<span style='color:{color};'>RETURN {ret:+.2f}%</span>"
            )
        else:
            self._equity_summary.setText("No backtest results yet.")

    def clear(self) -> None:
        self.metrics.set_result(None)
        self._equity_view.set_result(None)
        self._drawdown_view.set_result(None)
        self.journal.set_result(None)


__all__ = ["StrategyLabWorkspace"]
