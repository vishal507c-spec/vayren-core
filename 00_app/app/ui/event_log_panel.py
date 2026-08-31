"""EventLogPanel — compact live event log, fed by real bus events."""

from collections import deque
from datetime import datetime

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

_MAX_ENTRIES = 200
_LABEL_STYLE = "color: palette(placeholder-text); font-size: 10px; font-weight: 700;"

_LEVEL_COLOR = {
    "INFO": "#8a93a6",
    "WARN": "#d4a017",
    "ERROR": "#ef5350",
    "SUCCESS": "#26a69a",
}


class EventLogPanel(QWidget):
    """Scrollable log: each line is a real system event, bounded to 200 entries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        caption = QLabel("EVENT LOG", self)
        caption.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(caption)

        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(_MAX_ENTRIES)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setFont(QFont("Consolas", 8))
        self._view.setStyleSheet(
            "QPlainTextEdit { background: palette(base); border: 1px solid palette(midlight);"
            " border-radius: 3px; padding: 4px; }"
        )
        layout.addWidget(self._view, 1)

        self._entries: deque[str] = deque(maxlen=_MAX_ENTRIES)

    def add_entry(self, level: str, message: str) -> None:
        """Append one timestamped entry."""
        stamp = datetime.now().strftime("%H:%M:%S")
        color = _LEVEL_COLOR.get(level.upper(), "#8a93a6")
        html = (
            f'<span style="color:{color}">[{stamp}] {level.upper()}</span> {self._escape(message)}'
        )
        self._view.appendHtml(html)
        self._entries.append(f"[{stamp}] {level}: {message}")

    def clear_log(self) -> None:
        """Remove all entries."""
        self._view.clear()
        self._entries.clear()

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @property
    def entries(self) -> tuple[str, ...]:
        """Snapshot of stored entries (for tests)."""
        return tuple(self._entries)
