"""OptionsPanel — narrow fixed-width column, far-left of the window.

Hosts two placeholder tool buttons (indicator, drawing) for future options
tooling. Pure UI — no bus, no SQL, no events.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget


class OptionsPanel(QWidget):
    """The far-left panel of the window splitter (options | watchlist | chart).

    Contains exactly two small placeholder icon buttons, stacked vertically.
    They are UI placeholders only — disabled, no connections, no menus — so
    they cannot be clicked. Options tooling lands here in later phases.
    """

    OPTIONS_WIDTH = 56
    BUTTON_SIZE = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(self.OPTIONS_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 0)
        layout.setSpacing(8)

        self._button_top = self._placeholder_button("◉")
        self._button_bottom = self._placeholder_button("◇")
        layout.addWidget(self._button_top, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._button_bottom, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def _placeholder_button(self, glyph: str) -> QToolButton:
        button = QToolButton(self)
        button.setText(glyph)
        button.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
        button.setEnabled(False)
        return button
