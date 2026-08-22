"""TradeOverlay — entry/exit markers, SL/TP and replay cursor on the chart."""

from __future__ import annotations

from chart.models.chart_viewport import ChartViewport
from PySide6.QtCore import QPointF as _QPointF
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF

from backtest.models.result import StrategyResult
from backtest.models.trade import TradeRecord

_BULL = QColor("#26a69a")
_BEAR = QColor("#ef5350")
_SL = QColor("#ef5350")
_TP = QColor("#26a69a")
_CONNECTION_WIN = QColor(38, 166, 154, 140)
_CONNECTION_LOSS = QColor(239, 83, 80, 140)
_REPLAY = QColor(255, 193, 7, 180)
_LABEL_BG = QColor("#1f2632")
_LABEL_TEXT = QColor("#cfd8dc")


def _price_y(price: float, low: float, high: float, rect: QRectF) -> float:
    span = high - low
    if span <= 0:
        return rect.center().y()
    frac = (price - low) / span
    return rect.bottom() - frac * rect.height()


def _bar_x(index: int, first: int, last: int, rect: QRectF) -> float:
    count = max(1, last - first)
    return rect.left() + (index - first + 0.5) / count * rect.width()


class TradeOverlay:
    """ChartOverlay that draws real backtest trades.

    Paints nothing when no result is active; call :meth:`set_result` after a
    backtest completes and :meth:`set_replay_index` during progress.
    """

    def __init__(self) -> None:
        self._trades: tuple[TradeRecord, ...] = ()
        self._replay_index: int | None = None
        self._highlight_index: int | None = None

    def set_result(self, result: StrategyResult | None) -> None:
        """Replace the trades shown on the chart."""
        self._trades = result.trades if result is not None else ()
        self._replay_index = None
        self._highlight_index = None

    def clear(self) -> None:
        """Remove all markers."""
        self._trades = ()
        self._replay_index = None
        self._highlight_index = None

    def set_replay_index(self, index: int | None) -> None:
        """Set the progress cursor position (None hides it)."""
        self._replay_index = index

    def set_highlight(self, trade_index: int | None) -> None:
        """Highlight one trade's connection line."""
        self._highlight_index = trade_index

    def paint_overlay(self, painter: QPainter, viewport: ChartViewport) -> None:
        """Draw entry/exit markers, SL/TP lines and replay cursor."""
        if not self._trades and self._replay_index is None:
            return
        chart_rect = QRectF(viewport.chart_rect)
        first, last = viewport.first, viewport.last
        low, high = viewport.price_low, viewport.price_high

        for idx, trade in enumerate(self._trades):
            visible = first <= trade.entry_index < last or first <= trade.exit_index < last
            if not visible:
                continue
            x_entry = _bar_x(trade.entry_index, first, last, chart_rect)
            y_entry = _price_y(trade.entry_price, low, high, chart_rect)
            x_exit = _bar_x(trade.exit_index, first, last, chart_rect)
            y_exit = _price_y(trade.exit_price, low, high, chart_rect)

            # connection line
            conn_color = _CONNECTION_WIN if trade.winning else _CONNECTION_LOSS
            if idx == self._highlight_index:
                conn_color = QColor(conn_color)
                conn_color.setAlpha(230)
            pen = QPen(
                conn_color, 1.5 if idx == self._highlight_index else 1.0, Qt.PenStyle.DashLine
            )
            painter.setPen(pen)
            painter.drawLine(int(x_entry), int(y_entry), int(x_exit), int(y_exit))

            # entry marker (green up triangle + BUY label)
            self._paint_marker(painter, x_entry, y_entry, True, trade.winning, "BUY")
            # exit marker
            self._paint_marker(painter, x_exit, y_exit, False, trade.winning, "SELL")

        # replay cursor
        if self._replay_index is not None and first <= self._replay_index < last:
            x = _bar_x(self._replay_index, first, last, chart_rect)
            pen = QPen(_REPLAY, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(int(x), int(chart_rect.top()), int(x), int(chart_rect.bottom()))

    def _paint_marker(
        self,
        painter: QPainter,
        x: float,
        y: float,
        is_entry: bool,
        winning: bool,
        label: str,
    ) -> None:
        color = _BULL if winning else _BEAR
        if is_entry:
            color = _BULL
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # triangle
        size = 7
        if is_entry:
            points = [
                (x, y - size),
                (x - size * 0.6, y + size * 0.5),
                (x + size * 0.6, y + size * 0.5),
            ]
        else:
            points = [
                (x, y + size),
                (x - size * 0.6, y - size * 0.5),
                (x + size * 0.6, y - size * 0.5),
            ]
        poly = QPolygonF([_QPointF(px, py) for px, py in points])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(poly)
        # label pill
        font = QFont("Segoe UI", 7)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(label) + 6
        h = metrics.height() + 2
        bx = x - w / 2
        by = (y - size - h - 2) if is_entry else (y + size + 2)
        rect = QRectF(bx, by, w, h)
        painter.setBrush(QBrush(_LABEL_BG))
        painter.setPen(QPen(color, 1))
        painter.drawRoundedRect(rect, 2, 2)
        painter.setPen(_LABEL_TEXT)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()
