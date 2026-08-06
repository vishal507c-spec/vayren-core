"""CandleRenderer — stateless QPainter painting of candlesticks."""

from market.models.bar import Bar
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

_GRID_LINES = 8
_LABEL_WIDTH = 72.0


class CandleRenderer:
    """Pure painting: grid, price labels, candle wicks/bodies, volume strip.

    Holds no state and no data logic — given a window of bars and a price
    range, it draws. Theme colors live here so widgets stay logic-free.
    """

    BACKGROUND = QColor("#101418")
    GRID = QColor("#232936")
    TEXT = QColor("#8a93a6")
    BULL = QColor("#26a69a")
    BEAR = QColor("#ef5350")
    VOLUME = QColor(38, 166, 154, 90)

    @staticmethod
    def paint(
        painter: QPainter,
        bars: tuple[Bar, ...],
        first: int,
        last: int,
        price_low: float,
        price_high: float,
        volume_max: int,
        chart_rect: QRect,
        volume_rect: QRect,
    ) -> None:
        """Draw the candle window. `first`/`last` are indices into `bars` (last exclusive)."""
        count = last - first
        if count <= 0 or chart_rect.isEmpty():
            return
        painter.save()
        painter.setFont(QFont("Segoe UI", 8))

        CandleRenderer._draw_grid(painter, chart_rect, price_low, price_high)

        slot_width = chart_rect.width() / count
        body_width = max(1.0, slot_width * 0.7)
        x_center = chart_rect.left() + slot_width / 2.0
        for index in range(first, last):
            bar = bars[index]
            center_x = x_center + slot_width * (index - first)
            color = CandleRenderer.BULL if bar.close >= bar.open else CandleRenderer.BEAR

            wick_y1 = CandleRenderer._y(bar.high, price_low, price_high, chart_rect)
            wick_y2 = CandleRenderer._y(bar.low, price_low, price_high, chart_rect)
            painter.setPen(QPen(color, 1))
            painter.drawLine(int(center_x), int(wick_y1), int(center_x), int(wick_y2))

            top = CandleRenderer._y(max(bar.open, bar.close), price_low, price_high, chart_rect)
            bottom = CandleRenderer._y(min(bar.open, bar.close), price_low, price_high, chart_rect)
            body = QRectF(center_x - body_width / 2.0, top, body_width, max(bottom - top, 1.0))
            painter.fillRect(body, color)

            volume_height = (
                volume_rect.height() * (bar.volume / volume_max) if volume_max > 0 else 0.0
            )
            volume = QRectF(
                center_x - body_width / 2.0,
                volume_rect.bottom() - volume_height,
                body_width,
                volume_height,
            )
            painter.fillRect(volume, CandleRenderer.VOLUME)

        painter.restore()

    @staticmethod
    def _draw_grid(
        painter: QPainter, chart_rect: QRect, price_low: float, price_high: float
    ) -> None:
        for index in range(_GRID_LINES + 1):
            y = chart_rect.top() + chart_rect.height() * index / _GRID_LINES
            painter.setPen(QPen(CandleRenderer.GRID, 1))
            painter.drawLine(chart_rect.left(), int(y), chart_rect.right(), int(y))
            price = price_high - (price_high - price_low) * index / _GRID_LINES
            painter.setPen(QPen(CandleRenderer.TEXT, 1))
            label = QRectF(chart_rect.right() - _LABEL_WIDTH, y - 7.0, _LABEL_WIDTH - 4.0, 14.0)
            painter.drawText(
                label, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{price:.2f}"
            )

    @staticmethod
    def _y(price: float, price_low: float, price_high: float, chart_rect: QRect) -> float:
        span = price_high - price_low
        if span <= 0.0:
            return chart_rect.top() + chart_rect.height() / 2.0
        return chart_rect.bottom() - (price - price_low) / span * chart_rect.height()
