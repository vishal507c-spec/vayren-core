"""Analytics views — seven independent tab contents for the tester panel."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt, Signal
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


class EquityCurveView(_ChartView):
    """Equity line with starting/ending/drawdown annotations."""

    def set_result(self, result: StrategyResult | None) -> None:
        super().set_result(result)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _CHART_BG)
        if self._result is None or not self._result.equity_curve:
            painter.setPen(_TEXT)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No equity data — run a backtest"
            )
            return
        curve = self._result.equity_curve
        pad_l, pad_r, pad_t, pad_b = 48, 12, 8, 18
        plot = self.rect().adjusted(pad_l, pad_t, -pad_r, -pad_b)
        if plot.width() <= 0 or plot.height() <= 0:
            return
        equities = decimate_envelope([p.equity for p in curve], max(64, plot.width() * 2))
        lo, hi = min(equities), max(equities)
        span = hi - lo or 1.0
        # grid
        painter.setPen(QPen(_GRID, 1))
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
        # polyline
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(_EQUITY, 1.6))
        pts = []
        for i, equity in enumerate(equities):
            x = plot.left() + i / max(1, len(equities) - 1) * plot.width()
            y = plot.bottom() - (equity - lo) / span * plot.height()
            from PySide6.QtCore import QPointF as _QPointF

            pts.append(_QPointF(x, y))
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i + 1])
        # baseline at initial capital
        y0 = plot.bottom() - (self._result.metrics.starting_capital - lo) / span * plot.height()
        painter.setPen(QPen(QColor("#3b4659"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(plot.left(), int(y0), plot.right(), int(y0))


class DrawdownView(_ChartView):
    """Drawdown area chart."""

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _CHART_BG)
        if self._result is None or not self._result.equity_curve:
            painter.setPen(_TEXT)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No drawdown data — run a backtest"
            )
            return
        curve = self._result.equity_curve
        pad_l, pad_r, pad_t, pad_b = 48, 12, 8, 18
        plot = self.rect().adjusted(pad_l, pad_t, -pad_r, -pad_b)
        draws = decimate_envelope([p.drawdown_pct for p in curve], max(64, plot.width() * 2))
        max_dd = max(draws, default=1.0) or 1.0
        painter.setPen(QPen(_GRID, 1))
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(_DD, 1.2))
        painter.setBrush(QBrush(QColor(239, 83, 80, 40)))
        from PySide6.QtCore import QPointF as _QPointF
        from PySide6.QtGui import QPolygonF as _Poly

        pts = []
        for i, dd in enumerate(draws):
            x = plot.left() + i / max(1, len(draws) - 1) * plot.width()
            y = plot.top() + dd / max_dd * plot.height()
            pts.append(_QPointF(x, y))
        poly_pts = pts + [_QPointF(pts[-1].x(), plot.top()), _QPointF(pts[0].x(), plot.top())]
        painter.drawPolygon(_Poly(poly_pts))
        # line on top
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i + 1])


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
