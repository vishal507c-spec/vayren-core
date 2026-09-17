"""LabelRenderer — stateless QPainter helper for overlay labels.

Two label styles:

- Framed labels (rounded rectangle, semi-transparent background, subtle
  border): used by OverlayRenderer for the right price label and the OHLC
  bar (``paint`` / ``paint_left``).
- Flat coordinate tag (solid teal fill, dark centered text, square corners,
  no border): the crosshair bottom time label (``paint_centered``).

Pure painting: no state, no SQL, no events. Pens/brushes cached at class
level — nothing is allocated per paint call.
"""

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen


class LabelRenderer:
    """Paints fixed-size overlay labels with single- or multi-line text.

    Alignment modes:
      - paint_right:    label's right edge at position.right()
      - paint_left:     label's left edge at position.left()
      - paint_centered: flat teal time-axis tag, horizontally centered at
        center_x, vertically placed within the strip
        [strip_top, strip_top + strip_height]
    """

    BACKGROUND = QColor(16, 20, 24, 220)
    BORDER = QColor(51, 57, 70)
    TEXT = QColor("#cfd8dc")

    # TradingView-style coordinate tag for the crosshair time label:
    # solid cyan/teal fill, dark centered text, square corners, no border.
    TAG_BACKGROUND = QColor("#26a69a")
    TAG_TEXT = QColor("#0b0f13")
    TAG_MIN_HEIGHT = 16.0
    TAG_VPAD = 2.0

    _bg_brush = QBrush(BACKGROUND)
    _border_pen = QPen(BORDER, 1)
    _text_pen = QPen(TEXT)
    _tag_bg_brush = QBrush(TAG_BACKGROUND)
    _tag_text_pen = QPen(TAG_TEXT, 1)

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
        text_color: QColor | None = None,
    ) -> QRect:
        """Paint `text` in a framed label left-aligned to `position.left()`.

        `text_color` overrides the default label text color (used to accent
        the OHLC change readout). Returns the pixel rect the label occupied.
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
        LabelRenderer._draw_label(painter, rect, text, font, padding, text_color)
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
        """Paint `text` as a flat teal tag centered horizontally at `center_x`.

        TradingView-style time-axis coordinate tag: a solid cyan/teal
        rectangle with dark centered text — square corners, no border, no
        rounded corners, compact fixed height. The tag is vertically centered
        within the strip [strip_top, strip_top + strip_height]. When `bounds`
        is given the tag is clamped inside it (center stays as close to
        `center_x` as the bounds allow, keeping the whole tag visible near the
        chart edges). Supports multi-line text (newline-separated).
        Returns the pixel rect the tag occupied.
        """
        painter.save()
        painter.setFont(font)
        fm = painter.fontMetrics()
        line_count = text.count("\n") + 1
        text_h = fm.height() * line_count
        label_w = fm.horizontalAdvance(text) + 2 * padding
        label_h = max(text_h + 2 * LabelRenderer.TAG_VPAD, LabelRenderer.TAG_MIN_HEIGHT)

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(LabelRenderer._tag_bg_brush)
        painter.drawRect(rect)
        painter.setPen(LabelRenderer._tag_text_pen)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
        return rect.toRect()

    @staticmethod
    def _draw_label(
        painter: QPainter,
        rect: QRectF,
        text: str,
        font: QFont,
        padding: float,
        text_color: QColor | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setBrush(LabelRenderer._bg_brush)
        painter.setPen(LabelRenderer._border_pen)
        painter.drawRoundedRect(rect, 3.0, 3.0)
        painter.setPen(LabelRenderer._text_pen if text_color is None else QPen(text_color, 1))
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
