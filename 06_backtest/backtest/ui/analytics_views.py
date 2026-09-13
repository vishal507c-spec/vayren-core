"""Analytics views — seven independent tab contents for the tester panel."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backtest.models.result import StrategyResult

_MUTED = "color: palette(placeholder-text); font-size: 11px;"
_VALUE = "color: palette(text); font-size: 11px; font-weight: 500;"
_HEADER = "color: palette(placeholder-text); font-size: 10px; font-weight: 700;"
_CHART_BG = QColor("#101418")
_GRID = QColor("#232936")
_BULL = QColor("#26a69a")
_BEAR = QColor("#ef5350")
_TEXT = QColor("#8a93a6")
_EQUITY = QColor("#42a5f5")
_DD = QColor("#ef5350")


def decimate_envelope(values: list[float], max_points: int) -> list[float]:
    """Downsample to ≤ ``max_points`` preserving the visual envelope.

    Each pixel bucket keeps its (min, max) so spikes never vanish — at the
    rendered resolution the polyline is indistinguishable from the full
    curve, while a 193k-point equity curve paints in milliseconds instead
    of seconds. Input data is never modified.
    """
    n = len(values)
    if n <= max_points or max_points < 4:
        return list(values)
    out: list[float] = []
    stride = n / max_points
    for b in range(max_points):
        start = int(b * stride)
        stop = max(start + 1, int((b + 1) * stride))
        chunk = values[start:stop]
        out.append(min(chunk))
        out.append(max(chunk))
    return out


class _ChartView(QWidget):
    """Base: override :meth:`paintEvent` to draw on :attr:`result`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: StrategyResult | None = None
        self.setMinimumHeight(160)

    def set_result(self, result: StrategyResult | None) -> None:
        """Update the backing result and repaint."""
        self._result = result
        self.update()


_AXIS = QColor("#5b6878")
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 68, 18, 16, 26


def _compact_axis_money(value: float) -> str:
    """Axis tick text — compact, never a wall of digits."""
    amount = abs(float(value))
    sign = "-" if value < 0 else ""
    if amount >= 1e7:
        return f"{sign}{amount / 1e7:.1f}Cr"
    if amount >= 1e5:
        return f"{sign}{amount / 1e5:.2f}L"
    if amount >= 1e3:
        return f"{sign}{amount / 1e3:.0f}K"
    return f"{sign}{amount:.0f}"


def _short_stamp(stamp: str) -> str:
    """'2026-01-02 09:15:00' → '02 Jan 26' (best effort, never raises)."""
    text = (stamp or "")[:10]
    try:
        from datetime import datetime as _dt

        return _dt.strptime(text, "%Y-%m-%d").strftime("%d %b %y")
    except (ValueError, TypeError):
        return text or "--"


class _HoverChart(_ChartView):
    """Shared crosshair plumbing for the analytical chart surfaces.

    Hover is a first-class affordance here: the user must be able to read a
    precise value off the curve without leaving the surface (§18).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._hover_x: float | None = None
        self._plot: QRect | None = None

    def set_result(self, result: StrategyResult | None) -> None:
        self._hover_x = None
        super().set_result(result)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        point = event.position().toPoint()
        if self._plot is not None and self._plot.contains(point):
            self._hover_x = float(point.x())
        else:
            self._hover_x = None
        self.update()

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self._hover_x = None
        self.update()
        super().leaveEvent(event)

    def _hover_index(self, count: int) -> int | None:
        """Map the hovered x to a data index in the *full* series."""
        if self._hover_x is None or self._plot is None or count < 2:
            return None
        width = max(1, self._plot.width())
        ratio = (self._hover_x - self._plot.left()) / width
        return max(0, min(count - 1, int(round(ratio * (count - 1)))))

    def _draw_axes(self, painter: QPainter, plot: QRect, lo: float, hi: float) -> None:
        """Y gridlines with value labels + frame. One shared visual grammar."""
        painter.setPen(QPen(_GRID, 1))
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
            value = hi - (hi - lo) * i / 4
            painter.setPen(_AXIS)
            painter.drawText(
                QRect(0, int(y) - 8, _PAD_L - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _compact_axis_money(value),
            )
            painter.setPen(QPen(_GRID, 1))

    def _draw_time_axis(self, painter: QPainter, plot: QRect, first: str, last: str) -> None:
        painter.setPen(_AXIS)
        painter.setFont(QFont("Segoe UI", 8))
        y = plot.bottom() + 5
        painter.drawText(plot.left(), y + 10, _short_stamp(first))
        painter.drawText(
            QRect(plot.left(), y, plot.width(), 14),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            _short_stamp(last),
        )

    def _draw_readout(self, painter: QPainter, plot: QRect, lines: list[str]) -> None:
        """Cursor-attached readout box — the precise numbers, in place."""
        if not lines:
            return
        painter.setFont(QFont("Segoe UI", 8))
        metrics = painter.fontMetrics()
        width = max(metrics.horizontalAdvance(line) for line in lines) + 16
        height = metrics.height() * len(lines) + 10
        x = int(self._hover_x or plot.left()) + 14
        if x + width > plot.right():
            x = int(self._hover_x or plot.left()) - width - 14
        x = max(plot.left(), x)
        y = min(max(plot.top(), plot.top() + 6), plot.bottom() - height)
        box = QRect(x, y, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(7, 11, 16, 235)))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(QPen(_GRID, 1))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(QColor("#cfd8dc"))
        for index, line in enumerate(lines):
            painter.drawText(
                box.left() + 8,
                box.top() + metrics.height() * (index + 1) + 1,
                line,
            )


class EquityCurveView(_HoverChart):
    """Equity line: axes, baseline, start/end markers, crosshair readout.

    This is an analytical surface, not decoration — it keeps the space it is
    given (300-400px on desktop when active) and never compresses into a
    strip (§18).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(240)
        self._series: list[float] = []
        self._stamps: list[str] = []

    def set_result(self, result: StrategyResult | None) -> None:
        super().set_result(result)
        curve = result.equity_curve if result is not None else ()
        # Cache the numeric series once: hover repaints must never rescan a
        # 193k-point curve.
        self._series = [float(p.equity) for p in curve]
        self._stamps = [str(p.timestamp) for p in curve]
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _CHART_BG)
        if self._result is None or len(self._series) < 2:
            painter.setPen(_TEXT)
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "NO EQUITY DATA\nRun a backtest to see the capital trajectory.",
            )
            self._plot = None
            return
        plot = self.rect().adjusted(_PAD_L, _PAD_T, -_PAD_R, -_PAD_B)
        if plot.width() <= 0 or plot.height() <= 0:
            self._plot = None
            return
        self._plot = plot
        series = self._series
        lo, hi = min(series), max(series)
        baseline = float(self._result.metrics.starting_capital or lo)
        lo, hi = min(lo, baseline), max(hi, baseline)
        span = (hi - lo) or 1.0
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_axes(painter, plot, lo, hi)
        self._draw_time_axis(painter, plot, self._stamps[0], self._stamps[-1])

        def y_of(value: float) -> float:
            return plot.bottom() - (value - lo) / span * plot.height()

        # baseline at the initial capital — the "did I make money" reference
        y0 = y_of(baseline)
        painter.setPen(QPen(QColor("#3b4659"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(plot.left(), int(y0), plot.right(), int(y0))
        painter.setPen(_AXIS)
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(plot.left() + 4, int(y0) - 4, "START CAPITAL")

        # curve (LOD-decimated for paint, full resolution for hover)
        equities = decimate_envelope(series, max(64, plot.width() * 2))
        pts = []
        for i, equity in enumerate(equities):
            x = plot.left() + i / max(1, len(equities) - 1) * plot.width()
            from PySide6.QtCore import QPointF as _QPointF

            pts.append(_QPointF(x, y_of(equity)))
        painter.setPen(QPen(_EQUITY, 1.6))
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i + 1])

        # start / end markers with their values
        start, end = series[0], series[-1]
        for x, value in ((plot.left(), start), (plot.right(), end)):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_EQUITY))
            painter.drawEllipse(int(x) - 3, int(y_of(value)) - 3, 6, 6)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#9fb3c8"))
        painter.drawText(
            QRect(plot.left() + 4, int(y_of(start)) + 4, 140, 14),
            Qt.AlignmentFlag.AlignLeft,
            f"START {_compact_axis_money(start)}",
        )
        painter.drawText(
            QRect(plot.right() - 150, int(y_of(end)) - 18, 146, 14),
            Qt.AlignmentFlag.AlignRight,
            f"END {_compact_axis_money(end)}",
        )

        # crosshair readout
        index = self._hover_index(len(series))
        if index is not None:
            value = series[index]
            x = plot.left() + index / max(1, len(series) - 1) * plot.width()
            painter.setPen(QPen(QColor("#7f8fa4"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(x), plot.top(), int(x), plot.bottom())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.drawEllipse(int(x) - 3, int(y_of(value)) - 3, 6, 6)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            change = (value - start) / start * 100 if start else 0.0
            self._draw_readout(
                painter,
                plot,
                [
                    _short_stamp(self._stamps[index]),
                    f"EQUITY {_compact_axis_money(value)}",
                    f"CHANGE {change:+.2f}%",
                ],
            )


def _drawdown_profile(values: list[float]) -> tuple[float, int, bool]:
    """(max drawdown %, longest underwater run in points, recovered?).

    Derived from the engine's existing per-point ``drawdown_pct`` series —
    no new metric formula, only a reading of the stored curve.
    """
    longest = current = 0
    for value in values:
        if value > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return max(values, default=0.0), longest, (not values or values[-1] <= 0)


class DrawdownView(_HoverChart):
    """Drawdown area chart with max/duration/recovery annotations (§20)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(240)
        self._draws: list[float] = []
        self._stamps: list[str] = []

    def set_result(self, result: StrategyResult | None) -> None:
        super().set_result(result)
        curve = result.equity_curve if result is not None else ()
        self._draws = [float(p.drawdown_pct) for p in curve]
        self._stamps = [str(p.timestamp) for p in curve]
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _CHART_BG)
        if self._result is None or len(self._draws) < 2:
            painter.setPen(_TEXT)
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "NO DRAWDOWN DATA\nRun a backtest to see the risk profile.",
            )
            self._plot = None
            return
        plot = self.rect().adjusted(_PAD_L, _PAD_T + 14, _PAD_R, _PAD_B)
        if plot.width() <= 0 or plot.height() <= 0:
            self._plot = None
            return
        self._plot = plot
        draws = self._draws
        max_dd, longest, recovered = _drawdown_profile(draws)
        ceiling = max(max_dd, 1e-9)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # ── key drawdown facts, stated before the picture ──
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#9fb3c8"))
        painter.drawText(
            QRect(plot.left(), 2, plot.width(), 14),
            Qt.AlignmentFlag.AlignLeft,
            f"MAX DRAWDOWN  -{max_dd:.2f}%   ·   LONGEST UNDERWATER  {longest} bars"
            f"   ·   {'RECOVERED' if recovered else 'NOT RECOVERED'}",
        )
        self._draw_axes(painter, plot, 0.0, ceiling)
        self._draw_time_axis(painter, plot, self._stamps[0], self._stamps[-1])

        def y_of(value: float) -> float:
            return plot.top() + value / ceiling * plot.height()

        # max drawdown reference line
        y_max = y_of(max_dd)
        painter.setPen(QPen(QColor("#7a2f36"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(plot.left(), int(y_max), plot.right(), int(y_max))

        sampled = decimate_envelope(draws, max(64, plot.width() * 2))
        from PySide6.QtCore import QPointF as _QPointF
        from PySide6.QtGui import QPolygonF as _Poly

        pts = []
        for i, value in enumerate(sampled):
            x = plot.left() + i / max(1, len(sampled) - 1) * plot.width()
            pts.append(_QPointF(x, y_of(value)))
        painter.setPen(QPen(_DD, 1.2))
        painter.setBrush(QBrush(QColor(239, 83, 80, 46)))
        painter.drawPolygon(
            _Poly(pts + [_QPointF(pts[-1].x(), plot.top()), _QPointF(pts[0].x(), plot.top())])
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i + 1])

        index = self._hover_index(len(draws))
        if index is not None:
            value = draws[index]
            x = plot.left() + index / max(1, len(draws) - 1) * plot.width()
            painter.setPen(QPen(QColor("#7f8fa4"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(x), plot.top(), int(x), plot.bottom())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.drawEllipse(int(x) - 3, int(y_of(value)) - 3, 6, 6)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            self._draw_readout(
                painter,
                plot,
                [
                    _short_stamp(self._stamps[index]),
                    f"DRAWDOWN -{value:.2f}%",
                ],
            )


class DistributionView(_ChartView):
    """Win/loss counts + P&L and R-multiple histograms."""

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _CHART_BG)
        if self._result is None or not self._result.trades:
            painter.setPen(_TEXT)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No distribution — run a backtest"
            )
            return
        trades = self._result.trades
        wins = sum(1 for t in trades if t.winning)
        losses = len(trades) - wins
        # header counts
        painter.setPen(_TEXT)
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        painter.drawText(
            self.rect().adjusted(8, 4, -8, -8),
            Qt.AlignmentFlag.AlignTop,
            f"Wins: {wins}   Losses: {losses}   R-defined: {sum(1 for t in trades if t.r_multiple is not None)}/{len(trades)}",  # noqa: E501
        )
        # P&L histogram bottom half
        rect = self.rect().adjusted(8, 22, -8, -8)
        if rect.height() <= 0:
            return
        # bucket P&L into 10 bins
        pnls = [t.pnl for t in trades]
        if not pnls:
            return
        lo, hi = min(pnls), max(pnls)
        span = hi - lo or 1.0
        bins = 10
        counts = [0] * bins
        for v in pnls:
            idx = min(bins - 1, int((v - lo) / span * bins))
            counts[idx] += 1
        max_c = max(counts) or 1
        bar_w = rect.width() / bins
        for i, c in enumerate(counts):
            h = c / max_c * (rect.height() * 0.85)
            x = rect.left() + i * bar_w + 1
            y = rect.bottom() - h
            color = _BULL if (lo + (i + 0.5) / bins * span) >= 0 else _BEAR
            painter.fillRect(int(x), int(y), int(bar_w - 2), int(h), color)


class TradesView(QWidget):
    """QTableWidget listing closed trades (materialization capped).

    All trades stay in the backing result (export/selection map to global
    indices); only the first ``_ROW_CAP`` rows become widgets so a
    527-stock batch never builds millions of items.
    """

    trade_selected = Signal(int)

    _ROW_CAP = 2000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: StrategyResult | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(self)
        self._table.setColumnCount(10)
        self._table.setHorizontalHeaderLabels(
            ["#", "Side", "Entry", "Entry Price", "Exit", "Exit Price", "Qty", "P&L", "P&L %", "R"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.cellClicked.connect(lambda r, _c: self.trade_selected.emit(r))
        layout.addWidget(self._table)

    def set_result(self, result: StrategyResult | None) -> None:
        """Populate the table from `result` (first rows only when huge)."""
        self._result = result
        self._table.setRowCount(0)
        if result is None or not result.trades:
            self._table.setToolTip("")
            return
        total = len(result.trades)
        shown = min(total, self._ROW_CAP)
        self._table.setRowCount(shown)
        for row in range(shown):
            trade = result.trades[row]
            values = [
                str(row + 1),
                trade.side,
                trade.entry_time[:16],
                f"{trade.entry_price:.2f}",
                trade.exit_time[:16],
                f"{trade.exit_price:.2f}",
                f"{trade.quantity:.2f}",
                f"{trade.pnl:+.2f}",
                f"{trade.pnl_pct:+.2f}%",
                f"{trade.r_multiple:.2f}" if trade.r_multiple is not None else "--",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 7:
                    item.setForeground(QBrush(_BULL if trade.winning else _BEAR))
                self._table.setItem(row, col, item)
        if total > shown:
            self._table.setToolTip(
                f"Showing first {shown:,} of {total:,} trades — use the Strategy Lab blotter filters to narrow."  # noqa: E501
            )
        else:
            self._table.setToolTip("")


class MonthlyView(_ChartView):
    """Monthly P&L bar chart."""

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _CHART_BG)
        if self._result is None or not self._result.trades:
            painter.setPen(_TEXT)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No monthly data — run a backtest"
            )
            return
        buckets: dict[str, float] = defaultdict(float)
        for trade in self._result.trades:
            buckets[trade.exit_time[:7]] += trade.pnl
        months = sorted(buckets)
        if not months:
            return
        rect = self.rect().adjusted(40, 8, -8, -16)
        max_abs = max((abs(v) for v in buckets.values()), default=1.0) or 1.0
        bar_w = rect.width() / len(months)
        for i, month in enumerate(months):
            value = buckets[month]
            h = abs(value) / max_abs * (rect.height() / 2 - 2)
            x = rect.left() + i * bar_w + 1
            mid = rect.center().y()
            color = _BULL if value >= 0 else _BEAR
            if value >= 0:
                painter.fillRect(int(x), int(mid - h), int(bar_w - 2), int(h), color)
            else:
                painter.fillRect(int(x), int(mid), int(bar_w - 2), int(h), color)


class PerformanceView(QWidget):
    """Detailed metrics table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        self._labels: dict[str, QLabel] = {}
        for key in (
            "Starting capital",
            "Ending capital",
            "Net profit",
            "Net profit %",
            "Gross profit",
            "Gross loss",
            "Max drawdown %",
            "Max drawdown",
            "Avg trade",
            "Expectancy",
            "Sharpe ratio",
            "Profit factor",
            "Win rate",
            "Total trades",
            "Period",
            "Bars used",
        ):
            QVBoxLayout()
            kl = QLabel(key, self)
            kl.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
            vl = QLabel("--", self)
            vl.setStyleSheet("font-size: 11px; font-weight: 600;")
            self._labels[key] = vl
            layout.addWidget(kl)
            layout.addWidget(vl)

    def set_result(self, result: StrategyResult | None) -> None:
        """Fill from `result`."""
        if result is None:
            for label in self._labels.values():
                label.setText("--")
            return
        m = result.metrics
        mapping = {
            "Starting capital": f"₹ {m.starting_capital:,.2f}",
            "Ending capital": f"₹ {m.ending_capital:,.2f}",
            "Net profit": f"₹ {m.net_profit:+,.2f}",
            "Net profit %": f"{m.net_profit_pct:+.2f}%",
            "Gross profit": f"₹ {m.gross_profit:,.2f}",
            "Gross loss": f"₹ {m.gross_loss:,.2f}",
            "Max drawdown %": f"{m.max_drawdown_pct:.2f}%",
            "Max drawdown": f"₹ {m.max_drawdown_abs:,.2f}",
            "Avg trade": f"₹ {m.avg_trade:,.2f}" if m.avg_trade is not None else "--",
            "Expectancy": f"₹ {m.expectancy:,.2f}" if m.expectancy is not None else "--",
            "Sharpe ratio": f"{m.sharpe_ratio:.2f}" if m.sharpe_ratio is not None else "--",
            "Profit factor": f"{m.profit_factor:.2f}" if m.profit_factor is not None else "--",
            "Win rate": f"{m.win_rate * 100:.1f}%" if m.win_rate is not None else "--",
            "Total trades": str(m.total_trades),
            "Period": f"{result.period_start[:10] if result.period_start else '--'} → {result.period_end[:10] if result.period_end else '--'}",  # noqa: E501
            "Bars used": str(result.bars_used),
        }
        for key, value in mapping.items():
            self._labels[key].setText(value)


class ExposureView(_ChartView):
    """Time-in-market and exposure summary."""

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _CHART_BG)
        if self._result is None or not self._result.trades:
            painter.setPen(_TEXT)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No exposure data — run a backtest"
            )
            return
        held = sum(t.bars_held for t in self._result.trades)
        total = self._result.bars_used or 1
        pct = held / total * 100.0 if total else 0.0
        painter.setPen(_TEXT)
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            self.rect().adjusted(12, 8, -12, -12),
            Qt.AlignmentFlag.AlignTop,
            f"Time in market: {pct:.1f}%   Trades: {len(self._result.trades)}   Bars held: {held}/{total}",  # noqa: E501
        )
        # bar
        rect = self.rect().adjusted(12, 32, -12, -12)
        painter.fillRect(rect, QColor("#1f2632"))
        w = int(rect.width() * pct / 100.0)
        painter.fillRect(
            rect.left(), rect.top(), w, rect.height(), _BULL if pct < 80 else QColor("#d4a017")
        )
