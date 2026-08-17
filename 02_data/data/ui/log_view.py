"""LogPanel — collapsible engine log with a clear action.

The terminal-style engine log is tucked away by default so it can never
consume most of the panel height; expanding shows a height-capped log.
LogView stays the same read-only buffer used by the rest of the console.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_LOG_MAX_HEIGHT = 150


class LogView(QPlainTextEdit):
    """Read-only log of engine status messages and event lines."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(2000)
        self.setPlaceholderText("Engine log — no output yet.")

    def append_line(self, line: str) -> None:
        self.appendPlainText(line)


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


class LogPanel(QWidget):
    """Collapsible engine log: header row (toggle + Clear Log) + LogView."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(4)
        self._toggle = _SlimButton("⌄", self)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setStyleSheet(
            "color: palette(placeholder-text); background: transparent; border: none; "
            "padding: 0 2px; font-size: 10px;"
        )
        self._toggle.clicked.connect(self._on_toggle)
        title = _SlimLabel("ENGINE LOG", self)
        title.setStyleSheet("color: palette(placeholder-text); font-size: 10px; font-weight: 700;")
        self._clear_button = _SlimButton("Clear Log", self)
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.setStyleSheet(
            "color: palette(placeholder-text); background: transparent; border: none; "
            "padding: 0 2px; font-size: 10px;"
        )
        self._clear_button.clicked.connect(self.clear)
        header.addWidget(self._toggle)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._clear_button)
        layout.addLayout(header)

        self._log = LogView(self)
        self._log.setMaximumHeight(_LOG_MAX_HEIGHT)
        self._log.hide()
        layout.addWidget(self._log)

    def _on_toggle(self) -> None:
        self._expanded = not self._expanded
        self._log.setVisible(self._expanded)
        self._toggle.setText("⌃" if self._expanded else "⌄")

    @property
    def log(self) -> LogView:
        return self._log

    @property
    def expanded(self) -> bool:
        return self._expanded

    def clear(self) -> None:
        self._log.clear()
