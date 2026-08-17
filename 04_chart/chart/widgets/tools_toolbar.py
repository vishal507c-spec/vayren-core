"""ChartToolsToolbar — TradingView-style vertical chart tools rail.

A narrow fixed-width column of tool buttons pinned to the left edge of the
chart canvas. Buttons are organised into visual groups (view, measurement,
shapes, annotation, utility, settings) separated by hairline dividers, with
the settings group docked to the bottom.

Pure presentation: every button is a UI placeholder — toggling a button only
changes its checked state. Nothing here touches the chart model, candles,
coordinates, the bus or any chart logic; the widget exists solely as a visual
rail that later phases can wire real tools into.
"""

from collections.abc import Callable
from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chart.theme import ACCENT_TEXT, PLACEHOLDER, TEXT

TOOLBAR_WIDTH = 40
BUTTON_SIZE = 30
ICON_SIZE = 16

_MODE_TOOLS: tuple[tuple[str, str], ...] = (
    ("cursor", "Cursor"),
    ("crosshair", "Crosshair"),
)
_MEASURE_TOOLS: tuple[tuple[str, str], ...] = (
    ("horizontal_line", "Horizontal Line"),
    ("trend_line", "Trend Line"),
)
_SHAPE_TOOLS: tuple[tuple[str, str], ...] = (
    ("rectangle", "Rectangle"),
    ("ellipse", "Ellipse"),
    ("fibonacci", "Fib"),
)
_ANNOTATION_TOOLS: tuple[tuple[str, str], ...] = (
    ("text", "Text Note"),
    ("comment", "Comment"),
)
_UTILITY_TOOLS: tuple[tuple[str, str], ...] = (
    ("magnet", "Snap"),
    ("lock", "Lock Objects"),
)
_SETTINGS_TOOLS: tuple[tuple[str, str], ...] = (
    ("settings", "Settings"),
    ("help", "Help"),
)

_TOOLBAR_STYLE = """
ChartToolsToolbar {
    background: palette(alternate-base);
    border-right: 1px solid palette(midlight);
}
ChartToolsToolbar QToolButton {
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
    padding: 0px;
    border: none;
    border-radius: 4px;
    background: transparent;
}
ChartToolsToolbar QToolButton:hover {
    background: palette(midlight);
}
ChartToolsToolbar QToolButton:pressed {
    background: palette(mid);
}
ChartToolsToolbar QToolButton:checked {
    background: palette(highlight);
}
ChartToolsToolbar QToolButton:disabled {
    background: transparent;
}
ChartToolsToolbar QFrame#separator {
    background: palette(midlight);
    border: none;
    min-width: 1px;
    min-height: 1px;
    max-height: 1px;
}
"""


def _stroke(painter: QPainter, color: QColor, width: float = 1.2) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw_watchlist(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(3.0, 3.5, 3.4, 9.0), 1.0, 1.0)
    painter.drawLine(QPointF(8.5, 5.5), QPointF(13.5, 5.5))
    painter.drawLine(QPointF(8.5, 8.5), QPointF(13.5, 8.5))
    painter.drawLine(QPointF(8.5, 11.5), QPointF(13.5, 11.5))


def _draw_cursor(painter: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(1.5, 1.5)
    path.lineTo(1.5, 11.0)
    path.lineTo(3.9, 9.3)
    path.lineTo(5.5, 14.3)
    path.lineTo(7.4, 13.6)
    path.lineTo(5.8, 8.9)
    path.lineTo(9.3, 9.4)
    path.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)


def _draw_download(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawLine(QPointF(8.0, 3.5), QPointF(8.0, 8.2))
    painter.drawLine(QPointF(5.6, 6.8), QPointF(8.0, 9.2))
    painter.drawLine(QPointF(10.4, 6.8), QPointF(8.0, 9.2))
    painter.drawRoundedRect(QRectF(2.6, 10.6, 10.8, 2.6), 1.0, 1.0)


def _draw_crosshair(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawEllipse(QPointF(8.0, 8.0), 3.0, 3.0)
    painter.drawLine(QPointF(8.0, 1.5), QPointF(8.0, 3.4))
    painter.drawLine(QPointF(8.0, 12.6), QPointF(8.0, 14.5))
    painter.drawLine(QPointF(1.5, 8.0), QPointF(3.4, 8.0))
    painter.drawLine(QPointF(12.6, 8.0), QPointF(14.5, 8.0))


def _draw_horizontal_line(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawLine(QPointF(1.5, 8.0), QPointF(14.5, 8.0))


def _draw_trend_line(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawLine(QPointF(2.0, 13.0), QPointF(13.8, 4.2))
    painter.drawLine(QPointF(10.7, 3.2), QPointF(13.8, 4.2))
    painter.drawLine(QPointF(13.7, 7.4), QPointF(13.8, 4.2))


def _draw_rectangle(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(2.2, 3.5, 11.6, 9.5), 1.0, 1.0)


def _draw_ellipse(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawEllipse(QRectF(2.5, 4.0, 11.0, 8.5))


def _draw_fibonacci(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    path = QPainterPath()
    path.moveTo(1.5, 12.0)
    path.lineTo(5.5, 12.0)
    path.lineTo(5.5, 7.5)
    path.lineTo(9.5, 7.5)
    path.lineTo(9.5, 4.5)
    path.lineTo(13.5, 4.5)
    painter.drawPath(path)
    painter.drawLine(QPointF(10.4, 3.3), QPointF(13.5, 4.5))
    painter.drawLine(QPointF(13.4, 7.4), QPointF(13.5, 4.5))


def _draw_text(painter: QPainter, color: QColor) -> None:
    font = QFont("Segoe UI", 9)
    font.setBold(True)
    painter.setPen(color)
    painter.setFont(font)
    painter.drawText(QRectF(2.5, 2.0, 11.0, 12.0), Qt.AlignmentFlag.AlignCenter, "T")


def _draw_comment(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(2.0, 3.0, 12.0, 9.0), 2.0, 2.0)
    path = QPainterPath()
    path.moveTo(4.5, 12.0)
    path.lineTo(3.2, 14.5)
    path.lineTo(7.0, 12.0)
    path.closeSubpath()
    painter.drawPath(path)


def _draw_magnet(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(3.8, 1.5, 8.4, 4.6), 1.0, 1.0)
    painter.drawLine(QPointF(5.0, 6.1), QPointF(5.0, 11.0))
    painter.drawLine(QPointF(11.0, 6.1), QPointF(11.0, 11.0))
    painter.drawArc(QRectF(5.0, 8.0, 6.0, 6.0), 0, -180 * 16)


def _draw_lock(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawArc(QRectF(6.0, 1.5, 4.0, 5.0), 0, -180 * 16)
    painter.drawRoundedRect(QRectF(4.2, 6.0, 7.6, 8.0), 1.5, 1.5)
    painter.drawEllipse(QPointF(8.0, 9.4), 0.9, 0.9)
    painter.drawLine(QPointF(8.0, 10.2), QPointF(8.0, 12.2))


def _draw_settings(painter: QPainter, color: QColor) -> None:
    _stroke(painter, color)
    painter.drawEllipse(QPointF(8.0, 8.0), 2.6, 2.6)
    painter.drawEllipse(QPointF(8.0, 8.0), 1.0, 1.0)
    for i in range(8):
        angle = i * pi / 4.0
        x1 = 8.0 + 3.8 * cos(angle)
        y1 = 8.0 + 3.8 * sin(angle)
        x2 = 8.0 + 5.0 * cos(angle)
        y2 = 8.0 + 5.0 * sin(angle)
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _draw_help(painter: QPainter, color: QColor) -> None:
    font = QFont("Segoe UI", 10)
    font.setBold(True)
    painter.setPen(color)
    painter.setFont(font)
    painter.drawText(QRectF(2.5, 1.5, 11.0, 13.0), Qt.AlignmentFlag.AlignCenter, "?")


_DRAWERS: dict[str, Callable[[QPainter, QColor], None]] = {
    "watchlist": _draw_watchlist,
    "cursor": _draw_cursor,
    "download": _draw_download,
    "crosshair": _draw_crosshair,
    "horizontal_line": _draw_horizontal_line,
    "trend_line": _draw_trend_line,
    "rectangle": _draw_rectangle,
    "ellipse": _draw_ellipse,
    "fibonacci": _draw_fibonacci,
    "text": _draw_text,
    "comment": _draw_comment,
    "magnet": _draw_magnet,
    "lock": _draw_lock,
    "settings": _draw_settings,
    "help": _draw_help,
}

_ICON_CACHE: dict[str, QIcon] = {}


def _pixmap(kind: str, color: QColor) -> QPixmap:
    pixmap = QPixmap(32, 32)
    pixmap.setDevicePixelRatio(2.0)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2.0, 2.0)
    _DRAWERS[kind](painter, color)
    painter.end()
    return pixmap


def _icon(kind: str) -> QIcon:
    icon = _ICON_CACHE.get(kind)
    if icon is None:
        icon = QIcon()
        icon.addPixmap(_pixmap(kind, TEXT), QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(_pixmap(kind, ACCENT_TEXT), QIcon.Mode.Normal, QIcon.State.On)
        icon.addPixmap(_pixmap(kind, PLACEHOLDER), QIcon.Mode.Disabled, QIcon.State.Off)
        _ICON_CACHE[kind] = icon
    return icon


class ChartToolsToolbar(QWidget):
    """Vertical chart-tools rail pinned to the left edge of the chart canvas.

    The rail is the permanent navigation rail with EXACTLY TWO items: the
    first button (watchlist) opens/closes the watchlist side panel
    (``watchlist_clicked`` signal) and the second button (download)
    opens/closes the historical download side panel (``download_clicked``
    signal) — the window owns the single active-panel state and syncs the
    buttons' checked states. No other icons, no separators, no extra
    sections: a clean, quiet, institutional rail. The remaining tool-group
    definitions (``_MODE_TOOLS`` … ``_SETTINGS_TOOLS``, the mode group and
    their drawer functions) are retained below for later phases — nothing
    is rendered from them.

    Pure presentation: every button is a UI placeholder — toggling a button
    only changes its checked state. Nothing here touches the chart model,
    candles, coordinates, the bus or any chart logic; the widget exists
    solely as a visual rail that later phases can wire real tools into.
    """

    watchlist_clicked = Signal()
    download_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(TOOLBAR_WIDTH)
        self.setStyleSheet(_TOOLBAR_STYLE)

        self._buttons: list[QToolButton] = []
        self._buttons_by_kind: dict[str, QToolButton] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(5, 8, 5, 8)
        self._layout.setSpacing(2)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        self._watchlist_button = self._tool_button(
            "watchlist", "Watchlist (All Stocks)", checkable=True
        )
        self._watchlist_button.setChecked(True)
        self._watchlist_button.clicked.connect(self.watchlist_clicked)
        self._layout.addWidget(self._watchlist_button)

        self._download_button = self._tool_button("download", "Historical Download", checkable=True)
        self._download_button.clicked.connect(self.download_clicked)
        self._layout.addWidget(self._download_button)

        self._layout.addStretch(1)

    @property
    def buttons(self) -> tuple[QToolButton, ...]:
        """The tool buttons, top to bottom."""
        return tuple(self._buttons)

    @property
    def watchlist_button(self) -> QToolButton:
        """The navigation button for the watchlist side panel (first icon)."""
        return self._watchlist_button

    @property
    def download_button(self) -> QToolButton:
        """The navigation button for the historical download side panel."""
        return self._download_button

    def _tool_button(self, kind: str, tooltip: str, checkable: bool) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(_icon(kind))
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        button.setProperty("kind", kind)
        self._buttons.append(button)
        self._buttons_by_kind[kind] = button
        return button

    def _mode_button(self, kind: str, tooltip: str) -> QToolButton:
        button = self._tool_button(kind, tooltip, checkable=True)
        self._mode_group.addButton(button)
        return button

    def _separator(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("separator")
        frame.setFixedHeight(1)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return frame
