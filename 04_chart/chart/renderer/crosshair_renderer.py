"""CrosshairRenderer — stateless QPainter drawing of the mouse crosshair.

Draws one vertical line (full plot height) and one horizontal line (full
plot width) crossing at the mouse position. Thin, semi-transparent gray.
Pens are cached at class level — nothing is allocated per paint call.
"""

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPainter, QPen


class CrosshairRenderer:
    """Draws the crosshair after the candles. No state, no events."""

    LINE_COLOR = QColor(180, 180, 180, 120)
    LINE_WIDTH = 1

    _pen = QPen(LINE_COLOR, LINE_WIDTH)

    @staticmethod
    def paint(painter: QPainter, position: QPoint, plot_rect: QRect) -> None:
        """Draw vertical + horizontal crosshair lines through `position`.

        The vertical line spans the full plot height, the horizontal line
        the full plot width. Lines stay inside `plot_rect`.
        """
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(CrosshairRenderer._pen)
        painter.drawLine(position.x(), plot_rect.top(), position.x(), plot_rect.bottom())
        painter.drawLine(plot_rect.left(), position.y(), plot_rect.right(), position.y())
        painter.restore()
