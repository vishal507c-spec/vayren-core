"""Regression test for Trade Row → Instant Exact Chart Context bug.

Ensures single-click anywhere on a trade row triggers the chart context path.
See prompt §35.
"""

from unittest.mock import MagicMock

import pytest
from backtest.models.config import BacktestConfig
from backtest.models.equity import EquityPoint
from backtest.models.metrics import PerformanceMetrics
from backtest.models.result import StrategyResult
from backtest.models.trade import TradeRecord
from core.event_bus.event_bus import EventBus
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services.trade_chart_controller import TradeChartController
from app.ui.strategy_lab_workspace import TradeBlotter


def _dummy_result(n: int = 3) -> StrategyResult:
    trades = tuple(
        TradeRecord(
            symbol="360ONE",
            side="SHORT" if i % 2 == 0 else "LONG",
            entry_index=i * 10,
            exit_index=i * 10 + 1,
            entry_time=f"2023-01-0{2 + i} 10:00:00",
            exit_time=f"2023-01-0{2 + i} 10:15:00",
            entry_price=445.0 + i,
            exit_price=447.0 + i,
            quantity=100.0,
            pnl=100.0 * i,
            pnl_pct=1.0,
            commission=5.0,
            bars_held=1,
            exit_reason="SIGNAL",
            r_multiple=0.5,
        )
        for i in range(n)
    )
    metrics = PerformanceMetrics(
        total_trades=n,
        net_profit=100.0,
        win_rate=0.5,
        profit_factor=1.2,
        max_drawdown_pct=1.0,
        starting_capital=100000.0,
        ending_capital=100100.0,
        gross_profit=100.0,
        gross_loss=0.0,
        max_drawdown_abs=1000.0,
        avg_trade=10.0,
        expectancy=10.0,
        sharpe_ratio=1.0,
        net_profit_pct=0.1,
    )
    curve = (
        EquityPoint(timestamp="2023-01-01 09:15:00", equity=100000.0),
        EquityPoint(timestamp="2023-01-10 09:15:00", equity=100100.0),
    )
    cfg = BacktestConfig(
        symbol="360ONE", timeframe="15m", start_date="2023-01-01", end_date="2023-03-10"
    )
    return StrategyResult(
        strategy_id="test-id",
        name="Test v1.0",
        config=cfg,
        trades=trades,
        equity_curve=curve,
        metrics=metrics,
        bars_used=1000,
        period_start="2023-01-01 09:15:00",
        period_end="2023-03-10 15:15:00",
    )


@pytest.fixture
def blotter(qt_app):  # type: ignore[no-untyped-def]
    _ = qt_app  # noqa: F841
    w = TradeBlotter()
    w.show()
    return w


def test_single_click_any_cell_triggers_trade_clicked(qt_app, blotter):  # noqa: ARG001
    _ = qt_app  # noqa: F841
    result = _dummy_result(2)
    blotter.set_result(result)
    assert blotter._table.rowCount() == 2

    received: list[int] = []
    blotter.trade_clicked.connect(received.append)

    # Click symbol cell (0,1)
    blotter._table.cellClicked.emit(0, 1)
    assert received == [0]

    received.clear()
    # Click entry price cell (0,4) — whole row must be clickable
    blotter._table.cellClicked.emit(0, 4)
    assert received == [0]

    received.clear()
    # Click # cell (0,0)
    blotter._table.cellClicked.emit(0, 0)
    assert received == [0]

    received.clear()
    # Click second row
    blotter._table.cellClicked.emit(1, 1)
    assert received == [1]


def test_mouse_click_any_cell_fires(qt_app, blotter):  # noqa: ARG001
    _ = qt_app  # noqa: F841
    result = _dummy_result(2)
    blotter.set_result(result)
    received: list[int] = []
    blotter.trade_clicked.connect(received.append)
    # Ensure widget is visible for QTest
    blotter.show()
    QApplication.processEvents()

    # Click via QTest on viewport for each column of first row
    for col in (0, 1, 2, 4, 7):
        received.clear()
        rect = blotter._table.visualItemRect(blotter._table.item(0, col))
        center = rect.center()
        QTest.mouseClick(
            blotter._table.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            center,
        )
        QApplication.processEvents()
        assert received == [0], f"col {col} did not trigger"


def test_controller_receives_correct_trade_via_filtered_view(qt_app):  # noqa: ARG001
    _ = qt_app  # noqa: F841
    # Full result has 4 trades, filtered SELL has 2
    result = _dummy_result(4)
    # Simulate SELL filtered result (even indices are SHORT)
    from backtest.engine.directional import derive_directional_result

    sell_result = derive_directional_result(result, "SHORT")
    assert sell_result is not None
    assert len(sell_result.trades) == 2  # SHORT trades are indices 0,2 in full

    bus = EventBus()
    widget = MagicMock()
    widget._model = MagicMock()
    widget._model.symbol = "360ONE"
    widget._model.timeframe = "15m"
    widget._model.bars = tuple(MagicMock(timestamp=f"2023-01-0{2 + i} 10:00:00") for i in range(10))
    widget.find_bar_index = MagicMock(return_value=5)
    widget.focus_on_trade = MagicMock(return_value=True)
    window = MagicMock()
    window._current_symbol = "360ONE"
    window._current_timeframe = "15m"
    overlay = MagicMock()
    overlay.focused_trade = None
    panel = MagicMock()
    panel.isVisible.return_value = False

    ctrl = TradeChartController(bus, widget, window, MagicMock(), overlay, panel, None)
    ctrl.set_result(result, result.config)

    # Simulate TradeBlotter emitting filtered index 1 (second SHORT, which is full index 2)
    blotter = TradeBlotter()
    blotter.set_result(sell_result)
    # blotter index 1 corresponds to full trade with entry 2023-01-04
    filtered_trade = blotter._trades[1]
    # Controller should map via select_trade_by_record
    ok = ctrl.select_trade_by_record(filtered_trade)
    assert ok
    # Controller's selected should be full index 2
    assert ctrl._selected_index == 2
    # Overlay should have focused trade with same entry_time as filtered (instant cache hit)
    assert overlay.set_focused_trade.called  # type: ignore[attr-defined]
    # Verify that the focused trade entry_time matches filtered
    args, _ = overlay.set_focused_trade.call_args  # type: ignore[attr-defined]
    assert args[0].entry_time == filtered_trade.entry_time


def test_rapid_clicks_final_wins(qt_app):  # noqa: ARG001
    _ = qt_app  # noqa: F841
    result = _dummy_result(5)
    bus = EventBus()
    widget = MagicMock()
    widget._model = MagicMock()
    widget._model.symbol = "360ONE"
    widget._model.timeframe = "15m"
    widget._model.bars = tuple(
        MagicMock(timestamp=f"2023-01-0{2 + i} 10:00:00") for i in range(100)
    )
    widget.find_bar_index = MagicMock(return_value=10)
    widget.focus_on_trade.return_value = True
    window = MagicMock()
    window._current_symbol = "360ONE"
    window._current_timeframe = "15m"
    overlay = MagicMock()
    panel = MagicMock()
    panel.isVisible.return_value = False
    ctrl = TradeChartController(bus, widget, window, MagicMock(), overlay, panel, None)
    ctrl.set_result(result, result.config)
    # Rapid
    ctrl.select_trade(0)
    ctrl.select_trade(1)
    ctrl.select_trade(2)
    ctrl.select_trade(3)
    ctrl.select_trade(4)
    assert ctrl._selected_index == 4
    assert ctrl._generation == 5


def test_trade_row_selection_feedback(qt_app, blotter):  # noqa: ARG001
    _ = qt_app  # noqa: F841
    result = _dummy_result(2)
    blotter.set_result(result)
    blotter.set_selected_index(0)
    assert blotter._table.selectedItems()
    blotter.set_selected_index(1)
    # First item of row 1 should be selected
    assert blotter._table.item(1, 0).isSelected() or blotter._table.selectionModel().isSelected(
        blotter._table.model().index(1, 0)
    )
