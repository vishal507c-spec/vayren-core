"""OverlayRenderer — stateless QPainter of floating chart UI labels.

Paints four overlay labels that follow the crosshair / chart state:

  1. Symbol info bar   — top-of-plot, single line (SYMBOL • timeframe • exchange)
  2. OHLC readout      — same top bar, single line (O H L C + change + %)
  3. Right price label — right scale, vertically aligned with crosshair
  4. Bottom time label — time-axis strip, horizontally centered at crosshair

All positions are relative to `plot_rect` (the chart's plot area, excluding
the time-axis strip and volume strip). The renderer owns no state and performs
no price math — it is handed a CrosshairValue or the raw Bar data.

Pure painting: no state, no SQL, no events.
"""

from datetime import datetime

from market.models.bar import Bar
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QFont, QPainter

from chart.models.crosshair_value import CrosshairValue
from chart.renderer.label_renderer import LabelRenderer


class OverlayRenderer:
    """Draws floating UI labels (symbol, OHLC, price, time) over the chart."""

    SYMBOL_FONT = QFont("Segoe UI", 9, QFont.Weight.Bold)
    OHLC_FONT = QFont("Segoe UI", 8)
    PRICE_FONT = QFont("Segoe UI", 8)
    TIME_FONT = QFont("Segoe UI", 8)

    @staticmethod
    def paint_symbol_info(
        painter: QPainter,
        symbol: str,
        timeframe: str,
        exchange: str,
        top_bar: QRect,
    ) -> QRect:
        """Paint the top-left symbol info label (SYMBOL • timeframe • exchange).

        Returns the pixel rect the label occupied so callers can place the
        OHLC readout to its right without overlap.
        """
        text = f"{symbol} \u2022 {timeframe} \u2022 {exchange}"
        return LabelRenderer.paint_left(
            painter,
            text,
            OverlayRenderer.SYMBOL_FONT,
            QRectF(top_bar),
        )

    @staticmethod
    def paint_ohlc(
        painter: QPainter,
        bar: Bar,
        top_bar: QRect,
        left_margin: int = 0,
    ) -> QRect:
        """Paint the OHLC readout on a single line in the top info bar.

        TradingView style: `O 100.50  H 105.00  L 95.00  C 102.00  +2.00 (+2.0%)`.
        `left_margin` is the right edge of the symbol info label so the OHLC
        bar starts just to its right.
        """
        change = bar.close - bar.open
        pct = bar.return_pct
        sign = "+" if change >= 0 else ""
        text = (
            f"O {bar.open:.2f}  H {bar.high:.2f}  "
            f"L {bar.low:.2f}  C {bar.close:.2f}  "
            f"{sign}{change:.2f} ({sign}{pct:.1f}%)"
        )
        position = QRectF(
            left_margin + 6,
            top_bar.top(),
            top_bar.width() - left_margin - 6,
            top_bar.height(),
        )
        return LabelRenderer.paint_left(
            painter,
            text,
            OverlayRenderer.OHLC_FONT,
            position,
        )

    @staticmethod
    def paint_price(
        painter: QPainter,
        value: CrosshairValue,
        crosshair_y: int,
        chart_rect: QRect,
    ) -> QRect:
        """Paint the right-side price label, vertically centered at crosshair_y."""
        text = f"{value.price:.2f}"
        label_h = 22
        y = crosshair_y - label_h / 2.0
        top = chart_rect.top()
        bottom = chart_rect.bottom() - label_h
        if y < top:
            y = top
        elif y > bottom:
            y = bottom
        position = QRectF(
            chart_rect.right() - 82,
            y,
            80,
            label_h,
        )
        return LabelRenderer.paint(
            painter,
            text,
            OverlayRenderer.PRICE_FONT,
            position,
        )

    @staticmethod
    def paint_time(
        painter: QPainter,
        value: CrosshairValue,
        timeframe: str,
        crosshair_x: int,
        axis_rect: QRect,
    ) -> QRect:
        """Paint the bottom time-axis label centered at `crosshair_x`.

        The label center is always the vertical crosshair's X position; when
        it would overflow the axis strip the label hugs the strip edge. All
        formats are single-line so the label resizes automatically.
        """
        formatted = OverlayRenderer._format_timestamp(value.timestamp, timeframe)
        return LabelRenderer.paint_centered(
            painter,
            formatted,
            OverlayRenderer.TIME_FONT,
            float(crosshair_x),
            float(axis_rect.top()),
            float(axis_rect.height()),
            bounds=QRectF(axis_rect),
        )

    @staticmethod
    def _format_timestamp(timestamp: str, timeframe: str = "1d") -> str:
        """Format an ISO timestamp as a single-line bottom label.

        Reads the exact timestamp from the candle — no estimation.
        """
        dt = OverlayRenderer._parse_ts(timestamp)
        if dt is None:
            return timestamp
        tf = timeframe.lower()
        date_part = dt.strftime("%a %d %b '%y")
        if tf.endswith("m") or tf.endswith("h"):
            return f"{date_part}  {dt.strftime('%H:%M')}"
        if tf.startswith("w") or tf == "1w":
            iso_cal = dt.isocalendar()
            week = iso_cal[1]
            year = dt.year
            if dt.month == 12 and dt.day >= 28 and week == 1:
                year = dt.year + 1
            return f"Week {week}  {year}"
        if tf.startswith("mo") or tf == "1mo":
            return dt.strftime("%b %Y")
        if tf.endswith("d"):
            return dt.strftime("%a %d %b '%y")
        return f"{date_part}  {dt.strftime('%H:%M')}"

    @staticmethod
    def _parse_ts(timestamp: str) -> datetime | None:
        """Parse a timestamp string into a datetime, or None if unparseable."""
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            return None
