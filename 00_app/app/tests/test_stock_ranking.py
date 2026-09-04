"""Stock ranking — selection-only universe, honest metrics, mode consistency."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from backtest.engine.directional import derive_symbol_result
from backtest.engine.metrics import compute_equity_curve, compute_metrics
from backtest.models.config import BacktestConfig
from backtest.models.result import StrategyResult
from backtest.models.trade import TradeRecord
from PySide6.QtWidgets import QApplication

from app.ui.stock_ranking import StockRankingWidget, build_stock_ranking
from app.ui.strategy_lab_workspace import StrategyLabWorkspace


def _trade(symbol: str, side: str, day: int, pnl: float) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        side=side,
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


def _merged(trades: tuple[TradeRecord, ...], symbol: str = "MULTI") -> StrategyResult:
    config = BacktestConfig(
        symbol=symbol,
        timeframe="15m",
        start_date="2026-01-01",
        end_date="2026-01-10",
        initial_capital=100000.0,
    )
    curve = compute_equity_curve(trades, 100000.0, "2026-01-01 09:15:00")
    metrics = compute_metrics(trades, curve, 100000.0)
    return StrategyResult(
        strategy_id="s",
        name="S v1.0",
        config=config,
        trades=trades,
        equity_curve=curve,
        metrics=metrics,
        bars_used=100,
        period_start="2026-01-01 09:15:00",
        period_end="2026-01-10 09:15:00",
    )


def test_single_stock_ranked_first(qt_app: QApplication) -> None:
    _ = qt_app
    base = _merged((_trade("AAA", "LONG", 2, 50.0),))
    rows = build_stock_ranking(base, ("AAA",), {}, ("AAA",))
    assert len(rows) == 1
    assert rows[0].symbol == "AAA"
    assert rows[0].rank == 1
    assert rows[0].status == "ranked"
    assert rows[0].net_profit == 50.0


def test_two_stocks_best_first(qt_app: QApplication) -> None:
    _ = qt_app
    base = _merged(
        (
            _trade("AAA", "LONG", 2, 50.0),
            _trade("BBB", "LONG", 3, -20.0),
        )
    )
    rows = build_stock_ranking(base, ("AAA", "BBB"), {}, ("AAA", "BBB"))
    assert [row.symbol for row in rows] == ["AAA", "BBB"]
    assert [row.rank for row in rows] == [1, 2]
    assert rows[0].net_profit == 50.0
    assert rows[1].net_profit == -20.0


def test_five_plus_stocks_sorted_compact(qt_app: QApplication) -> None:
    _ = qt_app
    trades = tuple(_trade(f"S{i:02d}", "LONG", i + 1, float(i * 10 - 20)) for i in range(6))
    symbols = tuple(f"S{i:02d}" for i in range(6))
    base = _merged(trades)
    rows = build_stock_ranking(base, symbols, {}, symbols)
    assert len(rows) == 6
    nets = [row.net_profit for row in rows if row.status == "ranked"]
    ranked_nets: list[float] = [n for n in nets if n is not None]
    assert len(ranked_nets) == len(nets)
    assert ranked_nets == sorted(ranked_nets, reverse=True)
    assert rows[0].rank == 1
    # No fake metrics — ranking matches derive_symbol_result exactly.
    for row in rows:
        view = derive_symbol_result(base, row.symbol)
        assert view is not None
        assert row.net_profit == view.metrics.net_profit
        assert row.total_trades == view.metrics.total_trades


def test_failed_symbol_unavailable_no_fake_rank(qt_app: QApplication) -> None:
    _ = qt_app
    base = _merged((_trade("AAA", "LONG", 2, 50.0),))
    rows = build_stock_ranking(base, ("AAA", "BBB"), {"BBB": "no bars"}, ("AAA", "BBB"))
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["AAA"].rank == 1
    assert by_symbol["BBB"].rank is None
    assert by_symbol["BBB"].status == "unavailable"
    assert by_symbol["BBB"].net_profit is None


def test_zero_trade_symbol_not_ranked(qt_app: QApplication) -> None:
    _ = qt_app
    base = _merged((_trade("AAA", "LONG", 2, 50.0),))
    rows = build_stock_ranking(base, ("AAA", "ZZZ"), {}, ("AAA", "ZZZ"))
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["AAA"].rank == 1
    assert by_symbol["ZZZ"].rank is None
    assert by_symbol["ZZZ"].status == "no_trades"


def test_pending_before_run_and_not_in_last_run(qt_app: QApplication) -> None:
    _ = qt_app
    rows = build_stock_ranking(None, ("AAA", "BBB"), {}, None)
    assert all(row.status == "pending" and row.rank is None for row in rows)
    base = _merged((_trade("AAA", "LONG", 2, 50.0),))
    rows = build_stock_ranking(base, ("AAA", "NEW"), {}, ("AAA",))
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["AAA"].rank == 1
    assert by_symbol["NEW"].rank is None
    assert by_symbol["NEW"].status == "pending"


def test_widget_universe_updates_before_run(qt_app: QApplication) -> None:
    _ = qt_app
    widget = StockRankingWidget()
    widget.show()
    assert widget.universe == ()
    widget.set_universe(("AAA", "BBB"))
    assert widget.universe == ("AAA", "BBB")
    assert widget.ordered_symbols() == ("AAA", "BBB")
    assert widget.rank_for("AAA") is None
    assert widget.row_status("AAA") == "pending"
    widget.close()
    widget.deleteLater()


def test_widget_refresh_after_run_and_sortable(qt_app: QApplication) -> None:
    _ = qt_app
    widget = StockRankingWidget()
    widget.show()
    widget.set_universe(("AAA", "BBB"))
    base = _merged(
        (
            _trade("AAA", "LONG", 2, -10.0),
            _trade("BBB", "LONG", 3, 40.0),
        )
    )
    widget.set_results(base, {}, ("AAA", "BBB"), "BUY — LONG")
    assert widget.ordered_symbols() == ("BBB", "AAA")
    assert widget.rank_for("BBB") == 1
    assert widget.rank_for("AAA") == 2
    # Sortable: click TRADES header re-sorts, unranked stay bottom.
    widget._on_header_clicked(3)
    assert widget.rank_for("BBB") == 1
    widget.close()
    widget.deleteLater()


def test_widget_row_click_emits_symbol(qt_app: QApplication) -> None:
    _ = qt_app
    widget = StockRankingWidget()
    widget.show()
    widget.set_universe(("AAA",))
    base = _merged((_trade("AAA", "LONG", 2, 10.0),))
    widget.set_results(base, {}, ("AAA",), "")
    seen: list[str] = []
    widget.symbol_focused.connect(seen.append)
    widget._on_cell_clicked(0, 1)
    assert seen == ["AAA"]
    widget.close()
    widget.deleteLater()


def test_workspace_ranking_modes_stay_consistent(qt_app: QApplication) -> None:
    _ = qt_app
    workspace = StrategyLabWorkspace()
    workspace.show()
    try:
        workspace.right_settings.set_symbols(("AAA", "BBB"))
        workspace.right_settings.set_selected_symbols(("AAA", "BBB"))
        QApplication.processEvents()
        ranking = workspace.right_settings.ranking
        assert ranking.universe == ("AAA", "BBB")
        # Pending before any RUN — no fake ranks.
        assert ranking.rank_for("AAA") is None
        base = _merged(
            (
                _trade("AAA", "LONG", 2, 60.0),
                _trade("AAA", "SHORT", 3, -50.0),
                _trade("BBB", "LONG", 4, 10.0),
                _trade("BBB", "SHORT", 5, 30.0),
            )
        )
        workspace.set_last_run_symbols(("AAA", "BBB"))
        workspace.set_ranking_errors({})
        workspace.set_result(base)
        # COMPARE default? Workspace starts BUY — BUY ranks LONG only.
        workspace.set_view_mode("BUY")
        QApplication.processEvents()
        assert ranking.rank_for("AAA") == 1  # LONG +60 vs +10
        workspace.set_view_mode("SELL")
        QApplication.processEvents()
        assert ranking.rank_for("BBB") == 1  # SHORT +30 vs -50
        workspace.set_view_mode("COMPARE")
        QApplication.processEvents()
        # COMPARE ranks combined: AAA +10 vs BBB +40 → BBB first.
        assert ranking.ordered_symbols()[0] == "BBB"
    finally:
        workspace.close()
        workspace.deleteLater()


def test_workspace_single_symbol_preserved(qt_app: QApplication) -> None:
    _ = qt_app
    workspace = StrategyLabWorkspace()
    workspace.show()
    try:
        workspace.right_settings.set_symbols(("AAA",))
        workspace.right_settings.set_selected_symbols(("AAA",))
        QApplication.processEvents()
        base = _merged((_trade("AAA", "LONG", 2, 25.0),), symbol="AAA")
        workspace.set_result(base)
        ranking = workspace.right_settings.ranking
        assert ranking.universe == ("AAA",)
        assert ranking.rank_for("AAA") == 1
        assert ranking.row_status("AAA") == "ranked"
    finally:
        workspace.close()
        workspace.deleteLater()


def test_workspace_ranking_click_filters_journal(qt_app: QApplication) -> None:
    _ = qt_app
    workspace = StrategyLabWorkspace()
    workspace.show()
    try:
        workspace.right_settings.set_symbols(("AAA", "BBB"))
        workspace.right_settings.set_selected_symbols(("AAA", "BBB"))
        QApplication.processEvents()
        base = _merged(
            (
                _trade("AAA", "LONG", 2, 20.0),
                _trade("BBB", "LONG", 3, 5.0),
            )
        )
        workspace.set_last_run_symbols(("AAA", "BBB"))
        workspace.set_ranking_errors({})
        workspace.set_result(base)
        workspace.set_view_mode("BUY")
        QApplication.processEvents()
        workspace._on_ranking_focus("BBB")
        assert workspace.journal.symbol_filter == "BBB"
    finally:
        workspace.close()
        workspace.deleteLater()
