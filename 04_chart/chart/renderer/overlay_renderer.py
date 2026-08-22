"""OverlayRenderer — stateless QPainter of floating chart UI labels.

Paints two independent groups of labels:

  Permanent header strip (every paint, latest bar):
  1. Symbol info — top-of-plot, single line (SYMBOL • timeframe • exchange),
     two-tone: bright bold symbol, muted meta. Unframed — the strip
     background is drawn by the widget.
  2. OHLC readout — same top bar, single line (O H L C + change + %),
     muted field letters with light values; the change segment is tinted
     with the candle bull/bear accent.

  Crosshair-following (only while the crosshair is active):
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
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen

from chart.models.crosshair_value import CrosshairValue
from chart.renderer.candle_renderer import CandleRenderer
from chart.renderer.label_renderer import LabelRenderer


class OverlayRenderer:
    """Draws floating UI labels (symbol, OHLC, price, time) over the chart."""

    SYMBOL_FONT = QFont("Segoe UI", 9, QFont.Weight.Bold)
    META_FONT = QFont("Segoe UI", 8)
    OHLC_LABEL_FONT = QFont("Segoe UI", 8)
    OHLC_FONT = QFont("Segoe UI", 8)
    PRICE_FONT = QFont("Segoe UI", 8)
    TIME_FONT = QFont("Segoe UI", 8)

    STRIP_BG = QColor("#141922")
    STRIP_BORDER = QColor("#232936")
    SYMBOL_TEXT = QColor("#e8eef5")
    META_TEXT = QColor("#8a93a6")
    OHLC_LABEL_TEXT = QColor("#5d6778")
    OHLC_VALUE_TEXT = QColor("#b7c0cc")

    @staticmethod
    def paint_symbol_info(
        painter: QPainter,
        symbol: str,
        timeframe: str,
        exchange: str,
        top_bar: QRect,
    ) -> QRect:
        """Paint the top-left symbol info (SYMBOL • timeframe • exchange).

        Two-tone and unframed: the symbol is bright and bold, the trailing
        timeframe • exchange meta is muted; the strip background itself is
        drawn by the widget. Returns the pixel rect the text occupied so
        callers can place the OHLC readout to its right without overlap.

        Spacing is generous and responsive: ``   •   `` gaps keep the symbol
        and timeframe visually prominent and prevent edge-touch.
        """
        # Slightly larger gaps for prominence: 3 spaces each side of bullet
        bullet = "   \u2022   "
        segments = (
            (symbol, OverlayRenderer.SYMBOL_FONT, OverlayRenderer.SYMBOL_TEXT),
            (bullet, OverlayRenderer.META_FONT, OverlayRenderer.META_TEXT),
            (timeframe, OverlayRenderer.META_FONT, OverlayRenderer.META_TEXT),
            (bullet, OverlayRenderer.META_FONT, OverlayRenderer.META_TEXT),
            (exchange, OverlayRenderer.META_FONT, OverlayRenderer.META_TEXT),
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        metrics = QFontMetrics(OverlayRenderer.SYMBOL_FONT)
        baseline = top_bar.top() + (top_bar.height() + metrics.ascent() - metrics.descent()) / 2.0
        # Left padding 10 keeps text off the edge even when container is narrow
        x = top_bar.left() + 10
        for text, font, color in segments:
            painter.setFont(font)
            painter.setPen(QPen(color, 1))
            painter.drawText(int(x), int(baseline), text)
            x += painter.fontMetrics().horizontalAdvance(text)
        painter.restore()
        # Right padding 12 ensures symbol block never touches OHLC
        return QRect(
            top_bar.left() + 10,
            top_bar.top(),
            x - (top_bar.left() + 10),
            top_bar.height(),
        )

    @staticmethod
    def paint_ohlc(
        painter: QPainter,
        bar: Bar,
        top_bar: QRect,
        left_margin: int = 0,
    ) -> QRect:
        """Paint the OHLC readout on a single line in the top info strip.

        Institutional style: muted field letters with light values
        (`O 100.50  H 105.00  L 95.00  C 102.00`), then the change readout
        `+2.00 (+2.0%)` tinted with the candle bull/bear accent so the
        direction reads at a glance. Unframed — the strip background is
        drawn by the widget. `left_margin` is the right edge of the symbol
        info label so the OHLC bar starts just to its right.

        Consistent spacing: ``O <val>    H <val>    ...`` with 14px gaps,
        and a 16px lead-in before the change segment. Respects the right
        boundary — clips gracefully without overlapping the edge.
        """
        change = bar.close - bar.open
        pct = bar.return_pct
        sign = "+" if change >= 0 else ""
        fields = (
            ("O", f"{bar.open:.2f}"),
            ("H", f"{bar.high:.2f}"),
            ("L", f"{bar.low:.2f}"),
            ("C", f"{bar.close:.2f}"),
        )
        suffix = f"{sign}{change:.2f} ({sign}{pct:.1f}%)"
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        metrics = QFontMetrics(OverlayRenderer.OHLC_FONT)
        baseline = top_bar.top() + (top_bar.height() + metrics.ascent() - metrics.descent()) / 2.0
        # 14px gap after symbol block plus internal right-padding guard
        x = left_margin + 14
        # Guard: if OHLC would overflow the right edge, keep it visible but
        # never let it touch the border — reserve 10px right padding
        right_limit = top_bar.right() - 10
        for label, value in fields:
            # Estimate width for this field; skip if it would overflow
            painter.setFont(OverlayRenderer.OHLC_LABEL_FONT)
            w_label = painter.fontMetrics().horizontalAdvance(label) + 2
            painter.setFont(OverlayRenderer.OHLC_FONT)
            w_value = painter.fontMetrics().horizontalAdvance(value) + 14
            if (
                x + w_label + w_value > right_limit
                and value != fields[-1][1]
                and x + w_label + w_value - 14 > right_limit
            ):
                break
            painter.setFont(OverlayRenderer.OHLC_LABEL_FONT)
            painter.setPen(QPen(OverlayRenderer.OHLC_LABEL_TEXT, 1))
            painter.drawText(int(x), int(baseline), label)
            x += painter.fontMetrics().horizontalAdvance(label) + 3
            painter.setFont(OverlayRenderer.OHLC_FONT)
            painter.setPen(QPen(OverlayRenderer.OHLC_VALUE_TEXT, 1))
            painter.drawText(int(x), int(baseline), value)
            x += painter.fontMetrics().horizontalAdvance(value) + 14
        prefix_end = x - 14
        # Change segment with 16px breathing room, but still inside right_limit
        painter.setFont(OverlayRenderer.OHLC_FONT)
        w_suffix = painter.fontMetrics().horizontalAdvance(suffix)
        sx = x + 2
        if sx + w_suffix <= right_limit:
            painter.setPen(QPen(CandleRenderer.BULL if change >= 0 else CandleRenderer.BEAR, 1))
            painter.drawText(int(sx), int(baseline), suffix)
        painter.restore()
        return QRect(left_margin, top_bar.top(), prefix_end - left_margin, top_bar.height())

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
