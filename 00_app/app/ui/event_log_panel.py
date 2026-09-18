"""EventLogPanel — compact live event log, fed by real bus events."""

from collections import deque
from datetime import datetime

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from app.ui import lab_theme as t

_MAX_ENTRIES = 200
_LABEL_STYLE = t.label()
_FONT_CODE = "Consolas"  # lab_theme FONT_CODE stack head; 8pt ≈ FS_LABEL

_LEVEL_COLOR = {
    "INFO": t.TEXT2,
    "WARN": t.WARN,
    "ERROR": t.NEG,
    "SUCCESS": t.POS,
}


class EventLogPanel(QWidget):
    """Scrollable log: each line is a real system event, bounded to 200 entries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.SP_MD, t.SP_SM, t.SP_MD, t.SP_SM)
        layout.setSpacing(t.SP_SM)

        caption = QLabel("EVENT LOG", self)
        caption.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(caption)

        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(_MAX_ENTRIES)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setFont(QFont(_FONT_CODE, 8))
        self._view.setStyleSheet(
            f"QPlainTextEdit {{ background: {t.BG1}; border: 1px solid {t.BORDER};"
            f" border-radius: {t.RADIUS_SM}px; padding: {t.SP_SM}px; color: {t.TEXT};"
            f" font-size: {t.FS_TABLE}px;"
            f" selection-background-color: {t.ACCENT_DIM}; }}"
        )
        layout.addWidget(self._view, 1)

        self._entries: deque[str] = deque(maxlen=_MAX_ENTRIES)

    def add_entry(self, level: str, message: str) -> None:
        """Append one timestamped entry."""
        stamp = datetime.now().strftime("%H:%M:%S")
        color = _LEVEL_COLOR.get(level.upper(), t.TEXT2)
        html = (
            f'<span style="color:{color}">[{stamp}] {level.upper()}</span> {self._escape(message)}'
        )
        self._view.appendHtml(html)
        self._entries.append(f"[{stamp}] {level}: {message}")

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @property
    def entries(self) -> tuple[str, ...]:
        """Snapshot of stored entries (for tests)."""
        return tuple(self._entries)
