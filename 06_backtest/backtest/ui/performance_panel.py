"""PerformancePanel — collapsible Strategy Tester with 8 metrics + analytics tabs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from backtest.models.result import BacktestResult, StrategyResult
from backtest.ui.analytics_views import (
    DistributionView,
    DrawdownView,
    EquityCurveView,
    ExposureView,
    MonthlyView,
    PerformanceView,
    TradesView,
)

_METRIC_KEYS = (
    "NET PROFIT",
    "TOTAL TRADES",
    "WIN RATE",
    "PROFIT FACTOR",
    "MAX DRAWDOWN",
    "AVG TRADE",
    "EXPECTANCY",
    "SHARPE RATIO",
)

_LABEL_STYLE = "color: palette(placeholder-text); font-size: 9px; font-weight: 700;"
_VALUE_STYLE = "color: palette(text); font-size: 12px; font-weight: 600;"
_VALUE_MUTED = "color: palette(placeholder-text); font-size: 12px; font-weight: 600;"
_HEADER_STYLE = "color: palette(text); font-size: 11px; font-weight: 700;"


class PerformancePanel(QWidget):
    """Collapsible bottom tester: metrics row + 7 analytics tabs + header stats."""

    trade_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._collapsed = True
        self._result: StrategyResult | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # header row (always visible, click toggles)
        header = QWidget(self)
        header.setStyleSheet(
            "background: palette(alternate-base); border-top: 1px solid palette(mid);"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(10, 4, 10, 4)
        h_layout.setSpacing(8)
        title = QLabel("STRATEGY TESTER — PERFORMANCE", header)
        title.setStyleSheet(_HEADER_STYLE)
        self._toggle = QPushButton("▸", header)
        self._toggle.setFixedSize(22, 22)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setStyleSheet("QPushButton { border: none; font-size: 12px; }")
        self._toggle.clicked.connect(self.toggle_collapsed)
        self._header_stats = QLabel("", header)
        self._header_stats.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        h_layout.addWidget(self._toggle)
        h_layout.addWidget(title)
        h_layout.addWidget(self._header_stats)
        h_layout.addStretch(1)
        layout.addWidget(header)

        # collapsible body
        self._body = QWidget(self)
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(8, 6, 8, 6)
        body_layout.setSpacing(6)

        # 8 metric tiles
        metrics_box = QGridLayout()
        metrics_box.setHorizontalSpacing(12)
        metrics_box.setVerticalSpacing(4)
        self._metric_labels: dict[str, QLabel] = {}
        for idx, key in enumerate(_METRIC_KEYS):
            col, row = idx % 4, idx // 4
            tile = QWidget(self._body)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(6, 4, 6, 4)
            tile_layout.setSpacing(1)
            kl = QLabel(key, tile)
            kl.setStyleSheet(_LABEL_STYLE)
            vl = QLabel("--", tile)
            vl.setStyleSheet(_VALUE_MUTED)
            self._metric_labels[key] = vl
            tile_layout.addWidget(kl)
            tile_layout.addWidget(vl)
            metrics_box.addWidget(tile, row, col)
        body_layout.addLayout(metrics_box)

        line = QFrame(self._body)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setStyleSheet("color: palette(midlight); background: palette(midlight);")
        line.setFixedHeight(1)
        body_layout.addWidget(line)

        # 7 analytics tabs
        self._tabs = QTabWidget(self._body)
        self._tabs.setDocumentMode(True)
        self._equity_view = EquityCurveView(self._tabs)
        self._drawdown_view = DrawdownView(self._tabs)
        self._trades_view = TradesView(self._tabs)
        self._trades_view.trade_selected.connect(self.trade_selected)
        self._monthly_view = MonthlyView(self._tabs)
        self._performance_view = PerformanceView(self._tabs)
        self._distribution_view = DistributionView(self._tabs)
        self._exposure_view = ExposureView(self._tabs)
        self._tabs.addTab(self._equity_view, "EQUITY CURVE")
        self._tabs.addTab(self._drawdown_view, "DRAWDOWN")
        self._tabs.addTab(self._trades_view, "TRADES")
        self._tabs.addTab(self._monthly_view, "MONTHLY")
        self._tabs.addTab(self._performance_view, "PERFORMANCE")
        self._tabs.addTab(self._distribution_view, "DISTRIBUTION")
        self._tabs.addTab(self._exposure_view, "EXPOSURE")
        body_layout.addWidget(self._tabs, 1)

        layout.addWidget(self._body)
        self._apply_collapsed()

    @property
    def collapsed(self) -> bool:
        """Whether the body is hidden."""
        return self._collapsed

    def toggle_collapsed(self) -> None:
        """Flip the collapsed state."""
        self._collapsed = not self._collapsed
        self._apply_collapsed()

    def set_collapsed(self, collapsed: bool) -> None:
        """Set collapsed state explicitly."""
        self._collapsed = collapsed
        self._apply_collapsed()

    def _apply_collapsed(self) -> None:
        self._body.setVisible(not self._collapsed)
        self._toggle.setText("▸" if self._collapsed else "▾")

    def set_result(self, result: StrategyResult | None) -> None:
        """Populate the metrics row and all analytics views from `result`.

        ``None`` clears every view to its ``"--"`` / placeholder state.
        """
        self._result = result
        if result is None:
            for label in self._metric_labels.values():
                label.setText("--")
                label.setStyleSheet(_VALUE_MUTED)
            self._header_stats.setText("")
        else:
            m = result.metrics
            mapping = {
                "NET PROFIT": f"₹ {m.net_profit:+,.0f}" if m.total_trades else "--",
                "TOTAL TRADES": str(m.total_trades),
                "WIN RATE": f"{m.win_rate * 100:.1f}%" if m.win_rate is not None else "--",
                "PROFIT FACTOR": f"{m.profit_factor:.2f}" if m.profit_factor is not None else "--",
                "MAX DRAWDOWN": f"{m.max_drawdown_pct:.2f}%",
                "AVG TRADE": f"₹ {m.avg_trade:+,.0f}" if m.avg_trade is not None else "--",
                "EXPECTANCY": f"₹ {m.expectancy:+,.0f}" if m.expectancy is not None else "--",
                "SHARPE RATIO": f"{m.sharpe_ratio:.2f}" if m.sharpe_ratio is not None else "--",
            }
            for key, value in mapping.items():
                lbl = self._metric_labels[key]
                lbl.setText(value)
                lbl.setStyleSheet(_VALUE_STYLE if value != "--" else _VALUE_MUTED)
            self._header_stats.setText(
                f"  {result.name}  •  {result.period_start[:10] if result.period_start else '--'} → {result.period_end[:10] if result.period_end else '--'}  •  {len(result.trades)} trades"  # noqa: E501
            )
        for view in (
            self._equity_view,
            self._drawdown_view,
            self._trades_view,
            self._monthly_view,
            self._performance_view,
            self._distribution_view,
            self._exposure_view,
        ):
            view.set_result(result)

    def set_results(self, result: BacktestResult | None) -> None:
        """Convenience: pick the first strategy result (lab's active strategy).

        The lab wires the active-strategy-aware variant in bootstrap; this
        keeps the panel itself unaware of strategy selection.
        """
        if result is None or not result.results:
            self.set_result(None)
        else:
            self.set_result(result.results[0])

    @property
    def tabs(self) -> QTabWidget:
        """The analytics tab widget."""
        return self._tabs

    def clear(self) -> None:
        """Reset to the empty state."""
        self.set_result(None)
