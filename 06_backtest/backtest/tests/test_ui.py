"""Backtest UI + overlay + validation tests."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from chart.models.chart_viewport import ChartViewport
from market.models.bar import Bar
from PySide6.QtWidgets import QApplication
from strategy.models.form import BacktestForm

from backtest.models.config import BacktestConfig
from backtest.models.equity import EquityPoint
from backtest.models.metrics import PerformanceMetrics
from backtest.models.result import StrategyResult
from backtest.models.trade import TradeRecord
from backtest.ui.overlay import TradeOverlay
from backtest.ui.performance_panel import PerformancePanel
from backtest.validation import validate_backtest_form


def _qt_app() -> QApplication:
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        return inst
    return QApplication([])


def _result() -> StrategyResult:
    trade = TradeRecord(
        symbol="T",
        side="LONG",
        entry_index=2,
        exit_index=5,
        entry_time="2026-01-03 09:15:00",
        exit_time="2026-01-06 09:15:00",
        entry_price=100,
        exit_price=110,
        quantity=10,
        pnl=90,
        pnl_pct=9,
        commission=2,
        bars_held=3,
        exit_reason="SIGNAL",
    )
    curve = (
        EquityPoint(timestamp="2026-01-01 09:15:00", equity=1_000_000),
        EquityPoint(timestamp="2026-01-06 09:15:00", equity=1_000_090),
    )
    metrics = PerformanceMetrics(
        net_profit=90,
        total_trades=1,
        win_rate=1.0,
        starting_capital=1_000_000,
        ending_capital=1_000_090,
    )
    return StrategyResult(
        strategy_id="s",
        name="S v1.0",
        config=BacktestConfig(
            symbol="T",
            timeframe="15m",
            start_date="2026-01-01",
            end_date="2026-01-10",
            initial_capital=1_000_000,
        ),
        trades=(trade,),
        equity_curve=curve,
        metrics=metrics,
        bars_used=10,
        period_start="2026-01-01 09:15:00",
        period_end="2026-01-10 09:15:00",
    )


def test_performance_panel_set_result():
    app = _qt_app()
    assert app is not None
    panel = PerformancePanel()
    assert panel.collapsed
    panel.set_result(_result())
    assert panel._metric_labels["NET PROFIT"].text() != "--"
    panel.clear()
    assert panel._metric_labels["NET PROFIT"].text() == "--"
    panel.toggle_collapsed()
    assert not panel.collapsed


def test_overlay_set_and_clear():
    from PySide6.QtCore import QRect

    overlay = TradeOverlay()
    overlay.set_result(_result())
    assert len(overlay._trades) == 1
    # paint shouldn't crash
    from PySide6.QtGui import QImage, QPainter

    bars = tuple(
        Bar(
            symbol="T",
            open=100,
            high=102,
            low=99,
            close=101,
            volume=1000,
            timestamp=f"2026-01-{i + 1:02d} 09:15:00",
        )
        for i in range(10)
    )
    vp = ChartViewport(
        bars=bars,
        first=0,
        last=10,
        price_low=90,
        price_high=115,
        volume_max=1000,
        chart_rect=QRect(0, 0, 400, 300),
        volume_rect=QRect(0, 300, 400, 40),
        axis_rect=QRect(0, 340, 400, 20),
    )
    img = QImage(400, 360, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    overlay.paint_overlay(painter, vp)
    painter.end()
    overlay.clear()
    assert not overlay._trades


def test_validate_form():
    form = BacktestForm(
        strategy_id="s",
        timeframe="15m",
        start_date="2026-01-01",
        end_date="2026-01-10",
        initial_capital=1_000_000,
        slippage_pct=0.02,
        commission_pct=0.03,
    )
    assert validate_backtest_form(form, "TEST", {"s"}) == []
    assert validate_backtest_form(form, None, {"s"})
    assert validate_backtest_form(form, "TEST", set())
    bad = BacktestForm(
        strategy_id="s",
        timeframe="15m",
        start_date="2026-01-10",
        end_date="2026-01-01",
        initial_capital=1_000_000,
        slippage_pct=0.02,
        commission_pct=0.03,
    )
    assert any("Start date" in e for e in validate_backtest_form(bad, "TEST", {"s"}))
