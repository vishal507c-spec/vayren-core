"""CandleRenderer — stateless QPainter painting of candlesticks."""

from market.models.bar import Bar
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen

_GRID_LINES = 8
_LABEL_WIDTH = 72.0

_GRID = QColor("#232936")
_TEXT = QColor("#8a93a6")


class CandleRenderer:
    """Pure painting: grid, price labels, candle wicks/bodies, volume strip.

    Holds no state and no data logic — given a window of bars and a price
    range, it draws. Theme colors live here so widgets stay logic-free.
    Pens and brushes are cached at class level: nothing is allocated per
    paint call.
    """

    BACKGROUND = QColor("#101418")
    GRID = _GRID
    TEXT = _TEXT
    BULL = QColor("#26a69a")
    BEAR = QColor("#ef5350")
    VOLUME = QColor(38, 166, 154, 90)
    VOLUME_BULL = QColor(38, 166, 154, 110)
    VOLUME_BEAR = QColor(239, 83, 80, 110)

    _FONT = QFont("Segoe UI", 8)
    _VOLUME_LABEL_FONT = QFont("Segoe UI", 7)
    _VOLUME_LABEL_FONT.setWeight(QFont.Weight.DemiBold)
    _grid_pen = QPen(_GRID, 1)
    _text_pen = QPen(_TEXT, 1)
    _bull_pen = QPen(BULL, 1)
    _bear_pen = QPen(BEAR, 1)
    _volume_brush = QBrush(VOLUME)
    _volume_bull_brush = QBrush(VOLUME_BULL)
    _volume_bear_brush = QBrush(VOLUME_BEAR)
    _volume_label_brush_bull = QBrush(BULL)
    _volume_label_brush_bear = QBrush(BEAR)
    _volume_label_text_pen = QPen(QColor("#ffffff"), 1)

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
        """Draw the full candle view: grid + price labels + wicks + bodies + volume.

        `first` is the leftmost window slot, `last` the rightmost slot
        (may exceed `len(bars)` to leave a right-side margin). Bars are
        clamped internally so the empty margin stays empty.
        """
        if chart_rect.isEmpty():
            return
        CandleRenderer.paint_grid(painter, chart_rect, price_low, price_high)
        CandleRenderer.paint_bars(
            painter,
            bars,
            first,
            last,
            price_low,
            price_high,
            volume_max,
            chart_rect,
            volume_rect,
        )

    @staticmethod
    def paint_grid(
        painter: QPainter, chart_rect: QRect, price_low: float, price_high: float
    ) -> None:
        """Draw the static furniture: horizontal gridlines + right price labels.

        Cached by the widget into a pixmap between viewport changes.
        """
        if chart_rect.isEmpty():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(CandleRenderer._FONT)
        for index in range(_GRID_LINES + 1):
            y = chart_rect.top() + chart_rect.height() * index / _GRID_LINES
            painter.setPen(CandleRenderer._grid_pen)
            painter.drawLine(chart_rect.left(), int(y), chart_rect.right(), int(y))
            price = price_high - (price_high - price_low) * index / _GRID_LINES
            painter.setPen(CandleRenderer._text_pen)
            label = QRectF(chart_rect.right() - _LABEL_WIDTH, y - 7.0, _LABEL_WIDTH - 4.0, 14.0)
            painter.drawText(
                label,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{price:.2f}",
            )
        painter.restore()

    @staticmethod
    def paint_bars(
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
        """Draw the data layer: candle wicks/bodies and the volume strip.

        `last` is the right window slot (may exceed `len(bars)`); bars are
        clamped so the margin beyond the data stays empty.
        """
        count = last - first
        bar_last = min(last, len(bars))
        if count <= 0 or bar_last <= first or chart_rect.isEmpty():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setFont(CandleRenderer._FONT)

        slot_width = chart_rect.width() / count
        body_width = max(1.0, slot_width * 0.7)
        for index in range(first, bar_last):
            bar = bars[index]
            center_x = chart_rect.left() + slot_width * (index - first + 0.5)
            color = CandleRenderer.BULL if bar.close >= bar.open else CandleRenderer.BEAR
            pen = (
                CandleRenderer._bull_pen
                if color is CandleRenderer.BULL
                else CandleRenderer._bear_pen
            )

            wick_y1 = CandleRenderer._y(bar.high, price_low, price_high, chart_rect)
            wick_y2 = CandleRenderer._y(bar.low, price_low, price_high, chart_rect)
            painter.setPen(pen)
            painter.drawLine(int(center_x), int(wick_y1), int(center_x), int(wick_y2))

            top = CandleRenderer._y(max(bar.open, bar.close), price_low, price_high, chart_rect)
            bottom = CandleRenderer._y(min(bar.open, bar.close), price_low, price_high, chart_rect)
            body = QRectF(center_x - body_width / 2.0, top, body_width, max(bottom - top, 1.0))
            painter.fillRect(body, color)

            # TradingView-style volume: same x as candle, height proportional to volume_max,
            # color follows candle direction (green for bull, red for bear), aligned to time scale
            is_bull = bar.close >= bar.open
            volume_height = (
                volume_rect.height() * (bar.volume / volume_max) if volume_max > 0 else 0.0
            )
            # Clamp to avoid 1px gap at top when volume_max is outlier; keep natural resize
            volume_height = max(1.0, volume_height) if bar.volume > 0 else 0.0
            if volume_height > volume_rect.height():
                volume_height = float(volume_rect.height())
            volume = QRectF(
                center_x - body_width / 2.0,
                volume_rect.bottom() - volume_height,
                body_width,
                volume_height,
            )
            vol_brush = (
                CandleRenderer._volume_bull_brush if is_bull else CandleRenderer._volume_bear_brush
            )
            painter.fillRect(volume, vol_brush)

        painter.restore()

    @staticmethod
    def format_volume(volume: int) -> str:
        """TradingView-style compact volume formatting.

        950 -> "950", 8020 -> "8.02 K", 1250000 -> "1.25 M", 2500000000 -> "2.50 B"
        """
        v = int(volume)
        if v >= 1_000_000_000:
            return f"{v / 1_000_000_000:.2f} B"
        if v >= 1_000_000:
            return f"{v / 1_000_000:.2f} M"
        if v >= 1_000:
            return f"{v / 1_000:.2f} K"
        return str(v)

    @staticmethod
    def paint_volume_value(
        painter: QPainter,
        volume: int,
        is_bull: bool,
        volume_max: int,
        volume_rect: QRect,
    ) -> QRect:
        """Paint latest volume value label at right edge of volume pane.

        Compact rounded-rectangle, right-aligned, vertically centered at
        latest volume level, green/red per bar direction (TradingView exact).
        No-ops when volume_max is 0 or rect is empty.
        """
        if volume_rect.isEmpty() or volume_max <= 0:
            return QRect()
        text = CandleRenderer.format_volume(volume)
        # volume height for latest bar
        volume_height = volume_rect.height() * (volume / volume_max) if volume_max > 0 else 0.0
        volume_height = max(0.0, min(volume_height, float(volume_rect.height())))
        # y at top of volume bar
        y_top = volume_rect.bottom() - volume_height
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(CandleRenderer._VOLUME_LABEL_FONT)
        fm = painter.fontMetrics()
        pad_h = 6
        pad_v = 3
        text_w = fm.horizontalAdvance(text)
        text_h = fm.height()
        label_w = text_w + 2 * pad_h
        label_h = text_h + 2 * pad_v
        # clamp label height to volume_rect height
        label_h = min(label_h, volume_rect.height())
        # right edge aligned, 2px inset from right
        x = volume_rect.right() - label_w - 2
        # vertically centered at y_top, clamped inside volume_rect
        y = y_top - label_h / 2.0
        if y < volume_rect.top():
            y = float(volume_rect.top())
        if y + label_h > volume_rect.bottom():
            y = float(volume_rect.bottom() - label_h)
        rect = QRectF(x, y, label_w, label_h)
        brush = (
            CandleRenderer._volume_label_brush_bull
            if is_bull
            else CandleRenderer._volume_label_brush_bear
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(brush)
        painter.drawRoundedRect(rect, 3.0, 3.0)
        painter.setPen(CandleRenderer._volume_label_text_pen)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
        return rect.toRect()

    @staticmethod
    def _y(price: float, price_low: float, price_high: float, chart_rect: QRect) -> float:
        span = price_high - price_low
        if span <= 0.0:
            return chart_rect.top() + chart_rect.height() / 2.0
        return chart_rect.bottom() - (price - price_low) / span * chart_rect.height()
