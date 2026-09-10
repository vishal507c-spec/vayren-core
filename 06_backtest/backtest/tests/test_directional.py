"""Directional + symbol result derivation tests."""

from backtest.engine.directional import (
    derive_directional_result,
    derive_symbol_result,
    derive_symbol_results,
)
from backtest.engine.metrics import compute_equity_curve, compute_metrics
from backtest.models.config import BacktestConfig
from backtest.models.result import StrategyResult
from backtest.models.trade import TradeRecord


def _trade(symbol: str, side: str, day: int, pnl: float) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        side=side,
        entry_index=day,
        exit_index=day + 1,
        entry_time=f"2026-01-{day:02d} 09:15:00",
        exit_time=f"2026-01-{day + 1:02d} 09:15:00",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=10.0,
        pnl=pnl,
        pnl_pct=pnl,
        commission=1.0,
        bars_held=1,
        exit_reason="SIGNAL",
    )


def _result() -> StrategyResult:
    trades = (
        _trade("AAA", "LONG", 2, 50.0),
        _trade("BBB", "SHORT", 3, -20.0),
        _trade("AAA", "SHORT", 4, 30.0),
    )
    config = BacktestConfig(
        symbol="AAA",
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
        bars_used=30,
        period_start="2026-01-01 09:15:00",
        period_end="2026-01-10 09:15:00",
    )


def test_derive_symbol_result_filters_by_symbol() -> None:
    base = _result()
    view = derive_symbol_result(base, "AAA")
    assert view is not None
    assert [t.symbol for t in view.trades] == ["AAA", "AAA"]
    assert view.metrics.total_trades == 2
    assert view.metrics.net_profit == 80.0
    # identity preserved
    assert view.strategy_id == base.strategy_id
    assert view.config is base.config
    assert view.period_start == base.period_start


def test_derive_symbol_result_empty_side_reports_zero_trades() -> None:
    base = _result()
    view = derive_symbol_result(base, "ZZZ")
    assert view is not None
    assert view.trades == ()
    assert view.metrics.total_trades == 0


def test_derive_symbol_result_none_base() -> None:
    assert derive_symbol_result(None, "AAA") is None


def test_derive_directional_still_splits_by_side() -> None:
    base = _result()
    view = derive_directional_result(base, "LONG")
    assert view is not None
    assert [t.side for t in view.trades] == ["LONG"]


def test_derive_symbol_results_matches_singular() -> None:
    base = _result()
    views = derive_symbol_results(base, ("AAA", "BBB", "ZZZ"))
    assert set(views) == {"AAA", "BBB", "ZZZ"}
    for symbol in ("AAA", "BBB", "ZZZ"):
        single = derive_symbol_result(base, symbol)
        grouped = views[symbol]
        assert single is not None
        assert grouped.trades == single.trades
        assert tuple(grouped.equity_curve) == tuple(single.equity_curve)
        assert grouped.metrics == single.metrics
        assert grouped.bars_used == single.bars_used
        assert grouped.period_start == single.period_start
        assert grouped.period_end == single.period_end


def test_derive_symbol_results_none_base() -> None:
    assert derive_symbol_results(None, ("AAA",)) == {}
