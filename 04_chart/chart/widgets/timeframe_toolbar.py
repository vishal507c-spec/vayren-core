"""TimeframeToolbar — single-line timeframe row (TradingView-inspired).

Premium minimal: 15m 30m 45m 1h 2h 4h ▾  — no separators, no wrapping,
one horizontal line, flexbox, responsive. Overflow (1D,1W,…) lives in the
small ▾ dropdown. Active timeframe highlighted via existing VAYREN palette.
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class TimeframeToolbar(QWidget):
    """Shows the timeframes detected for the current symbol as buttons.

    Single horizontal line, no separators, no wrapping, no scrollbar —
    flexbox via QHBoxLayout. Visible: 15m,30m,45m,1h,2h,4h. Overflow (1D,1W,…)
    inside a small ▾ dropdown. Active highlighted via palette(highlight).

    A click emits ``timeframe_selected`` with the timeframe label. Pure UI:
    no bus, no SQL, no events — the window turns the signal into events.
    Labels come from the market-side detection (SQLite), never hardcoded.
    """

    timeframe_selected = Signal(str)

    # Canonical visible order (TradingView-inspired, premium compact)
    _VISIBLE_ORDER: tuple[str, ...] = ("5m", "15m", "30m", "45m", "1h", "2h", "4h")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(32)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._layout = lay

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._overflow: tuple[str, ...] = ()
        self._dropdown: QPushButton | None = None
        self._menu: QMenu | None = None
        self._active: str | None = None
        self._all_timeframes: tuple[str, ...] = ()

    def set_timeframes(self, timeframes: tuple[str, ...]) -> None:
        """Replace the buttons: 15m 30m 45m 1h 2h 4h + ▾ (1D,1W,…). No separators."""
        self.clear()
        self._all_timeframes = tuple(timeframes)
        available = set(timeframes)
        visible = [tf for tf in self._VISIBLE_ORDER if tf in available]
        overflow = tuple(tf for tf in timeframes if tf not in visible)
        self._overflow = overflow

        for timeframe in visible:
            button = QPushButton(timeframe, self)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setMinimumWidth(44)
            button.setFixedHeight(24)
            button.setMinimumHeight(24)
            self._layout.addWidget(button)
            self._group.addButton(button)
            self._buttons[timeframe] = button
            button.clicked.connect(
                lambda _checked=False, tf=timeframe: self.timeframe_selected.emit(tf)
            )

        # Small dropdown for overflow (1D,1W,…) — no large dropdown
        self._dropdown = QPushButton("▼", self)
        self._dropdown.setCheckable(True)
        self._dropdown.setFixedWidth(28)
        self._dropdown.setFixedHeight(24)
        self._dropdown.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._dropdown.setStyleSheet(
            "QPushButton { font-size: 10px; padding: 0px; }"  # noqa: E501
            "QPushButton:checked { background: palette(highlight); color: palette(highlighted-text); }"  # noqa: E501
        )
        self._menu = QMenu(self._dropdown)
        self._menu.setStyleSheet("QMenu { min-width: 64px; }")
        for tf in overflow:
            act = self._menu.addAction(tf)
            act.triggered.connect(lambda _checked=False, t=tf: self.timeframe_selected.emit(t))
        self._dropdown.clicked.connect(self._show_menu)
        self._layout.addWidget(self._dropdown)

        # Stretch to keep everything left-aligned, no wrapping, no scrollbar
        self._layout.addStretch(1)

    def clear(self) -> None:
        """Remove all timeframe buttons and dropdown."""
        for _label, button in list(self._buttons.items()):
            self._group.removeButton(button)
            self._layout.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()
        if self._dropdown is not None:
            self._layout.removeWidget(self._dropdown)
            self._dropdown.deleteLater()
            self._dropdown = None
        if self._menu is not None:
            self._menu.deleteLater()
            self._menu = None
        self._overflow = ()

    def _show_menu(self) -> None:
        if self._menu is None or self._dropdown is None:
            return
        # Rebuild menu in case timeframes changed (keeps 1D/1W working exactly as before)
        self._menu.clear()
        for tf in self._overflow:
            act = self._menu.addAction(tf)
            act.triggered.connect(lambda _checked=False, t=tf: self.timeframe_selected.emit(t))
        # Highlight active if overflow contains it
        for act in self._menu.actions():
            act.setCheckable(True)
            act.setChecked(act.text().lower() == (self._active or "").lower())
        pos = self._dropdown.mapToGlobal(self._dropdown.rect().bottomLeft())
        self._menu.exec(pos)

    def select_timeframe(self, timeframe: str) -> None:
        """Check the button matching `timeframe` (visible or dropdown)."""
        self._active = timeframe
        target = None
        for label, button in self._buttons.items():
            if label.lower() == timeframe.lower():
                target = button
                break
        if target is not None:
            target.setChecked(True)
            if self._dropdown is not None:
                self._dropdown.setChecked(False)
            return
        # Overflow active → highlight dropdown arrow (case-insensitive)
        if any(tf.lower() == timeframe.lower() for tf in self._overflow):
            for _, b in self._buttons.items():
                b.setChecked(False)
            if self._dropdown is not None:
                self._dropdown.setChecked(True)
            return
        # Fallback: clear all
        for _, b in self._buttons.items():
            b.setChecked(False)
        if self._dropdown is not None:
            self._dropdown.setChecked(False)

    @property
    def timeframes(self) -> tuple[str, ...]:
        """All timeframes (visible + overflow) in original order."""
        return self._all_timeframes

    def sizeHint(self) -> QSize:
        return QSize(480, 32)
