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


def test_results_visible_with_ready_state(workspace: StrategyLabWorkspace) -> None:
    # Redesign: results stay visible with a compact ready state — no blank dock.
    workspace.show()
    assert workspace.results_open()
    assert "READY" in workspace.metrics._status.text()
    assert workspace.metrics._vals["NET PROFIT"].text() == "--"


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


def test_library_taxonomy_filters_favorites_and_recent(qt_app: QApplication) -> None:
    from PySide6.QtCore import Qt

    assert qt_app is not None
    explorer = StrategyLibraryPanel()
    explorer.show()
    explorer.set_strategies([("OBR", 3.0), ("SMA", 2.0), ("RSI", 1.0)])
    assert explorer._list.count() == 3
    # Favorite pins a star and the fav filter isolates it.
    explorer.toggle_favorite("SMA")
    explorer.set_lib_filter("fav")
    assert explorer._list.count() == 1
    assert explorer._list.item(0).data(Qt.ItemDataRole.UserRole) == "SMA"
    # Recent tracks opens most-recent-first.
    explorer.strategy_selected.emit("RSI")
    explorer.strategy_selected.emit("OBR")
    explorer.set_lib_filter("recent")
    assert explorer._list.count() == 2
    assert explorer._list.item(0).data(Qt.ItemDataRole.UserRole) == "OBR"
    assert explorer._list.item(1).data(Qt.ItemDataRole.UserRole) == "RSI"
    # Back to all restores the full alpha list.
    explorer.set_lib_filter("all")
    assert explorer._list.count() == 3
    explorer.deleteLater()


def test_library_empty_filter_shows_no_matches(qt_app: QApplication) -> None:
    assert qt_app is not None
    explorer = StrategyLibraryPanel()
    explorer.show()
    explorer.set_strategies([("OBR", 3.0)])
    explorer.set_lib_filter("fav")
    assert explorer._list.isHidden() and not explorer._empty.isHidden()
    assert explorer._empty_msg.text() == "No matches"
    # Real empty library keeps the onboarding copy.
    explorer.set_strategies([])
    assert explorer._empty_msg.text() == "No strategies yet"
    explorer.deleteLater()


def test_library_select_name_falls_back_to_all(qt_app: QApplication) -> None:
    assert qt_app is not None
    explorer = StrategyLibraryPanel()
    explorer.show()
    explorer.set_strategies([("OBR", 3.0), ("SMA", 2.0)])
    explorer.set_lib_filter("fav")
    explorer.select_name("OBR")
    assert explorer._lib_filter == "all"
    assert explorer.current_name() == "OBR"
    explorer.deleteLater()


def test_strategy_identity_line_renders_record_meta(
    workspace: StrategyLabWorkspace,
) -> None:
    workspace.show()
    assert workspace._crumb_meta.text() == ""
    workspace.open_strategy(
        "OBR", "strategy('OBR')\n", {"version": "1.0", "updated_at": "2026-09-11T17:53:16"}
    )
    assert "v1.0" in workspace._crumb_meta.text()
    assert "2026" not in workspace._crumb_meta.text()  # short date, not ISO
    assert not workspace._crumb_meta.isHidden()
    # Unknown record stays empty — never invented.
    workspace.open_strategy("X", "strategy('X')\n")
    assert workspace._crumb_meta.text() == ""


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
    panel.set_selected_symbols(("X",))
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


def test_backtest_config_is_compact_grid(workspace: StrategyLabWorkspace) -> None:
    # Config lives in one dense card: symbols + timeframe/date/capital side by side.
    panel = workspace.right_settings
    host = panel._symbols.parentWidget()
    assert host is not None
    grid = host.layout()
    assert grid is not None
    assert panel._tf_combo.isVisibleTo(panel)
    assert panel._from.isVisibleTo(panel) and panel._to.isVisibleTo(panel)
    assert panel._capital.isVisibleTo(panel)
    # Advanced settings start disclosed-collapsed (progressive disclosure).
    assert not panel._advanced_host.isVisibleTo(panel)
    panel._advanced_toggle.click()
    assert panel._advanced_host.isVisibleTo(panel)
    assert "slippage" in panel._advanced_host.text().lower()


def test_single_dominant_run_action(workspace: StrategyLabWorkspace) -> None:
    # ONE primary RUN in the BACKTEST panel, directly after configuration.
    from PySide6.QtWidgets import QPushButton

    primaries = [
        b
        for b in workspace.right_settings.findChildren(QPushButton)
        if b.styleSheet() == workspace.right_settings._run.styleSheet()
        and b.isVisibleTo(workspace.right_settings)
    ]
    assert len(primaries) == 1
    assert "RUN" in workspace.right_settings._run.text()
    assert workspace.right_settings._run.minimumHeight() >= 28


def test_mode_selector_only_on_backtest_tab(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.open_strategy("OBR Sell", 'strategy("OBR Sell")\n')
    workspace.center_detail._switch(0)
    assert not workspace._mode_selector.isVisibleTo(workspace)
    workspace.center_detail.show_backtest()
    assert workspace._mode_selector.isVisibleTo(workspace)


def test_result_header_states(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    assert "READY" in workspace.metrics._status.text()
    workspace._set_run_busy(True)
    assert "RUNNING" in workspace.metrics._status.text()
    workspace._set_run_busy(False)
    assert "READY" in workspace.metrics._status.text()
    workspace.set_run_state("failed")
    assert "FAILED" in workspace.metrics._status.text()
    workspace.clear()
    assert "READY" in workspace.metrics._status.text()


def test_results_reachable_by_scroll_at_workstation_size(
    workspace: StrategyLabWorkspace,
) -> None:
    # The workspace page scrolls instead of squeezing content into the
    # viewport: metrics stay above the fold, ranking is reachable below.
    workspace.resize(1600, 900)
    workspace.show()
    # An open strategy keeps the analytical dock expanded (empty-dock
    # compaction only collapses the never-opened, never-run page).
    workspace.open_strategy("T", "strategy('T')\n")
    workspace.right_settings.set_symbols(("AAA", "BBB", "CCC"))
    workspace.right_settings.set_selected_symbols(("AAA", "BBB", "CCC"))
    QApplication.processEvents()
    assert workspace.metrics.isVisibleTo(workspace)
    height = workspace.height()
    top = workspace.metrics.mapTo(workspace, workspace.metrics.rect().topLeft()).y()
    assert 0 <= top < height
    # Ranking flows below in the scroll page: present and reachable.
    ranking_table = workspace.right_settings.ranking._table
    bar = workspace._page_scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    assert ranking_table.isVisibleTo(workspace)
    viewport = workspace._page_scroll.viewport()
    in_view = ranking_table.mapTo(viewport, ranking_table.rect().topLeft())
    assert -ranking_table.height() < in_view.y() < viewport.height()
    # Sections never overlap, wherever the scroll position is.
    banner = workspace._stale_banner
    banner.setVisible(True)
    QApplication.processEvents()
    sections = [banner, workspace.metrics, workspace._strip, workspace._result_stack]
    rects = [_rect_in(s, workspace) for s in sections if s.isVisibleTo(workspace)]
    for first, second in zip(rects, rects[1:], strict=False):
        assert not first.intersects(second)
    banner.setVisible(False)


def test_compare_ranking_first_class(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.set_view_mode("COMPARE")
    QApplication.processEvents()
    compare = workspace._compare_view
    assert compare._ranking.isVisibleTo(compare)
    assert "COMPARE" in compare._compare_header.text()
    # One dominant RUN ALL; per-side runs stay as quiet secondaries.
    assert "RUN ALL" in compare._run_all.text()
    assert compare._run_all.minimumHeight() >= 32
    assert not hasattr(compare, "_risk_grid")


def test_compare_ranking_syncs_universe_and_results(workspace: StrategyLabWorkspace) -> None:
    from backtest.engine.metrics import compute_equity_curve, compute_metrics
    from backtest.models.config import BacktestConfig
    from backtest.models.result import StrategyResult
    from backtest.models.trade import TradeRecord

    def trade(symbol: str, day: int, pnl: float) -> TradeRecord:
        return TradeRecord(
            symbol=symbol,
            side="LONG",
            entry_index=day,
            exit_index=day + 1,
            entry_time=f"2026-01-{day:02d} 09:15:00",
            exit_time=f"2026-01-{day:02d} 15:15:00",
            entry_price=100.0,
            exit_price=100.0 + pnl,
            quantity=10.0,
            pnl=pnl,
            pnl_pct=pnl,
            commission=1.0,
            bars_held=1,
            exit_reason="SIGNAL",
        )

    workspace.show()
    workspace.right_settings.set_symbols(("AAA", "BBB"))
    workspace.right_settings.set_selected_symbols(("AAA", "BBB"))
    trades = (trade("AAA", 2, 60.0), trade("BBB", 3, 10.0))
    config = BacktestConfig(
        symbol="MULTI",
        timeframe="15m",
        start_date="2026-01-01",
        end_date="2026-01-10",
        initial_capital=100000.0,
    )
    curve = compute_equity_curve(trades, 100000.0, "2026-01-01 09:15:00")
    base = StrategyResult(
        strategy_id="s",
        name="S",
        config=config,
        trades=trades,
        equity_curve=curve,
        metrics=compute_metrics(trades, curve, 100000.0),
        bars_used=100,
        period_start="2026-01-01",
        period_end="2026-01-10",
    )
    workspace.set_last_run_symbols(("AAA", "BBB"))
    workspace.set_ranking_errors({})
    workspace.set_result(base)
    workspace.set_view_mode("COMPARE")
    QApplication.processEvents()
    compare_ranking = workspace._compare_view._ranking
    assert compare_ranking.universe == ("AAA", "BBB")
    assert compare_ranking.ordered_symbols()[0] == "AAA"
    assert compare_ranking._table.isVisibleTo(workspace._compare_view)


def test_compare_stock_count_flows_to_header(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.right_settings.set_symbols(("AAA", "BBB", "CCC"))
    workspace.right_settings.set_selected_symbols(("AAA", "BBB", "CCC"))
    workspace.set_view_mode("COMPARE")
    QApplication.processEvents()
    assert "3" in workspace._compare_view._compare_header.text()
    assert "3" in workspace._compare_view._run_all.text()


def _multi_symbol_base(book: tuple[tuple[str, tuple[float, ...]], ...]):
    """Build a multi-symbol result whose per-symbol P&L is fully predictable."""
    from backtest.engine.metrics import compute_equity_curve, compute_metrics
    from backtest.models.config import BacktestConfig
    from backtest.models.result import StrategyResult
    from backtest.models.trade import TradeRecord

    trades = []
    index = 0
    for symbol, pnls in book:
        for pnl in pnls:
            index += 1
            trades.append(
                TradeRecord(
                    symbol=symbol,
                    side="LONG",
                    entry_index=index,
                    exit_index=index + 1,
                    entry_time=f"2026-01-{index:02d} 09:15:00",
                    exit_time=f"2026-01-{index:02d} 15:15:00",
                    entry_price=100.0,
                    exit_price=100.0 + pnl,
                    quantity=10.0,
                    pnl=pnl,
                    pnl_pct=pnl,
                    commission=1.0,
                    bars_held=1,
                    exit_reason="SIGNAL",
                )
            )
    config = BacktestConfig(
        symbol="MULTI",
        timeframe="15m",
        start_date="2026-01-01",
        end_date="2026-01-31",
        initial_capital=100000.0,
    )
    curve = compute_equity_curve(tuple(trades), 100000.0, "2026-01-01 09:15:00")
    return StrategyResult(
        strategy_id="s",
        name="S",
        config=config,
        trades=tuple(trades),
        equity_curve=curve,
        metrics=compute_metrics(tuple(trades), curve, 100000.0),
        bars_used=100,
        period_start="2026-01-01",
        period_end="2026-01-31",
    )


def _load_compare(workspace: StrategyLabWorkspace, book: tuple[tuple[str, tuple[float, ...]], ...]):
    symbols = tuple(symbol for symbol, _ in book)
    workspace.show()
    # Pin the window so the board's column count is deterministic — it depends
    # on the splitter's resolved widths, which vary with the active font DB.
    workspace.resize(1600, 1000)
    QApplication.processEvents()
    workspace.right_settings.set_symbols(symbols)
    workspace.right_settings.set_selected_symbols(symbols)
    workspace.set_last_run_symbols(symbols)
    workspace.set_ranking_errors({})
    workspace.set_result(_multi_symbol_base(book))
    workspace.set_view_mode("COMPARE")
    QApplication.processEvents()
    return workspace._compare_view._comparison


def test_compare_board_ranks_selected_stocks(workspace: StrategyLabWorkspace) -> None:
    # AAA is the only profitable-and-consistent book → ranked first, crowned leader.
    board = _load_compare(
        workspace,
        (
            ("AAA", (60.0, 40.0)),
            ("BBB", (10.0, -5.0)),
            ("CCC", (-20.0, -30.0)),
        ),
    )
    assert [row.symbol for row in board._stocks] == ["AAA", "BBB", "CCC"]
    assert board._total == 3
    assert "3 STOCKS COMPARED" in board._scope.text()
    assert board._leader.text().endswith("AAA")
    assert board._empty.isVisibleTo(board) is False


def test_compare_board_caps_columns_and_reports_scope(workspace: StrategyLabWorkspace) -> None:
    book = tuple((f"SYM{i:03d}", (float(100 - i),)) for i in range(10))
    board = _load_compare(workspace, book)
    assert board._total == 10
    assert len(board._stocks) == 6
    assert "TOP 6 OF 10 BY NET P&L" in board._scope.text()
    # Columns stay ranked by NET P&L — never selection order.
    assert board._stocks[0].symbol == "SYM000"


def test_compare_board_crowns_no_leader_on_tie(workspace: StrategyLabWorkspace) -> None:
    board = _load_compare(workspace, (("AAA", (25.0,)), ("BBB", (25.0,))))
    assert board._total == 2
    assert board._leader.text() == ""


def test_compare_board_empty_states(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.set_view_mode("COMPARE")
    QApplication.processEvents()
    board = workspace._compare_view._comparison
    assert board._stocks == ()
    assert "SELECT STOCKS TO COMPARE" in board._empty.text()
    # Universe selected but never backtested → distinct, honest empty state.
    workspace.right_settings.set_symbols(("AAA", "BBB"))
    workspace.right_settings.set_selected_symbols(("AAA", "BBB"))
    workspace._push_ranking()
    assert board._stocks == ()
    assert "NO COMPARABLE RESULTS" in board._empty.text()


def test_compare_cell_never_invents_values() -> None:
    from app.ui.strategy_lab_workspace import _compare_cell

    assert _compare_cell("money", None) == "--"
    assert _compare_cell("money", -118788.0) == "-₹1,18,788.00"
    assert _compare_cell("pct", -4.5) == "-4.50%"
    assert _compare_cell("rate", 0.527) == "52.7%"
    assert _compare_cell("dd", 12.345) == "-12.35%"
    assert _compare_cell("ratio", 1.234) == "1.23"
    assert _compare_cell("count", 35) == "35"


def test_batch_progress_single_indicator_and_stop(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.right_settings.set_busy(True)  # → topbar busy + pill RUNNING
    assert "RUNNING" in workspace.metrics._status.text()
    # counts appear exactly once (topbar status), never on run buttons
    workspace.set_batch_progress(123, 527)
    assert workspace._topbar_status.text() == "● 123 / 527 · 23%"
    assert "123" not in workspace.metrics._status.text()
    assert "123" not in workspace._topbar_run.text()
    # batch confirmed in flight → both run buttons morph to ■ STOP
    assert workspace._topbar_run.text() == "■ STOP"
    assert workspace.right_settings._run.text() == "■ STOP"
    # throttled to percent changes: same percent keeps prior text
    before = workspace._topbar_status.text()
    workspace.set_batch_progress(124, 527)  # still 23%
    assert workspace._topbar_status.text() == before
    workspace.set_batch_progress(247, 527)  # 46%
    assert workspace._topbar_status.text() == "● 247 / 527 · 46%"
    # STOP click emits stop_requested instead of starting a run
    stopped: list = []
    runs: list = []
    workspace.stop_requested.connect(lambda: stopped.append(1))
    workspace.run_backtest.connect(runs.append)
    workspace._topbar_run.click()
    assert stopped and not runs
    workspace.right_settings._run.click()
    assert len(stopped) == 2 and not runs
    workspace.set_run_state("complete")
    assert "COMPLETE" in workspace.metrics._status.text()


def test_run_states_include_aggregating(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.set_run_state("aggregating")
    assert "AGGREGATING" in workspace.metrics._status.text()
    workspace.set_run_state("finalizing")
    assert "FINALIZING" in workspace.metrics._status.text()


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


def _many_trades(n: int):
    from backtest.models.trade import TradeRecord

    return tuple(
        TradeRecord(
            symbol=f"S{i % 7:03d}",
            side="LONG" if i % 2 == 0 else "SHORT",
            entry_index=i,
            exit_index=i + 1,
            entry_time=f"2023-02-{(i % 27) + 1:02d} 10:00:00",
            exit_time=f"2023-02-{(i % 27) + 1:02d} 10:15:00",
            entry_price=100.0 + i,
            exit_price=101.0 + i,
            quantity=10.0,
            pnl=float(i),
            pnl_pct=1.0,
            commission=1.0,
            bars_held=1,
            exit_reason="SIGNAL",
        )
        for i in range(n)
    )


def _blotter_result(n: int):
    from backtest.models.config import BacktestConfig
    from backtest.models.equity import EquityPoint
    from backtest.models.metrics import PerformanceMetrics
    from backtest.models.result import StrategyResult

    trades = _many_trades(n)
    metrics = PerformanceMetrics(
        total_trades=n,
        net_profit=float(n),
        win_rate=0.5,
        profit_factor=1.0,
        max_drawdown_pct=0.0,
        starting_capital=100000.0,
        ending_capital=100000.0 + n,
        gross_profit=float(n),
        gross_loss=0.0,
        max_drawdown_abs=0.0,
        avg_trade=1.0,
        expectancy=1.0,
        sharpe_ratio=None,
        net_profit_pct=0.0,
    )
    return StrategyResult(
        strategy_id="cap",
        name="Cap v1.0",
        config=BacktestConfig(
            symbol="MULTI", timeframe="15m", start_date="2023-01-01", end_date="2023-03-10"
        ),
        trades=trades,
        equity_curve=(EquityPoint(timestamp="2023-01-01 09:15:00", equity=100000.0),),
        metrics=metrics,
        bars_used=n,
        period_start="2023-01-01 09:15:00",
        period_end="2023-03-10 15:15:00",
    )


def test_blotter_caps_materialized_rows(qt_app):
    from PySide6.QtWidgets import QApplication

    from app.ui.strategy_lab_workspace import TradeBlotter

    _ = qt_app
    blotter = TradeBlotter()
    blotter.show()
    QApplication.processEvents()
    blotter.set_result(_blotter_result(5000))
    assert blotter._table.rowCount() == TradeBlotter._ROW_CAP
    assert blotter._count_label.isVisible()
    assert "5,000" in blotter._count_label.text()
    # clicks still map to global trade indices
    received: list[int] = []
    blotter.trade_clicked.connect(received.append)
    blotter._table.cellClicked.emit(0, 1)
    assert received == [0]
    blotter._table.cellClicked.emit(1999, 1)
    assert received == [0, 1999]


def test_blotter_filter_refills_from_all_data(qt_app):
    from PySide6.QtWidgets import QApplication

    from app.ui.strategy_lab_workspace import TradeBlotter

    _ = qt_app
    blotter = TradeBlotter()
    blotter.show()
    QApplication.processEvents()
    blotter.set_result(_blotter_result(5000))
    # 5000 trades over 7 symbols; filtering to one symbol shows all of them
    blotter.set_symbol_filter("S003")
    assert blotter._table.rowCount() == 714  # 5000 // 7 rounded per modulo
    assert not blotter._count_label.isVisible()
    blotter.set_symbol_filter("ALL")
    assert blotter._table.rowCount() == TradeBlotter._ROW_CAP


def test_blotter_export_covers_all_trades(qt_app, tmp_path):
    from app.ui.strategy_lab_workspace import TradeBlotter

    _ = qt_app
    blotter = TradeBlotter()
    blotter.set_result(_blotter_result(2500))
    out = tmp_path / "trades.csv"
    from unittest.mock import patch

    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(out), "")):
        blotter._export_csv()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2501  # header + every trade, not just materialized rows


def test_perf_context_refreshes_on_selection(workspace: StrategyLabWorkspace) -> None:
    workspace.show()
    workspace.right_settings.set_symbols(("AAA", "BBB"))
    workspace.right_settings.set_selected_symbols(["AAA", "BBB"])
    QApplication.processEvents()
    assert workspace.metrics._context.text() == "BUY · LONG · 2 STOCKS ANALYZED"
    workspace.right_settings.set_selected_symbols([])
    QApplication.processEvents()
    assert "NO STOCKS SELECTED" in workspace.metrics._context.text()


def test_ranking_columns_pack_left_without_gap(qt_app):
    _ = qt_app
    from app.ui.stock_ranking import StockRankingWidget

    widget = StockRankingWidget()
    widget.show()
    try:
        assert not widget._table.horizontalHeader().stretchLastSection()
        widget.set_universe(("AAA", "BBB"))
        QApplication.processEvents()
        hdr = widget._table.horizontalHeader()
        gap = hdr.sectionViewportPosition(8) - (hdr.sectionViewportPosition(7) + hdr.sectionSize(7))
        assert gap == 0
    finally:
        widget.close()
        widget.deleteLater()


# ── result ownership + staleness state model (§2/§5/§6/§52/§53) ──


def _aligned_result(workspace: StrategyLabWorkspace):
    """A result whose config exactly matches the panel's live configuration."""
    from typing import Any, cast

    from backtest.models.config import BacktestConfig
    from backtest.models.metrics import PerformanceMetrics
    from backtest.models.result import StrategyResult

    cfg = cast("dict[str, Any]", workspace.right_settings.current_config())
    claimed = cfg.get("symbols") or ()
    symbols = tuple(str(s) for s in claimed) if isinstance(claimed, (tuple, list)) else ()

    def _num(key: str) -> float:
        try:
            return float(cfg.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return StrategyResult(
        strategy_id="s",
        name="S",
        config=BacktestConfig(
            symbol=symbols[0] if len(symbols) == 1 else "MULTI",
            timeframe=str(cfg.get("timeframe") or ""),
            start_date=str(cfg.get("start_date") or ""),
            end_date=str(cfg.get("end_date") or ""),
            initial_capital=_num("initial_capital"),
            slippage_pct=_num("slippage_pct"),
            commission_pct=_num("commission_pct"),
        ),
        trades=(),
        equity_curve=(),
        metrics=PerformanceMetrics(),
        bars_used=0,
        period_start=None,
        period_end=None,
    )


def _open_aligned(
    workspace: StrategyLabWorkspace, symbols: tuple[str, ...] = ("AAA", "BBB")
) -> None:
    workspace.show()
    workspace.open_strategy("S", 'strategy("S")\n')
    workspace.right_settings.set_timeframes(("15m",))
    workspace.right_settings.select_timeframe("15m")
    workspace.right_settings.set_symbols(symbols)
    workspace.right_settings.set_selected_symbols(list(symbols))
    workspace.set_last_run_symbols(symbols, refresh=False)
    QApplication.processEvents()


def test_fresh_run_shows_no_stale_banner(workspace: StrategyLabWorkspace) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    assert not workspace._stale_banner.isVisibleTo(workspace)
    assert workspace._stale_sides == ()
    assert workspace._buy_owner is not None
    assert "COMPLETE" in workspace.metrics._status.text()


def test_symbol_change_marks_buy_stale_not_current(
    workspace: StrategyLabWorkspace,
) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace.right_settings.set_selected_symbols(["AAA"])
    QApplication.processEvents()
    assert workspace._stale_banner.isVisibleTo(workspace)
    assert "OUT OF DATE" in workspace._stale_title.text()
    assert workspace._stale_sides == ("BUY",)
    # History is kept on screen but labeled — never silently current.
    assert workspace.metrics.isVisibleTo(workspace)


def test_rerun_after_change_clears_stale(workspace: StrategyLabWorkspace) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace.right_settings.set_selected_symbols(["AAA"])
    QApplication.processEvents()
    assert workspace._stale_banner.isVisibleTo(workspace)
    workspace.set_last_run_symbols(("AAA",), refresh=False)
    workspace.set_result(_aligned_result(workspace))
    assert not workspace._stale_banner.isVisibleTo(workspace)
    assert workspace._stale_sides == ()


def test_strategy_switch_clears_results(workspace: StrategyLabWorkspace) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace.open_strategy("Other", 'strategy("Other")\n')
    assert workspace.buy_result is None
    assert workspace.metrics._vals["NET PROFIT"].text() == "--"
    assert not workspace._stale_banner.isVisibleTo(workspace)
    assert "READY" in workspace.metrics._status.text()


def test_no_strategy_empty_neutralizes_dock(workspace: StrategyLabWorkspace) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace._set_center_empty(True)
    assert workspace._center_stack.currentIndex() == 0
    assert workspace._ranking_base() is None
    assert workspace.metrics._vals["NET PROFIT"].text() == "--"
    assert not workspace._stale_banner.isVisibleTo(workspace)
    assert "READY" in workspace.metrics._status.text()


def test_failed_run_labels_previous_results_historical(
    workspace: StrategyLabWorkspace,
) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace.set_run_state("failed")
    assert "FAILED" in workspace.metrics._status.text()
    assert workspace._stale_banner.isVisibleTo(workspace)
    assert "LAST RUN FAILED" in workspace._stale_title.text()
    workspace.clear()
    assert not workspace._stale_banner.isVisibleTo(workspace)
    assert "READY" in workspace.metrics._status.text()


def test_running_hides_banner_until_terminal_state(
    workspace: StrategyLabWorkspace,
) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace.right_settings.set_selected_symbols(["AAA"])
    QApplication.processEvents()
    assert workspace._stale_banner.isVisibleTo(workspace)
    workspace.set_run_state("running")
    assert not workspace._stale_banner.isVisibleTo(workspace)
    workspace.set_run_state("ready")
    # Fresh run never arrived — the mismatch is honestly back.
    assert workspace._stale_banner.isVisibleTo(workspace)


def test_mode_sides_have_independent_owners(
    workspace: StrategyLabWorkspace,
) -> None:
    from app.ui.strategy_lab_workspace import StrategyViewMode

    _open_aligned(workspace)
    # Isolated BUY run: only the BUY side owns a result (production
    # single-side path via _pending_side).
    workspace._pending_side = StrategyViewMode.BUY
    workspace.set_result(_aligned_result(workspace))
    assert workspace.sell_result is None
    workspace.set_view_mode("SELL")
    # SELL never ran: honest NOT-RUN placeholder, no stale banner.
    assert not workspace._stale_banner.isVisibleTo(workspace)
    workspace.right_settings.set_selected_symbols(["AAA"])
    QApplication.processEvents()
    assert not workspace._stale_banner.isVisibleTo(workspace)
    workspace.set_view_mode("BUY")
    assert workspace._stale_banner.isVisibleTo(workspace)
    assert workspace._stale_sides == ("BUY",)


def test_compare_mode_names_both_stale_sides(
    workspace: StrategyLabWorkspace,
) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace.right_settings.set_selected_symbols(["AAA"])
    QApplication.processEvents()
    workspace.set_view_mode("COMPARE")
    # The single-mode dock (and its banner) is hidden in COMPARE —
    # currency is labeled on the compare notice instead.
    notice = workspace._compare_view._stale_notice
    assert notice.isVisibleTo(workspace._compare_view)
    assert "BUY" in notice.text()
    assert "SELL" in notice.text()
    assert "OUT OF DATE" in notice.text()


def test_owner_matches_unit() -> None:
    from typing import Any

    from app.ui.strategy_lab_workspace import StrategyLabWorkspace

    base: dict[str, Any] = {
        "strategy": "S",
        "symbols": ("AAA", "BBB"),
        "timeframe": "15m",
        "start_date": "2023-01-01",
        "end_date": "2026-01-01",
        "initial_capital": 1000000.0,
        "slippage_pct": 0.02,
        "commission_pct": 0.03,
        "params": (),
    }
    assert StrategyLabWorkspace._owner_matches(dict(base), dict(base))
    assert StrategyLabWorkspace._owner_matches(None, dict(base))
    changed = dict(base, symbols=("AAA",))
    assert not StrategyLabWorkspace._owner_matches(changed, dict(base))
    changed = dict(base, timeframe="1h")
    assert not StrategyLabWorkspace._owner_matches(changed, dict(base))
    # Unknown params on either side never cry wolf; known mismatch does.
    assert StrategyLabWorkspace._owner_matches(dict(base), dict(base, params=(("k", 1.0),)))
    assert not StrategyLabWorkspace._owner_matches(
        dict(base, params=(("k", 1.0),)), dict(base, params=(("k", 2.0),))
    )


def test_stale_banner_run_button_reruns_current_config(
    workspace: StrategyLabWorkspace,
) -> None:
    _open_aligned(workspace)
    workspace.set_result(_aligned_result(workspace))
    workspace.right_settings.set_selected_symbols(["AAA"])
    QApplication.processEvents()
    received: list[object] = []
    workspace.run_backtest.connect(received.append)
    workspace._stale_run.click()
    assert len(received) == 1
    cfg = received[0]
    assert isinstance(cfg, dict) and tuple(cfg.get("symbols") or ()) == ("AAA",)


def test_metrics_reflow_narrow_and_back(workspace: StrategyLabWorkspace) -> None:
    from PySide6.QtWidgets import QGridLayout

    workspace.show()
    tier2 = workspace.metrics._tier2_host.layout()
    assert isinstance(tier2, QGridLayout)
    # QGridLayout.rowCount() never shrinks (high-water mark), so placement
    # is asserted via itemAtPosition, not rowCount.
    workspace.metrics.reflow(2)
    assert tier2.count() == 8  # no widget lost in the re-lay
    assert tier2.itemAtPosition(2, 0) is not None  # second key-row exists
    assert tier2.itemAtPosition(0, 2) is None  # third column folded away
    assert workspace.metrics._vals["WIN RATE"].text() == "--"
    workspace.metrics.reflow(2)  # idempotent
    assert tier2.count() == 8
    assert tier2.itemAtPosition(2, 0) is not None
    workspace.metrics.reflow(4)
    assert tier2.count() == 8
    assert tier2.itemAtPosition(2, 0) is None  # back to one key-row
    assert tier2.itemAtPosition(0, 3) is not None
    assert workspace.metrics._vals["WIN RATE"].text() == "--"


# ── real-layout regression: no squeeze, no clipping, compact banner ──


def _rect_in(widget, ancestor):
    """Widget rect in ancestor coordinates (mapTo takes points, not rects)."""
    from PySide6.QtCore import QRect

    top_left = widget.mapTo(ancestor, widget.rect().topLeft())
    return QRect(top_left, widget.rect().size())


def _open_with_result(workspace: StrategyLabWorkspace):
    _open_aligned(workspace, ("RELIANCE", "TCS", "INFY", "HDFCBANK"))
    workspace.set_result(_aligned_result(workspace))
    QApplication.processEvents()


def test_kpi_rows_never_clip_with_result_at_720p(
    workspace: StrategyLabWorkspace,
) -> None:
    workspace.resize(1280, 720)
    _open_with_result(workspace)
    QApplication.processEvents()
    for key, label in workspace.metrics._vals.items():
        assert label.isVisibleTo(workspace), key
        # Every value row keeps at least one full line height — nothing
        # is squeezed into overlap by a parent splitter anymore.
        assert label.height() >= label.fontMetrics().height(), key
    hero = workspace.metrics._vals["NET PROFIT"]
    assert hero.height() >= hero.fontMetrics().height()


def test_result_sections_never_overlap_with_result(
    workspace: StrategyLabWorkspace,
) -> None:
    workspace.resize(1600, 900)
    _open_with_result(workspace)
    QApplication.processEvents()
    sections = [workspace.metrics, workspace._strip, workspace._result_stack]
    rects = [_rect_in(s, workspace) for s in sections if s.isVisibleTo(workspace)]
    assert len(rects) == 3
    for first, second in zip(rects, rects[1:], strict=False):
        assert not first.intersects(second)


def test_stale_banner_stays_compact(workspace: StrategyLabWorkspace) -> None:
    workspace.resize(1600, 900)
    _open_with_result(workspace)
    workspace.right_settings.set_selected_symbols(["RELIANCE"])
    QApplication.processEvents()
    banner = workspace._stale_banner
    assert banner.isVisibleTo(workspace)
    # Compact warning: roughly one row, never a giant block.
    assert banner.height() <= 96
    assert "RUN AGAIN" in workspace._stale_run.text()


def test_page_scrolls_instead_of_squeezing_at_small_height(
    workspace: StrategyLabWorkspace,
) -> None:
    workspace.resize(1600, 600)
    _open_with_result(workspace)
    QApplication.processEvents()
    bar = workspace._page_scroll.verticalScrollBar()
    assert bar.maximum() > 0  # content overflows → scrolls, never compresses
    # Metrics keep full height even when the viewport is short.
    for label in workspace.metrics._vals.values():
        assert label.height() >= label.fontMetrics().height()


# ── header run-button skin + compile-line copy + config grid presence ──


def test_topbar_run_restores_idle_style_after_busy(
    workspace: StrategyLabWorkspace,
) -> None:
    from app.ui import lab_theme as t

    workspace.show()
    workspace.right_settings.set_busy(True)
    workspace.set_batch_progress(10, 100)
    assert workspace._topbar_run.text() == "■ STOP"
    assert workspace._topbar_run.styleSheet() == t.STOP_QSS
    workspace.right_settings.set_busy(False)
    QApplication.processEvents()
    assert workspace._topbar_run.styleSheet() == t.TOP_RUN_QSS
    assert "RUN" in workspace._topbar_run.text()


def test_compile_result_never_doubles_status_glyph(
    workspace: StrategyLabWorkspace,
) -> None:
    pane = workspace.center_detail
    pane.show_compile_result(True, "✓ COMPILED — 6 params · warmup 0 · Python")
    assert pane._status_msg.text() == "✓ COMPILED — 6 params · warmup 0 · Python"
    pane.show_compile_result(True, "Strategy compiled successfully (6 params)")
    assert pane._status_msg.text() == "✓ Strategy compiled successfully (6 params)"
    pane.show_compile_result(False, "✕ boom", 1, 1)
    assert pane._status_msg.text() == "✕ boom"


def test_config_grid_populated_in_backtest_tab(
    workspace: StrategyLabWorkspace,
) -> None:
    workspace.resize(1920, 1080)
    workspace.show()
    workspace.open_strategy("OBR", 'strategy("OBR")\n')
    workspace.center_detail.show_backtest()
    workspace.right_settings.set_timeframes(("30m",))
    workspace.right_settings.select_timeframe("30m")
    workspace.right_settings.set_symbols(("AAA", "BBB"))
    workspace.right_settings.set_selected_symbols(["AAA", "BBB"])
    QApplication.processEvents()
    panel = workspace.right_settings
    assert panel._grid_host.height() > 0
    for field in panel._grid_fields:
        assert field.isVisibleTo(panel), field
        assert field.height() > 0, field
    for caption in panel._grid_captions:
        assert caption.isVisibleTo(panel)
    assert panel._run.isVisibleTo(panel)


# ── run guard: no strategy open → no run, honest inline message ──


def test_run_blocked_with_no_strategy_open(
    workspace: StrategyLabWorkspace,
) -> None:
    workspace.show()
    assert workspace._center_stack.currentIndex() == 0
    runs: list = []
    workspace.run_backtest.connect(runs.append)
    workspace._emit_run()
    workspace._emit_run_all()
    QApplication.processEvents()
    assert runs == []
    assert workspace._pending_side is None
    assert workspace._topbar_status.isVisibleTo(workspace)
    assert "Open a strategy" in workspace._topbar_status.text()
    # Opening a strategy clears the hint; the next run goes through.
    workspace.open_strategy("OBR", 'strategy("OBR")\n')
    assert not workspace._topbar_status.isVisibleTo(workspace)
    workspace._emit_run()
    assert len(runs) == 1


def test_panel_run_blocked_with_no_strategy_open(
    workspace: StrategyLabWorkspace,
) -> None:
    workspace.show()
    runs: list = []
    workspace.run_backtest.connect(runs.append)
    workspace._on_panel_run({})
    assert runs == []
    assert "Open a strategy" in workspace.right_settings._error.text()
    workspace.open_strategy("OBR", 'strategy("OBR")\n')
    workspace._on_panel_run(workspace.right_settings.current_config())
    assert len(runs) == 1
