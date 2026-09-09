"""Backtest UI + overlay + validation tests."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

from chart.models.chart_viewport import ChartViewport
from market.models.bar import Bar
from PySide6.QtGui import QImage
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


def _result_with_plots(plots: object) -> StrategyResult:
    """Same execution facts as :func:`_result` plus strategy-owned visuals."""
    base = _result()
    return StrategyResult(
        strategy_id=base.strategy_id,
        name=base.name,
        config=base.config,
        trades=base.trades,
        equity_curve=base.equity_curve,
        metrics=base.metrics,
        bars_used=base.bars_used,
        period_start=base.period_start,
        period_end=base.period_end,
        chart_series=base.chart_series,
        chart_plots=tuple(plots or ()),  # type: ignore[arg-type]
    )


def _marker_plot(
    bar: int,
    marker: str = "UP_ARROW",
    lifecycle: str = "ACTIVE",
    plot_type: str = "MARKER",
) -> dict:
    return {
        "event_id": f"S:{bar}:m:x",
        "source_strategy": "S",
        "plot_id": f"m_{bar}",
        "plot_type": plot_type,
        "symbol": "T",
        "timeframe": "15m",
        "bar_index": bar,
        "price": 100.0,
        "marker_type": marker,
        "text": None,
        "layer": 60,
        "lifecycle": lifecycle,
        "version": 1,
    }


def _paint_image(overlay: TradeOverlay, first: int = 0, last: int = 10) -> QImage:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter

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
        first=first,
        last=last,
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
    return img


def _pill_pixels(image: QImage) -> int:
    count = 0
    for x in range(0, image.width(), 2):
        for y in range(0, image.height(), 2):
            color = image.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) == (0x1F, 0x26, 0x32):
                count += 1
    return count


def _connection_midpoint_painted(image: QImage) -> bool:
    from PySide6.QtCore import QRectF

    from backtest.ui.overlay import _bar_x, _price_y

    rect = QRectF(0, 0, 400, 300)
    mx = (_bar_x(2, 0, 10, rect) + _bar_x(5, 0, 10, rect)) / 2.0
    my = (_price_y(100.0, 90, 115, rect) + _price_y(110.0, 90, 115, rect)) / 2.0
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            color = image.pixelColor(int(mx) + dx, int(my) + dy)
            if (color.red(), color.green(), color.blue()) != (0, 0, 0):
                return True
    return False


def test_overlay_generic_execution_paints_signal_markers():
    """No strategy plots: TradeOverlay renders entry/exit pills + connection."""
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result())
    assert overlay.covered_bars == frozenset()
    image = _paint_image(overlay)
    assert _pill_pixels(image) > 0
    assert _connection_midpoint_painted(image)


def test_overlay_strategy_owned_signals_suppressed_once():
    """Covered entry/exit: no duplicate pills, connection (span info) remains."""
    from strategy import MarkerType, PlotEvent, PlotType

    _qt_app()

    def _plot(bar: int, marker: MarkerType) -> PlotEvent:
        return PlotEvent(
            event_id=f"S:{bar}:e:x",
            source_strategy="S",
            plot_id=f"e_{bar}",
            plot_type=PlotType.MARKER,
            symbol="T",
            timeframe="15m",
            bar_index=bar,
            price=100.0,
            marker_type=marker,
            text=None,
        )

    overlay = TradeOverlay()
    overlay.set_result(
        _result_with_plots((_plot(2, MarkerType.UP_ARROW), _plot(5, MarkerType.DOWN_ARROW)))
    )
    assert overlay.covered_bars == frozenset({2, 5})
    image = _paint_image(overlay)
    # One event -> one visual: strategy glyphs own bars 2 and 5, so no pills…
    assert _pill_pixels(image) == 0
    # …but the holding-span connection (different information) remains.
    assert _connection_midpoint_painted(image)


def test_overlay_partial_coverage_suppresses_only_covered_end():
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result_with_plots((_marker_plot(2),)))
    assert overlay.covered_bars == frozenset({2})
    assert _pill_pixels(_paint_image(overlay)) > 0  # exit pill still paints


def test_overlay_unrelated_marker_keeps_both_visuals():
    """A strategy marker on another bar must not hide trade signals."""
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result_with_plots((_marker_plot(7),)))
    assert overlay.covered_bars == frozenset({7})
    assert _pill_pixels(_paint_image(overlay)) > 0


def test_overlay_removed_marker_does_not_suppress():
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(
        _result_with_plots(
            (_marker_plot(2, lifecycle="REMOVED"), _marker_plot(5, lifecycle="HIDDEN"))
        )
    )
    assert overlay.covered_bars == frozenset()
    assert _pill_pixels(_paint_image(overlay)) > 0


def test_overlay_non_marker_plots_never_suppress():
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(
        _result_with_plots(
            (
                _marker_plot(2, plot_type="ZONE"),
                dict(_marker_plot(5), plot_type="LINE", marker_type=None),
                {"plot_type": "NOPE"},
                None,
                "junk",
            )
        )
    )
    assert overlay.covered_bars == frozenset()
    assert _pill_pixels(_paint_image(overlay)) > 0


def _result_with_muted(muted: object) -> StrategyResult:
    base = _result()
    return StrategyResult(
        strategy_id=base.strategy_id,
        name=base.name,
        config=base.config,
        trades=base.trades,
        equity_curve=base.equity_curve,
        metrics=base.metrics,
        bars_used=base.bars_used,
        period_start=base.period_start,
        period_end=base.period_end,
        chart_series=base.chart_series,
        chart_plots=base.chart_plots,
        muted_bars=tuple(muted or ()),  # type: ignore[arg-type]
    )


def test_overlay_muted_exit_suppresses_pill_keeps_connection():
    """Strategy-muted bar: no execution pill, holding span still paints."""
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result_with_muted((5,)))
    assert overlay.covered_bars == frozenset({5})
    image = _paint_image(overlay)
    assert _pill_pixels(image) > 0  # entry pill still paints
    assert _connection_midpoint_painted(image)


def test_overlay_muted_entry_and_exit_leave_only_connection():
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result_with_muted((2, 5)))
    assert overlay.covered_bars == frozenset({2, 5})
    assert _pill_pixels(_paint_image(overlay)) == 0
    assert _connection_midpoint_painted(_paint_image(overlay))


def test_overlay_muted_bars_ignored_when_malformed():
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result_with_muted((-1, "x", None, 2.5, True)))
    assert overlay.covered_bars == frozenset()
    assert _pill_pixels(_paint_image(overlay)) > 0


def test_overlay_coverage_resets_per_result():
    """Coverage is per-result: switching results never leaks suppression."""
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result_with_plots((_marker_plot(2), _marker_plot(5))))
    assert overlay.covered_bars == frozenset({2, 5})
    overlay.set_result(_result())
    assert overlay.covered_bars == frozenset()
    assert _pill_pixels(_paint_image(overlay)) > 0
    overlay.set_result(_result_with_plots((_marker_plot(2),)))
    overlay.set_result(None)
    assert overlay.covered_bars == frozenset()
    assert overlay._trades == ()
    overlay.set_result(_result_with_plots((_marker_plot(2),)))
    overlay.clear()
    assert overlay.covered_bars == frozenset()


def test_overlay_focused_mode_keeps_full_detail():
    """Explicit trade inspection always paints full detail, even if covered."""
    _qt_app()
    overlay = TradeOverlay()
    result = _result_with_plots((_marker_plot(2), _marker_plot(5)))
    overlay.set_result(result)
    overlay.set_focused_trade(result.trades[0], 1)
    assert _pill_pixels(_paint_image(overlay)) > 0
    overlay.clear_focused()
    assert _pill_pixels(_paint_image(overlay)) == 0


def test_overlay_paint_is_deterministic():
    """Same result + same viewport -> identical pixels (no repaint behavior)."""
    _qt_app()
    overlay = TradeOverlay()
    overlay.set_result(_result_with_plots((_marker_plot(2), _marker_plot(5))))
    first = _paint_image(overlay)
    second = _paint_image(overlay)
    assert bytes(first.bits()) == bytes(second.bits())


def test_overlay_has_no_strategy_specific_branches():
    """Generic ownership only: no strategy names, no signal semantics."""
    import inspect

    import backtest.ui.overlay as module

    source = inspect.getsource(module)
    for forbidden in (
        '"OBR"',
        '"ORB"',
        '"VWAP"',
        "source_strategy",
        "entryEvent",
        "exitEvent",
        "ntEvent",
        "NO_TRADE",
        "SIGNAL_",
    ):
        assert forbidden not in source, f"overlay contains {forbidden}"
    # The generic contract is pinned: result-owned chart_plots drive coverage.
    assert "chart_plots" in source


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


def _form(**overrides: Any) -> BacktestForm:
    values: dict[str, Any] = {
        "strategy_id": "s",
        "timeframe": "15m",
        "start_date": "2026-01-01",
        "end_date": "2026-01-10",
        "initial_capital": 1_000_000,
        "slippage_pct": 0.02,
        "commission_pct": 0.03,
    }
    values.update(overrides)
    return BacktestForm(**values)


def test_max_position_size_validation():
    assert validate_backtest_form(_form(), "TEST", {"s"}) == []
    assert validate_backtest_form(_form(max_position_size=500_000), "TEST", {"s"}) == []
    assert validate_backtest_form(_form(max_position_size=1_000_000), "TEST", {"s"}) == []
    assert any(
        "positive" in e for e in validate_backtest_form(_form(max_position_size=0), "TEST", {"s"})
    )
    assert any(
        "positive" in e for e in validate_backtest_form(_form(max_position_size=-5), "TEST", {"s"})
    )
    assert any(
        "exceed" in e
        for e in validate_backtest_form(_form(max_position_size=2_000_000), "TEST", {"s"})
    )
