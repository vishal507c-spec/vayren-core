"""TradeOverlay — entry/exit markers, SL/TP and replay cursor on the chart.

Generic visual-ownership rule: a trade's entry/exit signal marker is painted
only when no strategy-owned MARKER PlotEvent already represents that bar and
the bar is not strategy-muted. The same backtest result carries all facts
(``trades`` for execution truth, ``chart_plots`` for strategy-owned visuals,
``muted_bars`` for intentionally visual-free bars), so coverage is derived
per ``set_result`` call — never per paint, never from strategy names, never
from the renderer. Connection lines (holding span + win/loss) always paint:
they carry different information than a signal point. Focused single-trade
mode always paints full detail: it is explicit user inspection, not ambient
signal visualization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF as _QPointF
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF

from backtest.models.result import StrategyResult
from backtest.models.trade import TradeRecord

if TYPE_CHECKING:
    # Annotation only (`from __future__ import annotations` is active).
    # At runtime the viewport is duck-typed (chart_rect/first/last/
    # price_low/price_high) against chart's ChartOverlay protocol, so there
    # is no runtime backtest → chart dependency.
    from chart.models.chart_viewport import ChartViewport

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


def _strategy_marker_bars(plots: Any) -> frozenset[int]:
    """Bars already owned by a strategy MARKER visual (generic, duck-typed).

    Accepts strategy ``PlotEvent`` objects or ``to_dict()`` dicts without
    importing the strategy module. Only live MARKER points count: other plot
    types (levels, zones, lines) and REMOVED/HIDDEN markers never suppress a
    trade signal. Malformed entries are skipped — coverage must never break
    ``set_result``.
    """
    covered: set[int] = set()
    try:
        items = tuple(plots or ())
    except TypeError:
        return frozenset()
    for plot in items:
        try:
            if isinstance(plot, dict):
                plot_type = plot.get("plot_type")
                bar = plot.get("bar_index")
                lifecycle = plot.get("lifecycle", "ACTIVE")
            else:
                plot_type = getattr(plot, "plot_type", None)
                bar = getattr(plot, "bar_index", None)
                lifecycle = getattr(plot, "lifecycle", "ACTIVE")
            if not isinstance(plot_type, str):
                plot_type = getattr(plot_type, "value", getattr(plot_type, "name", None))
            if str(plot_type or "").upper() != "MARKER":
                continue
            if not isinstance(lifecycle, str):
                lifecycle = getattr(lifecycle, "value", getattr(lifecycle, "name", "ACTIVE"))
            if str(lifecycle or "ACTIVE").upper() in ("REMOVED", "HIDDEN"):
                continue
            if isinstance(bar, bool) or not isinstance(bar, int) or bar < 0:
                continue
            covered.add(bar)
        except Exception:  # noqa: BLE001 — one bad plot never breaks coverage
            continue
    return frozenset(covered)


class TradeOverlay:
    """ChartOverlay that draws real backtest trades.

    Paints nothing when no result is active; call :meth:`set_result` after a
    backtest completes and :meth:`set_replay_index` during progress.

    Trade-context mode (Strategy Lab → Trade → Chart):
    ``set_focused_trade`` installs a *temporary* single-trade overlay. While
    focused, only that trade is rendered with precise ENTRY/EXIT markers
    (direction-aware labels + exact price). Previous focused context is
    automatically removed — overlays never accumulate.
    """

    def __init__(self) -> None:
        self._trades: tuple[TradeRecord, ...] = ()
        self._replay_index: int | None = None
        self._highlight_index: int | None = None
        self._focused_trade: TradeRecord | None = None
        self._focused_index: int | None = None
        # Bars whose signal visual is owned by a strategy PlotEvent (per result).
        self._covered_bars: frozenset[int] = frozenset()

    def set_result(self, result: StrategyResult | None) -> None:
        """Replace the trades shown on the chart."""
        self._trades = result.trades if result is not None else ()
        self._replay_index = None
        self._highlight_index = None
        # result change invalidates focused single-trade overlay
        self._focused_trade = None
        self._focused_index = None
        # Ownership is per result: this result's own strategy plots decide
        # which signal markers are already represented, plus strategy-declared
        # muted bars (intentionally visual-free bars, e.g. hidden SL exits).
        # Computed once here — never per paint, never from viewport state.
        plots = getattr(result, "chart_plots", ()) if result is not None else ()
        covered = set(_strategy_marker_bars(plots))
        muted = getattr(result, "muted_bars", ()) if result is not None else ()
        try:
            for bar in tuple(muted or ()):
                if isinstance(bar, int) and not isinstance(bar, bool) and bar >= 0:
                    covered.add(bar)
        except TypeError:
            pass
        self._covered_bars = frozenset(covered)

    def clear(self) -> None:
        """Remove all markers."""
        self._trades = ()
        self._replay_index = None
        self._highlight_index = None
        self._focused_trade = None
        self._focused_index = None
        self._covered_bars = frozenset()

    @property
    def covered_bars(self) -> frozenset[int]:
        """Bars with a strategy-owned signal visual (read-only, for tests)."""
        return self._covered_bars

    def set_replay_index(self, index: int | None) -> None:
        """Set the progress cursor position (None hides it)."""
        self._replay_index = index

    def set_highlight(self, trade_index: int | None) -> None:
        """Highlight one trade's connection line (legacy path)."""
        self._highlight_index = trade_index

    # ── focused trade-context ──────────────────────────────────

    def set_focused_trade(self, trade: TradeRecord | None, index: int | None = None) -> None:
        """Install a temporary single-trade context; replaces any previous one.

        When ``trade`` is None the focused overlay is cleared. ``index`` is the
        display row number (1-based trade id) when available — used only for
        potential future labeling, not for positioning.
        """
        self._focused_trade = trade
        self._focused_index = index
        # focused trade also drives highlight for backward compat
        self._highlight_index = index - 1 if index is not None and trade is not None else None

    def clear_focused(self) -> None:
        """Remove the temporary single-trade overlay."""
        self._focused_trade = None
        self._focused_index = None
        self._highlight_index = None

    @property
    def focused_trade(self) -> TradeRecord | None:
        return self._focused_trade

    @property
    def focused_index(self) -> int | None:
        return self._focused_index

    def paint_overlay(self, painter: QPainter, viewport: ChartViewport) -> None:
        """Draw entry/exit markers, SL/TP lines and replay cursor.

        When a focused trade is active only that trade is rendered (temporary
        context — previous trade's markers are removed). Otherwise all trades
        are rendered with highlight support.
        """
        chart_rect = QRectF(viewport.chart_rect)
        first, last = viewport.first, viewport.last
        low, high = viewport.price_low, viewport.price_high

        # Focused single-trade mode — temporary, non-accumulating
        if self._focused_trade is not None:
            trade = self._focused_trade
            visible = first <= trade.entry_index < last or first <= trade.exit_index < last
            if visible:
                x_entry = _bar_x(trade.entry_index, first, last, chart_rect)
                y_entry = _price_y(trade.entry_price, low, high, chart_rect)
                x_exit = _bar_x(trade.exit_index, first, last, chart_rect)
                y_exit = _price_y(trade.exit_price, low, high, chart_rect)

                conn_color = _CONNECTION_WIN if trade.winning else _CONNECTION_LOSS
                conn_color = QColor(conn_color)
                conn_color.setAlpha(230)
                pen = QPen(conn_color, 1.8, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.drawLine(int(x_entry), int(y_entry), int(x_exit), int(y_exit))

                side = (trade.side or "").upper()
                is_long = side == "LONG"
                entry_label = f"ENTRY {'LONG' if is_long else 'SHORT'}  ₹{trade.entry_price:,.2f}"
                exit_label = f"EXIT {'SELL' if is_long else 'COVER'}  ₹{trade.exit_price:,.2f}"
                self._paint_marker(painter, x_entry, y_entry, True, trade.winning, entry_label)
                self._paint_marker(painter, x_exit, y_exit, False, trade.winning, exit_label)

                # short direction arrow on connection midpoint
                if not is_long:
                    mx = (x_entry + x_exit) / 2.0
                    my = (y_entry + y_exit) / 2.0
                    self._paint_direction_arrow(painter, mx, my, x_entry < x_exit)

            # replay cursor still allowed even with focused trade
            if self._replay_index is not None and first <= self._replay_index < last:
                x = _bar_x(self._replay_index, first, last, chart_rect)
                pen = QPen(_REPLAY, 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(int(x), int(chart_rect.top()), int(x), int(chart_rect.bottom()))
            return

        if not self._trades and self._replay_index is None:
            return

        for idx, trade in enumerate(self._trades):
            visible = first <= trade.entry_index < last or first <= trade.exit_index < last
            if not visible:
                continue
            x_entry = _bar_x(trade.entry_index, first, last, chart_rect)
            y_entry = _price_y(trade.entry_price, low, high, chart_rect)
            x_exit = _bar_x(trade.exit_index, first, last, chart_rect)
            y_exit = _price_y(trade.exit_price, low, high, chart_rect)

            # connection line — holding span + win/loss: different information
            # than a signal point, so it always paints even when covered.
            conn_color = _CONNECTION_WIN if trade.winning else _CONNECTION_LOSS
            if idx == self._highlight_index:
                conn_color = QColor(conn_color)
                conn_color.setAlpha(230)
            pen = QPen(
                conn_color, 1.5 if idx == self._highlight_index else 1.0, Qt.PenStyle.DashLine
            )
            painter.setPen(pen)
            painter.drawLine(int(x_entry), int(y_entry), int(x_exit), int(y_exit))

            # Signal markers paint only when not already owned by a strategy
            # PlotEvent at the same bar — one event, one canonical visual.
            if trade.entry_index not in self._covered_bars:
                # entry marker (green up triangle + BUY label)
                self._paint_marker(painter, x_entry, y_entry, True, trade.winning, "BUY")
            if trade.exit_index not in self._covered_bars:
                # exit marker
                self._paint_marker(painter, x_exit, y_exit, False, trade.winning, "SELL")

        # replay cursor
        if self._replay_index is not None and first <= self._replay_index < last:
            x = _bar_x(self._replay_index, first, last, chart_rect)
            pen = QPen(_REPLAY, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(int(x), int(chart_rect.top()), int(x), int(chart_rect.bottom()))

    def _paint_direction_arrow(
        self, painter: QPainter, x: float, y: float, rightward: bool
    ) -> None:
        """Tiny direction chevron at midpoint for SHORT clarity."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = 4.0
        color = QColor("#cfd8dc")
        painter.setPen(QPen(color, 1.1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if rightward:
            pts = [(x - size, y - size * 0.6), (x + size * 0.4, y), (x - size, y + size * 0.6)]
        else:
            pts = [(x + size, y - size * 0.6), (x - size * 0.4, y), (x + size, y + size * 0.6)]
        for i in range(len(pts) - 1):
            painter.drawLine(int(pts[i][0]), int(pts[i][1]), int(pts[i + 1][0]), int(pts[i + 1][1]))
        painter.restore()

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
        # label pill — two-line aware (ENTRY LONG ₹price already includes price)
        font = QFont("Segoe UI", 7)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(label) + 10
        h = metrics.height() + 4
        bx = x - w / 2
        by = (y - size - h - 2) if is_entry else (y + size + 2)
        rect = QRectF(bx, by, w, h)
        painter.setBrush(QBrush(_LABEL_BG))
        painter.setPen(QPen(color, 1))
        painter.drawRoundedRect(rect, 2, 2)
        painter.setPen(_LABEL_TEXT)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()
