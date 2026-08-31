"""DownloadPanel — the download console's configuration surface.

One flow of sections: Configuration (interval, stock search + chips +
checklist, date range with quick ranges), Download Plan (live estimates),
then the action row (Download Data / Check Coverage / Cancel Download).
The whole panel scrolls inside the host's single scroll area; the stock
checklist keeps its own internal list scroll. Pure input surface: emits
typed signals, exposes a busy state. No bus, no events, no engine.
"""

from __future__ import annotations

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from data.settings import INTERVAL_LABEL, INTERVAL_MINUTES, NSE_HOLIDAYS
from data.ui.section_label import SectionLabel
from data.ui.stock_checklist import StockChecklistWidget

_DATE_FMT = "yyyy-MM-dd"  # internal / emitted value, always ISO
_DISPLAY_FMT = "dd MMM yyyy"  # what the inputs show
_PLAN_FMT = "dd MMM yyyy"

_DEFAULT_FROM = QDate(2017, 1, 1)

_EST_BARS_PER_DAY_MIN = 375  # NSE trading minutes per day, plan estimate

_LABEL_STYLE = "color: palette(placeholder-text); font-size: 11px; font-weight: 600;"

_COMPACT_STYLE = """
DownloadPanel QPushButton {
    padding: 2px 5px;
    font-size: 11px;
}
DownloadPanel QComboBox {
    padding: 2px 4px;
    font-size: 12px;
    font-weight: 600;
}
"""

_DATE_EDIT_STYLE = """
QDateEdit {
    min-width: 0;
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
QDateEdit::drop-down {
    border: none;
    width: 20px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}
QDateEdit:disabled {
    color: palette(placeholder-text);
}
"""

_PRIMARY_STYLE = """
QPushButton {
    background: palette(highlight);
    color: palette(highlighted-text);
    border: none;
    border-radius: 3px;
    padding: 4px 8px;
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
    padding: 2px 5px;
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


def _trading_days(from_date: QDate, to_date: QDate) -> int:
    """Weekdays minus known NSE holidays inside [from, to] — plan estimate."""
    days = 0
    day = from_date
    while day <= to_date:
        if day.dayOfWeek() <= 5 and day.toString(_DATE_FMT) not in NSE_HOLIDAYS:
            days += 1
        day = day.addDays(1)
    return days


def _format_rows(count: int) -> str:
    if count >= 1_000_000:
        return f"~{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"~{count / 1_000:.0f}K"
    return f"{count}"


class _SlimLabel(QLabel):
    """QLabel that never forces the side panel wider than its slot."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class _SlimButton(QPushButton):
    """QPushButton with the same shrink-tolerant minimum size hint."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class DownloadPanel(QWidget):
    """Inputs for one explicit download range or a coverage scan.

    Configuration (interval, stock selection with chips, date range with
    quick ranges), the live Download Plan and the action row form one
    vertical flow; the host's scroll area scrolls the whole panel, and the
    stock checklist scrolls its own list. Emits typed signals, exposes a
    busy state. No bus, no events, no engine.
    """

    download_requested = Signal(str, str, str, str)  # symbol, interval, from, to
    coverage_requested = Signal(str, str)  # symbol, interval
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self.setStyleSheet(_COMPACT_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        layout.addWidget(SectionLabel("Configuration", self))
        layout.addLayout(self._build_interval(self))
        layout.addLayout(self._build_stocks(self))
        layout.addLayout(self._build_dates(self))

        layout.addWidget(SectionLabel("Download Plan", self))
        layout.addLayout(self._build_plan(self))

        layout.addLayout(self._build_actions())

        self._stocks.selection_changed.connect(self._on_selection_changed)
        self._interval.currentIndexChanged.connect(self._on_input_changed)
        self._from_edit.dateChanged.connect(self._on_input_changed)
        self._to_edit.dateChanged.connect(self._on_input_changed)

        self._update_plan()

    # ── construction ─────────────────────────────────────────────────────────

    def _build_interval(self, parent: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)
        label = QLabel("INTERVAL", parent)
        label.setStyleSheet(_LABEL_STYLE)
        self._interval = QComboBox(parent)
        for interval_id, label_text in INTERVAL_LABEL.items():
            self._interval.addItem(label_text, userData=interval_id)
        self._interval.setCurrentIndex(max(0, list(INTERVAL_LABEL).index("15m")))
        box.addWidget(label)
        box.addWidget(self._interval)
        return box

    def _build_stocks(self, parent: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        label = QLabel("STOCKS", parent)
        label.setStyleSheet(_LABEL_STYLE)
        self._stocks = StockChecklistWidget(parent)
        box.addWidget(label)
        box.addWidget(self._stocks, 1)
        return box

    def _build_dates(self, parent: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)
        label = QLabel("DATE RANGE", parent)
        label.setStyleSheet(_LABEL_STYLE)
        box.addWidget(label)
        today = QDate.currentDate()
        self._from_edit = QDateEdit(parent)
        self._from_edit.setCalendarPopup(True)
        self._from_edit.setDisplayFormat(_DISPLAY_FMT)
        self._from_edit.setDate(_DEFAULT_FROM)
        self._from_edit.setStyleSheet(_DATE_EDIT_STYLE)
        self._from_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._to_edit = QDateEdit(parent)
        self._to_edit.setCalendarPopup(True)
        self._to_edit.setDisplayFormat(_DISPLAY_FMT)
        self._to_edit.setDate(today)
        self._to_edit.setStyleSheet(_DATE_EDIT_STYLE)
        self._to_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box.addWidget(self._date_box("From", self._from_edit, parent))
        box.addWidget(self._date_box("To", self._to_edit, parent))

        quick = QHBoxLayout()
        quick.setSpacing(4)
        for caption, months, years in (
            ("1M", -1, 0),
            ("3M", -3, 0),
            ("6M", -6, 0),
            ("1Y", 0, -1),
            ("MAX", 0, -10),
        ):
            button = _SlimButton(caption, parent)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(18)
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _=False, m=months, y=years: self._apply_quick_range(m, y))
            quick.addWidget(button)
        quick.addStretch(1)
        box.addLayout(quick)
        return box

    def _date_box(self, caption: str, edit: QDateEdit, parent: QWidget) -> QWidget:
        label = QLabel(caption.upper(), parent)
        label.setStyleSheet(_LABEL_STYLE)
        inner = QVBoxLayout()
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(2)
        inner.addWidget(label)
        inner.addWidget(edit)
        wrapper = QWidget(parent)
        wrapper.setLayout(inner)
        return wrapper

    def _build_plan(self, parent: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(3)
        self._plan_stocks = self._plan_row(box, "Stocks", parent)
        self._plan_interval = self._plan_row(box, "Interval", parent)
        self._plan_range = self._plan_row(box, "Date Range", parent)
        # Full range must never truncate — wrap instead of clipping.
        self._plan_range.setWordWrap(True)
        self._plan_range.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._plan_days = self._plan_row(box, "Est. Trading Days", parent)
        self._plan_rows = self._plan_row(box, "Est. Data Rows", parent)
        self._plan_error = _SlimLabel("", parent)
        self._plan_error.setStyleSheet("color: palette(highlight); font-size: 10px;")
        box.addWidget(self._plan_error)
        return box

    def _plan_row(self, box: QVBoxLayout, caption: str, parent: QWidget) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)
        name = _SlimLabel(caption, parent)
        name.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        value = _SlimLabel("—", parent)
        value.setStyleSheet("font-size: 11px; font-weight: 600;")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        row.addWidget(name, 1)
        row.addWidget(value, 1)
        box.addLayout(row)
        return value

    def _build_actions(self) -> QVBoxLayout:
        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)

        self._download_button = QPushButton("Download Data", self)
        self._download_button.setStyleSheet(_PRIMARY_STYLE)
        self._download_button.setMinimumHeight(26)
        self._download_button.setDefault(True)
        self._download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_button.clicked.connect(self._emit_download)
        actions.addWidget(self._download_button)

        secondary = QHBoxLayout()
        secondary.setSpacing(4)
        self._coverage_button = QPushButton("Check Coverage", self)
        self._coverage_button.setStyleSheet(_SECONDARY_STYLE)
        self._coverage_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._coverage_button.clicked.connect(self._emit_coverage)
        self._cancel_button = QPushButton("Cancel Download", self)
        self._cancel_button.setStyleSheet(_SECONDARY_STYLE)
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.setEnabled(False)
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self.cancel_requested)
        secondary.addWidget(self._coverage_button, 1)
        secondary.addWidget(self._cancel_button, 1)
        actions.addLayout(secondary)
        return actions

    # ── public surface ───────────────────────────────────────────────────────

    def set_busy(self, busy: bool) -> None:
        """Disable inputs while a run is in flight."""
        self._busy = busy
        self._interval.setEnabled(not busy)
        self._from_edit.setEnabled(not busy)
        self._to_edit.setEnabled(not busy)
        self._stocks.setEnabled(not busy)
        self._cancel_button.setEnabled(busy)
        self._cancel_button.setVisible(busy)
        self._update_actions()

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        """Replace the stock universe offered by the checklist."""
        self._stocks.set_symbols(symbols)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._stocks.symbols

    @property
    def busy(self) -> bool:
        return self._busy

    # ── input reactions ──────────────────────────────────────────────────────

    def _on_input_changed(self) -> None:
        self._update_plan()

    def _on_selection_changed(self) -> None:
        self._update_plan()

    def _apply_quick_range(self, months: int, years: int) -> None:
        today = QDate.currentDate()
        start = today
        if months:
            start = today.addMonths(months)
        if years:
            start = today.addYears(years)
        self._from_edit.setDate(start)
        self._to_edit.setDate(today)

    def _update_plan(self) -> None:
        count = self._stocks.selected_count
        interval_id = str(self._interval.currentData())
        interval_label = INTERVAL_LABEL.get(interval_id, interval_id)
        from_date = self._from_edit.date()
        to_date = self._to_edit.date()

        self._plan_stocks.setText(f"{count}")
        self._plan_interval.setText(interval_label)
        self._plan_range.setText(f"{from_date.toString(_PLAN_FMT)} → {to_date.toString(_PLAN_FMT)}")
        if from_date > to_date:
            self._plan_days.setText("—")
            self._plan_rows.setText("—")
            self._plan_error.setText("From date is after To date")
        else:
            self._plan_error.clear()
            days = _trading_days(from_date, to_date)
            minutes = INTERVAL_MINUTES.get(interval_id, 15)
            bars_per_day = max(1, round(_EST_BARS_PER_DAY_MIN / minutes))
            self._plan_days.setText(f"{days}")
            self._plan_rows.setText(_format_rows(days * bars_per_day * count))
        self._update_actions()

    def _update_actions(self) -> None:
        valid_dates = self._from_edit.date() <= self._to_edit.date()
        self._download_button.setEnabled(not self._busy and valid_dates)
        self._coverage_button.setEnabled(not self._busy)

    # ── request emission ─────────────────────────────────────────────────────

    def _emit_download(self) -> None:
        symbols = self._stocks.selected_symbols
        if not symbols:
            return
        interval_id = self._interval.currentData()
        self.download_requested.emit(
            symbols[0],
            str(interval_id),
            self._from_edit.date().toString(_DATE_FMT),
            self._to_edit.date().toString(_DATE_FMT),
        )

    def _emit_coverage(self) -> None:
        symbols = self._stocks.selected_symbols
        if not symbols:
            return
        interval_id = self._interval.currentData()
        self.coverage_requested.emit(symbols[0], str(interval_id))
