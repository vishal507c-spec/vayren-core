"""Metrics — compute real metrics from closed trades and an equity curve.

All 8 displayed metrics plus helpers for equity + drawdown series. Every
value is deterministic and derived only from real trade data; when a metric
is undefined its field is None and callers show ``"--"``.

The numeric kernels (drawdown, equity accumulation, Sharpe) are owned by
Rust (`rust/vayren-core`, `metrics` module); this module marshals domain
models and assembles `PerformanceMetrics`.
"""

from __future__ import annotations

from backtest.models.equity import EquityPoint
from backtest.models.metrics import PerformanceMetrics
from backtest.models.trade import TradeRecord
from backtest.native_metrics import equity_curve_points
from backtest.native_metrics import max_drawdown as _native_drawdown
from backtest.native_metrics import sharpe as _native_sharpe


def compute_metrics(
    trades: tuple[TradeRecord, ...],
    equity_curve: tuple[EquityPoint, ...],
    initial_capital: float,
) -> PerformanceMetrics:
    """Derive the display metrics from `trades` and `equity_curve`."""
    total = len(trades)
    if total == 0:
        final = equity_curve[-1].equity if equity_curve else initial_capital
        return PerformanceMetrics(
            net_profit=final - initial_capital,
            net_profit_pct=(final - initial_capital) / initial_capital * 100.0
            if initial_capital
            else 0.0,
            total_trades=0,
            win_rate=None,
            profit_factor=None,
            max_drawdown_pct=0.0,
            max_drawdown_abs=0.0,
            avg_trade=None,
            expectancy=None,
            sharpe_ratio=None,
            gross_profit=0.0,
            gross_loss=0.0,
            starting_capital=initial_capital,
            ending_capital=final,
        )

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0.0)
    gross_loss = sum(t.pnl for t in trades if t.pnl < 0.0)
    wins = sum(1 for t in trades if t.winning)
    win_rate = wins / total
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0.0 else None
    avg_trade = sum(t.pnl for t in trades) / total if total else None
    expectancy = avg_trade

    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    max_dd_pct, max_dd_abs = _max_drawdown(equity_curve)
    sharpe = _sharpe(trades, equity_curve, initial_capital)

    return PerformanceMetrics(
        net_profit=final_equity - initial_capital,
        net_profit_pct=(final_equity - initial_capital) / initial_capital * 100.0
        if initial_capital
        else 0.0,
        total_trades=total,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_abs=max_dd_abs,
        avg_trade=avg_trade,
        expectancy=expectancy,
        sharpe_ratio=sharpe,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        starting_capital=initial_capital,
        ending_capital=final_equity,
    )


def compute_equity_curve(
    trades: tuple[TradeRecord, ...],
    initial_capital: float,
    start_time: str | None,
) -> tuple[EquityPoint, ...]:
    """Build the equity curve: starting point + one point per closed trade."""
    if not trades:
        t = start_time or "—"
        return (EquityPoint(timestamp=t, equity=initial_capital, drawdown_pct=0.0),)
    first_stamp = start_time or trades[0].entry_time
    points = [EquityPoint(timestamp=first_stamp, equity=initial_capital, drawdown_pct=0.0)]
    # Accumulation math is Rust-owned; timestamps stay a Python domain concern.
    pairs = equity_curve_points(initial_capital, [trade.pnl for trade in trades])
    for trade, (equity, drawdown) in zip(trades, pairs, strict=True):
        points.append(EquityPoint(timestamp=trade.exit_time, equity=equity, drawdown_pct=drawdown))
    return tuple(points)


def _max_drawdown(curve: tuple[EquityPoint, ...]) -> tuple[float, float]:
    """Peak-to-trough drawdown, computed by the Rust kernel."""
    if not curve:
        return 0.0, 0.0
    return _native_drawdown([point.equity for point in curve])


def _sharpe(
    trades: tuple[TradeRecord, ...],
    _curve: tuple[EquityPoint, ...],  # noqa: ARG002
    initial_capital: float,
) -> float | None:
    """Sharpe of per-trade equity returns, annualised by mean holding period.

    Returns are ``equity_after / equity_before − 1`` per closed trade;
    annualisation uses ``trades_per_year ≈ 252 * avg_bars_per_day /
    mean_bars_held`` with 25 trading bars per day (~NSE 15m session).
    None when fewer than 2 trades or zero variance.
    """
    if len(trades) < 2:
        return None
    return _native_sharpe(
        [trade.pnl for trade in trades],
        [trade.bars_held for trade in trades],
        initial_capital,
    )
