"""Strategy Lab workspace — clean three-column redesign contracts."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.ui.strategy_lab_workspace import (
    BacktestRunPanel,
    StrategyLabWorkspace,
    StrategyLibraryPanel,
)


@pytest.fixture()
def workspace(qt_app: QApplication) -> Generator[StrategyLabWorkspace, None, None]:
    assert qt_app is not None
    widget = StrategyLabWorkspace()
    yield widget
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()
    QApplication.sendPostedEvents()


def test_three_columns_present(workspace: StrategyLabWorkspace) -> None:
    # Spec requires clean two-column: library (25%) + workspace (75%)
    workspace.resize(1280, 760)
    workspace.show()
    sizes = workspace._main.sizes()
    assert len(sizes) == 2
    assert sizes[1] == max(sizes)
    assert 240 <= sizes[0] <= 340
    assert sizes[1] >= 700


def test_right_panel_open_by_default(workspace: StrategyLabWorkspace) -> None:
    # BACKTEST is now a tab inside the main workspace, not a right drawer
    workspace.show()
    assert workspace.right_settings is workspace.center_detail.backtest_panel
    assert workspace.center_detail._stack.count() == 3


def test_results_start_collapsed(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    assert not workspace.results_open()


def test_results_tabs_are_four(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.show_result(3, expand=True)
    assert workspace.results_open()
    assert workspace._result_stack.count() == 4
    assert workspace._result_stack.currentIndex() == 3


def test_center_has_code_and_params(workspace: StrategyLabWorkspace) -> None:
    pane = workspace.center_detail
    assert pane._stack.count() == 3
    assert pane._stack.currentIndex() == 0
    pane.show_parameters()
    assert pane._stack.currentIndex() == 1
    pane.show_backtest()
    assert pane._stack.currentIndex() == 2


def test_status_chip_states(workspace: StrategyLabWorkspace) -> None:
    pane = workspace.center_detail
    assert "READY" in pane._status.text()
    pane.editor.setPlainText("x = 1")
    assert "MODIFIED" in pane._status.text()
    pane.mark_saved()
    assert "READY" in pane._status.text()
    pane.show_compile_result(False, "bad", 1, 1)
    assert "ERROR" in pane._status.text()


def test_open_strategy_sets_name_and_code(workspace: StrategyLabWorkspace) -> None:
    code = 'strategy("Alpha")\n'
    workspace.open_strategy("Alpha", code)
    assert workspace.current_tab_name() == "Alpha"
    assert workspace.center_detail.get_code() == code
    assert workspace.center_detail._name.text() == "Alpha"


def test_rename_buffer_updates_header(workspace: StrategyLabWorkspace) -> None:
    pane = workspace.center_detail
    pane.open_buffer("Old", "")
    pane.rename_buffer("Old", "New")
    assert pane.current_tab_name() == "New"


def test_explorer_rows_search_and_empty_state(qt_app: QApplication) -> None:
    assert qt_app is not None
    explorer = StrategyLibraryPanel()
    explorer.show()
    explorer.set_strategies([("OBR Sell", 1700000000.0), ("Untitled", 0.0)])
    assert not explorer._list.isHidden() and explorer._empty.isHidden()
    assert not explorer._new_btn.isHidden()
    explorer._search.setText("OBR")
    assert explorer._list.count() == 1
    explorer.set_strategies([])
    assert not explorer._empty.isHidden() and explorer._list.isHidden()
    # Spec: exactly ONE primary NEW STRATEGY in empty state
    assert explorer._new_btn.isHidden()
    explorer.deleteLater()


def test_center_empty_when_no_strategy(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    assert workspace._center_stack.currentIndex() == 0
    assert not workspace._center_empty.isHidden()
    assert workspace.center_detail.isHidden()
    workspace.open_strategy("OBR Sell", 'strategy("OBR Sell")\n')
    assert workspace._center_stack.currentIndex() == 1
    assert workspace._center_empty.isHidden()
    assert not workspace.center_detail.isHidden()


def test_select_next_after_delete(workspace: StrategyLabWorkspace) -> None:
    opened: list[str] = []
    workspace.strategy_open_requested.connect(opened.append)
    workspace.left_nav.set_strategies([("A", 1.0), ("B", 2.0), ("C", 3.0)])
    workspace.select_next_after_delete("B")
    assert opened == ["C"]
    workspace.select_next_after_delete("C")
    assert opened[-1] == "B"


def test_run_config_has_fixed_costs(qt_app: QApplication) -> None:
    assert qt_app is not None
    panel = BacktestRunPanel()
    config = panel.current_config()
    assert config["slippage_pct"] == 0.02
    assert config["commission_pct"] == 0.03
    assert config["initial_capital"] == 1_000_000
    assert str(config["start_date"]) == "2023-01-01"
    assert "test_start" not in config and "test_end" not in config


def test_run_panel_blocks_empty_symbol(qt_app: QApplication) -> None:
    assert qt_app is not None
    panel = BacktestRunPanel()
    emitted: list[object] = []
    panel.run_requested.connect(emitted.append)
    panel._emit()
    assert not emitted
    assert panel._error.isVisibleTo(panel)
    assert "symbol" in panel._error.text().lower()


def test_run_panel_blocks_inverted_dates(qt_app: QApplication) -> None:
    from PySide6.QtCore import QDate

    assert qt_app is not None
    panel = BacktestRunPanel()
    panel.set_symbols(("X",))
    panel._symbol_combo.setCurrentText("X")
    panel._from.setDate(QDate(2026, 1, 1))
    panel._to.setDate(QDate(2025, 1, 1))
    emitted: list[object] = []
    panel.run_requested.connect(emitted.append)
    panel._emit()
    assert not emitted
    assert "after start" in panel._error.text()


def test_strategy_rows_have_menu_button(qt_app: QApplication) -> None:
    from PySide6.QtWidgets import QToolButton

    assert qt_app is not None
    explorer = StrategyLibraryPanel()
    explorer.show()
    explorer.set_strategies([("A", 1.0), ("B", 2.0)])
    buttons = explorer._list.findChildren(QToolButton)
    assert len(buttons) >= 2


def test_inr_formatting() -> None:
    from app.ui.strategy_lab_workspace import _inr

    assert _inr(1000000) == "10,00,000.00"
    assert _inr(-1234567.8) == "-12,34,567.80"
    assert _inr(0) == "0.00"


def test_equity_summary_populates(workspace: StrategyLabWorkspace) -> None:
    from backtest.models.config import BacktestConfig
    from backtest.models.equity import EquityPoint
    from backtest.models.metrics import PerformanceMetrics
    from backtest.models.result import StrategyResult

    workspace.show()
    points = tuple(
        EquityPoint(
            timestamp=f"2026-01-{d:02d} 09:15:00",
            equity=100000.0 + d * 500.0,
            drawdown_pct=0.0,
        )
        for d in range(1, 6)
    )
    result = StrategyResult(
        strategy_id="s",
        name="S",
        config=BacktestConfig(
            symbol="X",
            timeframe="15m",
            start_date="2026-01-01",
            end_date="2026-01-05",
            initial_capital=100000.0,
            slippage_pct=0.0,
            commission_pct=0.0,
        ),
        trades=(),
        equity_curve=points,
        metrics=PerformanceMetrics(),
        bars_used=100,
        period_start="2026-01-01",
        period_end="2026-01-05",
    )
    workspace.set_result(result)
    assert "START" in workspace._equity_summary.text()


def test_workspace_public_surface(workspace: StrategyLabWorkspace) -> None:
    for name in (
        "run_backtest",
        "trade_focus",
        "param_changed",
        "save_requested_relay",
        "compile_requested_relay",
        "strategy_open_requested",
        "set_result",
        "clear",
        "open_strategy",
        "set_strategy_name",
        "select_next_after_delete",
    ):
        assert hasattr(workspace, name)


def test_storage_file_ops(tmp_path: Path) -> None:
    from strategy.language.storage import (
        delete_strategy,
        duplicate_strategy,
        list_strategies_with_mtime,
        rename_strategy,
        save_strategy,
    )

    save_strategy('strategy("A")\n', "A", tmp_path)
    duplicate_strategy("A", "A Copy", tmp_path)
    items = dict(list_strategies_with_mtime(tmp_path))
    assert set(items) == {"A", "A Copy"}
    rename_strategy("A Copy", "A Renamed", tmp_path)
    with pytest.raises(FileExistsError):
        rename_strategy("A", "A", tmp_path)
    assert delete_strategy("A", tmp_path) is True
    assert delete_strategy("A", tmp_path) is False
