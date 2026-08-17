"""SectionLabel — a compact section heading for the download console.

Consistent section headings across the console widgets (Configuration,
Download Plan, Download Status, Provider Status, Engine Log): small
uppercase caption with a subtle hairline beneath. Palette roles only,
so it follows the VAYREN dark theme.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

_STYLE = "color: palette(placeholder-text); font-size: 10px; font-weight: 700;"


class _SlimLabel(QLabel):
    """QLabel that never forces the side panel wider than its slot."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class SectionLabel(QWidget):
    """Uppercase caption with a hairline — the console's section heading."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        caption = _SlimLabel(title, self)
        caption.setStyleSheet(_STYLE)
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setStyleSheet("color: palette(midlight); background: palette(midlight);")
        line.setFixedHeight(1)
        layout.addWidget(caption)
        layout.addWidget(line)
