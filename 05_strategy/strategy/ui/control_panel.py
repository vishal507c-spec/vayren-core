"""StrategyControlPanel — the Strategy Lab's control column."""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from strategy.models.definition import StrategyDefinition
from strategy.models.form import BacktestForm

_DATE_FMT = "dd MMM yyyy"
_ISO_FMT = "yyyy-MM-dd"
_DEFAULT_FROM = QDate(2017, 1, 1)

_LABEL_STYLE = "color: palette(placeholder-text); font-size: 11px; font-weight: 600;"

_PRIMARY_STYLE = """
QPushButton {
    background: palette(highlight);
    color: palette(highlighted-text);
    border: none;
    border-radius: 3px;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
}
QPushButton:hover {
    background: palette(highlight);
}
QPushButton:disabled {
    background: palette(mid);
    color: palette(placeholder-text);
}
"""

_SECONDARY_STYLE = """
QPushButton {
    color: palette(text);
    background: transparent;
    border: 1px solid palette(midlight);
    border-radius: 3px;
    padding: 3px 5px;
    font-size: 11px;
}
QPushButton:hover {
    border-color: palette(highlight);
    color: palette(highlight);
}
QPushButton:disabled {
    color: palette(placeholder-text);
}
"""

_SPIN_STYLE = """
QDoubleSpinBox {
    border: 1px solid palette(midlight);
    border-radius: 3px;
    padding: 2px 6px;
    background: palette(base);
    color: palette(text);
    font-size: 12px;
    font-weight: 600;
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}
"""


class StrategyControlPanel(QWidget):
    """Strategy Lab control form; emits :class:`BacktestForm` requests."""

    backtest_requested = Signal(object)  # BacktestForm
    paper_trade_requested = Signal(str)  # active strategy id
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._strategies: tuple[StrategyDefinition, ...] = ()
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        layout.addWidget(self._section_label("STRATEGY CONTROL"))
        layout.addLayout(self._build_strategy_row())
        layout.addWidget(self._section_label("DATE RANGE"))
        layout.addLayout(self._build_dates())
        layout.addLayout(self._build_timeframe())
        layout.addWidget(self._section_label("EXECUTION COSTS"))
        layout.addLayout(self._build_costs())
        layout.addStretch(1)
        layout.addLayout(self._build_actions())

    def _section_label(self, title: str) -> QLabel:
        label = QLabel(title, self)
        label.setStyleSheet(_LABEL_STYLE)
        return label

    def _field_label(self, caption: str) -> QLabel:
        label = QLabel(caption, self)
        label.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        return label

    def _build_strategy_row(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)
        box.addWidget(self._field_label("ACTIVE STRATEGY"))
        self._strategy_combo = QComboBox(self)
        self._strategy_combo.setStyleSheet("font-weight: 600;")
        box.addWidget(self._strategy_combo)
        return box

    def _build_dates(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(4)
        self._from_edit = self._date_edit(_DEFAULT_FROM)
        self._to_edit = self._date_edit(QDate.currentDate())
        arrow = QLabel("→", self)
        arrow.setStyleSheet("color: palette(placeholder-text);")
        row.addWidget(self._from_edit, 1)
        row.addWidget(arrow)
        row.addWidget(self._to_edit, 1)
        box.addLayout(row)
        return box

    def _date_edit(self, initial: QDate) -> QDateEdit:
        edit = QDateEdit(self)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat(_DATE_FMT)
        edit.setDate(initial)
        edit.setStyleSheet(
            "QDateEdit { border: 1px solid palette(midlight); border-radius: 3px;"
            " padding: 2px 6px; background: palette(base); font-weight: 600; }"
        )
        return edit

    def _build_timeframe(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)
        box.addWidget(self._field_label("TIMEFRAME"))
        self._timeframe_combo = QComboBox(self)
        self._timeframe_combo.setStyleSheet("font-weight: 600;")
        box.addWidget(self._timeframe_combo)
        return box

    def _build_costs(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        self._capital_spin: QDoubleSpinBox
        self._slippage_spin: QDoubleSpinBox
        self._commission_spin: QDoubleSpinBox
        capital_box = self._cost_group(
            "INITIAL CAPITAL (₹)", 1_000_000.0, 1_000.0, 1_000_000_000.0, 0, True
        )
        self._capital_spin = capital_box[1]
        slippage_box = self._cost_group("SLIPPAGE", 0.02, 0.0, 5.0, 2, False)
        self._slippage_spin = slippage_box[1]
        self._slippage_spin.setSuffix(" %")
        commission_box = self._cost_group("COMMISSION", 0.03, 0.0, 5.0, 2, False)
        self._commission_spin = commission_box[1]
        self._commission_spin.setSuffix(" %")
        for wrapper in (capital_box[0], slippage_box[0], commission_box[0]):
            box.addWidget(wrapper)
        return box

    def _cost_group(
        self,
        caption: str,
        value: float,
        minimum: float,
        maximum: float,
        decimals: int,
        group_separator: bool,
    ) -> tuple[QWidget, QDoubleSpinBox]:
        wrapper = QWidget(self)
        inner = QVBoxLayout(wrapper)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(2)
        inner.addWidget(self._field_label(caption))
        spin = QDoubleSpinBox(wrapper)
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setGroupSeparatorShown(group_separator)
        spin.setStyleSheet(_SPIN_STYLE)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        inner.addWidget(spin)
        return wrapper, spin

    def _build_actions(self) -> QVBoxLayout:
        actions = QVBoxLayout()
        actions.setSpacing(4)
        self._run_button = QPushButton("RUN BACKTEST", self)
        self._run_button.setStyleSheet(_PRIMARY_STYLE)
        self._run_button.setMinimumHeight(28)
        self._run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_button.clicked.connect(self._emit_backtest)
        actions.addWidget(self._run_button)
        secondary = QHBoxLayout()
        secondary.setSpacing(4)
        self._paper_button = QPushButton("PAPER TRADE", self)
        self._paper_button.setStyleSheet(_SECONDARY_STYLE)
        self._paper_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paper_button.clicked.connect(self._emit_paper_trade)
        self._reset_button = QPushButton("RESET", self)
        self._reset_button.setStyleSheet(_SECONDARY_STYLE)
        self._reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_button.clicked.connect(self.reset_requested)
        secondary.addWidget(self._paper_button)
        secondary.addWidget(self._reset_button)
        actions.addLayout(secondary)
        return actions

    def set_strategies(self, strategies: tuple[StrategyDefinition, ...]) -> None:
        """Replace the selectable strategies."""
        self._strategies = strategies
        current = self.active_strategy_id
        self._strategy_combo.blockSignals(True)
        self._strategy_combo.clear()
        if not strategies:
            self._strategy_combo.addItem("No strategies registered", userData=None)
            self._strategy_combo.setEnabled(False)
        else:
            self._strategy_combo.setEnabled(not self._busy)
            for definition in strategies:
                self._strategy_combo.addItem(definition.label, userData=definition.id)
            index = self._strategy_combo.findData(current)
            if index >= 0:
                self._strategy_combo.setCurrentIndex(index)
        self._strategy_combo.blockSignals(False)
        self._update_run_enabled()

    def set_timeframes(self, timeframes: tuple[str, ...]) -> None:
        """Replace the timeframe options (real detected timeframes only)."""
        current = self.timeframe
        self._timeframe_combo.blockSignals(True)
        self._timeframe_combo.clear()
        for timeframe in timeframes:
            self._timeframe_combo.addItem(timeframe, userData=timeframe)
        if current is not None:
            index = self._timeframe_combo.findData(current)
            if index >= 0:
                self._timeframe_combo.setCurrentIndex(index)
        self._timeframe_combo.blockSignals(False)
        self._update_run_enabled()

    def select_timeframe(self, timeframe: str) -> None:
        """Highlight `timeframe` when present (chart sync)."""
        index = self._timeframe_combo.findData(timeframe)
        if index >= 0:
            self._timeframe_combo.blockSignals(True)
            self._timeframe_combo.setCurrentIndex(index)
            self._timeframe_combo.blockSignals(False)
            self._update_run_enabled()

    def set_busy(self, busy: bool) -> None:
        """Disable inputs while a backtest runs."""
        self._busy = busy
        self._strategy_combo.setEnabled(not busy and bool(self._strategies))
        self._from_edit.setEnabled(not busy)
        self._to_edit.setEnabled(not busy)
        self._timeframe_combo.setEnabled(not busy)
        self._capital_spin.setEnabled(not busy)
        self._slippage_spin.setEnabled(not busy)
        self._commission_spin.setEnabled(not busy)
        self._paper_button.setEnabled(not busy)
        self._update_run_enabled()

    @property
    def active_strategy_id(self) -> str | None:
        """The selected strategy id, or None."""
        data = self._strategy_combo.currentData()
        return str(data) if data is not None else None

    @property
    def timeframe(self) -> str | None:
        """The selected timeframe, or None."""
        data = self._timeframe_combo.currentData()
        return str(data) if data is not None else None

    def _emit_backtest(self) -> None:
        strategy_id = self.active_strategy_id
        timeframe = self.timeframe
        if strategy_id is None or timeframe is None:
            return
        form = BacktestForm(
            strategy_id=strategy_id,
            timeframe=timeframe,
            start_date=self._from_edit.date().toString("yyyy-MM-dd"),
            end_date=self._to_edit.date().toString("yyyy-MM-dd"),
            initial_capital=self._capital_spin.value(),
            slippage_pct=self._slippage_spin.value(),
            commission_pct=self._commission_spin.value(),
        )
        self.backtest_requested.emit(form)

    def _emit_paper_trade(self) -> None:
        strategy_id = self.active_strategy_id
        if strategy_id is not None:
            self.paper_trade_requested.emit(strategy_id)

    def _update_run_enabled(self) -> None:
        ready = (
            not self._busy and self.active_strategy_id is not None and self.timeframe is not None
        )
        self._run_button.setEnabled(ready)
