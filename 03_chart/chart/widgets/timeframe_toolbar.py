"""TimeframeToolbar — horizontal row of timeframe selector buttons."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget


class TimeframeToolbar(QWidget):
    """Shows the timeframes detected for the current symbol as buttons.

    A click emits ``timeframe_selected`` with the timeframe label. Pure UI:
    no bus, no SQL, no events — the window turns the signal into events.
    Labels come from the market-side detection (SQLite), never hardcoded.
    """

    timeframe_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

    def set_timeframes(self, timeframes: tuple[str, ...]) -> None:
        """Replace the buttons with one per detected timeframe."""
        self.clear()
        for timeframe in timeframes:
            button = QPushButton(timeframe, self)
            button.setCheckable(True)
            self._layout.addWidget(button)
            self._group.addButton(button)
            self._buttons[timeframe] = button
            button.clicked.connect(
                lambda _checked=False, tf=timeframe: self.timeframe_selected.emit(tf)
            )

    def clear(self) -> None:
        """Remove all timeframe buttons."""
        for _label, button in list(self._buttons.items()):
            self._group.removeButton(button)
            self._layout.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()

    def select_timeframe(self, timeframe: str) -> None:
        """Check the button matching `timeframe` (case-insensitive), if present."""
        target = None
        for label, button in self._buttons.items():
            if label.lower() == timeframe.lower():
                target = button
                break
        if target is not None:
            target.setChecked(True)

    @property
    def timeframes(self) -> tuple[str, ...]:
        """The currently displayed timeframe labels, in order."""
        return tuple(self._buttons.keys())
