"""TimeframeToolbar — horizontal row of timeframe selector buttons.

Full-width equal distribution with proper padding, responsive stretch and
active state. No fixed pixel coordinates — the layout expands to fill the
available container width and keeps the active button fully visible.
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)


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
        # Outer layout holds a horizontal scroll area so narrow containers scroll
        # rather than squash or clip the active button.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._scroll.setFixedHeight(32)

        container = QWidget(self._scroll)
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QHBoxLayout(container)
        # Proper right-side padding so last button never touches the edge
        self._layout.setContentsMargins(6, 3, 6, 3)
        self._layout.setSpacing(4)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._scroll.setWidget(container)
        outer.addWidget(self._scroll)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._separators: list[QFrame] = []

    def set_timeframes(self, timeframes: tuple[str, ...]) -> None:
        """Replace the buttons with one per detected timeframe."""
        self.clear()
        for index, timeframe in enumerate(timeframes):
            button = QPushButton(timeframe, self._scroll.widget())
            button.setCheckable(True)
            # Equal width: each button expands to share available space
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumWidth(52)
            button.setFixedHeight(24)
            # Ensure text vertically centered via stylesheet padding handled in theme
            self._layout.addWidget(button, 1)
            self._group.addButton(button)
            self._buttons[timeframe] = button
            button.clicked.connect(
                lambda _checked=False, tf=timeframe: self.timeframe_selected.emit(tf)
            )
            # Subtle vertical separator between buttons (except after last)
            if index < len(timeframes) - 1:
                sep = QFrame(self._scroll.widget())
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setFrameShadow(QFrame.Shadow.Plain)
                sep.setFixedWidth(1)
                sep.setFixedHeight(16)
                sep.setStyleSheet("color: palette(mid); background: palette(mid); border: none;")
                self._layout.addWidget(sep)
                self._separators.append(sep)

    def clear(self) -> None:
        """Remove all timeframe buttons."""
        for _label, button in list(self._buttons.items()):
            self._group.removeButton(button)
            self._layout.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()
        for sep in self._separators:
            self._layout.removeWidget(sep)
            sep.deleteLater()
        self._separators.clear()

    def select_timeframe(self, timeframe: str) -> None:
        """Check the button matching `timeframe` (case-insensitive), if present."""
        target = None
        for label, button in self._buttons.items():
            if label.lower() == timeframe.lower():
                target = button
                break
        if target is not None:
            target.setChecked(True)
            # Keep active fully visible even when scrolled
            self._scroll.ensureWidgetVisible(target, 8, 0)

    @property
    def timeframes(self) -> tuple[str, ...]:
        """The currently displayed timeframe labels, in order."""
        return tuple(self._buttons.keys())

    def sizeHint(self) -> QSize:
        return QSize(480, 32)
