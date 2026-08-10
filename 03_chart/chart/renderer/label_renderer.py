"""LabelRenderer — stateless QPainter helper for framed overlay labels.

Draws a rounded-rectangle label with a semi-transparent background, a subtle
border, and text. Used by OverlayRenderer for the bottom time label, right
price label, symbol info bar and OHLC bar.

Pure painting: no state, no SQL, no events. Pens/brushes cached at class
level — nothing is allocated per paint call.
"""

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen


class LabelRenderer:
    """Paints fixed-size framed labels with single- or multi-line text.

    Alignment modes:
      - paint_right:    label's right edge at position.right()
      - paint_left:     label's left edge at position.left()
      - paint_centered: label horizontally centered at center_x, vertically
        placed within the strip [strip_top, strip_top + strip_height]
    """

    BACKGROUND = QColor(16, 20, 24, 220)
    BORDER = QColor(51, 57, 70)
    TEXT = QColor("#cfd8dc")

    _bg_brush = QBrush(BACKGROUND)
    _border_pen = QPen(BORDER, 1)
    _text_pen = QPen(TEXT)

    @staticmethod
    def paint(
        painter: QPainter,
        text: str,
        font: QFont,
        position: QRectF,
        padding: float = 6.0,
    ) -> QRect:
        """Paint `text` in a framed label right-aligned to `position.right()`.

        Returns the pixel rect the label occupied.
        """
        painter.save()
        fm = painter.fontMetrics()
        text_px = fm.horizontalAdvance(text)
        line_count = text.count("\n") + 1
        text_h = fm.height() * line_count
        label_w = text_px + 2 * padding
        label_h = text_h + 2 * padding

        x = position.right() - label_w
        y = position.top() + (position.height() - label_h) / 2.0
        if y < 0:
            y = position.top()

        rect = QRectF(x, y, label_w, label_h)
        LabelRenderer._draw_label(painter, rect, text, font, padding)
        painter.restore()
        return rect.toRect()

    @staticmethod
    def paint_left(
        painter: QPainter,
        text: str,
        font: QFont,
        position: QRectF,
        padding: float = 6.0,
    ) -> QRect:
        """Paint `text` in a framed label left-aligned to `position.left()`.

        Returns the pixel rect the label occupied.
        """
        painter.save()
        fm = painter.fontMetrics()
        text_px = fm.horizontalAdvance(text)
        line_count = text.count("\n") + 1
        text_h = fm.height() * line_count
        label_w = text_px + 2 * padding
        label_h = text_h + 2 * padding

        x = position.left()
        y = position.top() + (position.height() - label_h) / 2.0
        if y < 0:
            y = position.top()

        rect = QRectF(x, y, label_w, label_h)
        LabelRenderer._draw_label(painter, rect, text, font, padding)
        painter.restore()
        return rect.toRect()

    @staticmethod
    def paint_centered(
        painter: QPainter,
        text: str,
        font: QFont,
        center_x: float,
        strip_top: float,
        strip_height: float,
        padding: float = 6.0,
        bounds: QRectF | None = None,
    ) -> QRect:
        """Paint `text` centered horizontally at `center_x`.

        The label is vertically placed within the strip
        `[strip_top, strip_top + strip_height]`, centered if it fits.
        When `bounds` is given the label is clamped inside it (center stays
        as close to `center_x` as the bounds allow).
        Supports multi-line text (newline-separated).
        Returns the pixel rect the label occupied.
        """
        painter.save()
        fm = painter.fontMetrics()
        text_px = fm.horizontalAdvance(text)
        line_count = text.count("\n") + 1
        text_h = fm.height() * line_count
        label_w = text_px + 2 * padding
        label_h = text_h + 2 * padding

        x = center_x - label_w / 2.0
        y = strip_top + (strip_height - label_h) / 2.0
        if y < 0:
            y = strip_top
        if bounds is not None:
            if x < bounds.left():
                x = bounds.left()
            right = bounds.right() - label_w
            if x > right:
                x = max(right, bounds.left())

        rect = QRectF(x, y, label_w, label_h)
        LabelRenderer._draw_label(painter, rect, text, font, padding)
        painter.restore()
        return rect.toRect()

    @staticmethod
    def _draw_label(
        painter: QPainter,
        rect: QRectF,
        text: str,
        font: QFont,
        padding: float,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setBrush(LabelRenderer._bg_brush)
        painter.setPen(LabelRenderer._border_pen)
        painter.drawRoundedRect(rect, 3.0, 3.0)
        painter.setPen(LabelRenderer._text_pen)
        painter.setFont(font)
        text_rect = QRectF(
            rect.left() + padding,
            rect.top() + padding,
            rect.width() - 2 * padding,
            rect.height() - 2 * padding,
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            text,
        )
