"""Metrics — compute real metrics from closed trades and an equity curve.

All 8 displayed metrics plus helpers for equity + drawdown series. Every
value is deterministic and derived only from real trade data; when a metric
is undefined its field is None and callers show ``"--"``.
"""

from __future__ import annotations

import math

from backtest.models.equity import EquityPoint
from backtest.models.metrics import PerformanceMetrics
from backtest.models.trade import TradeRecord


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
    points: list[EquityPoint] = []
    equity = initial_capital
    peak = initial_capital
    if start_time:
        points.append(EquityPoint(timestamp=start_time, equity=initial_capital, drawdown_pct=0.0))
    else:
        points.append(
            EquityPoint(timestamp=trades[0].entry_time, equity=initial_capital, drawdown_pct=0.0)
        )
    for trade in trades:
        equity += trade.pnl
        if equity > peak:
            peak = equity
        dd_pct = (peak - equity) / peak * 100.0 if peak else 0.0
        points.append(EquityPoint(timestamp=trade.exit_time, equity=equity, drawdown_pct=dd_pct))
    return tuple(points)


def _max_drawdown(curve: tuple[EquityPoint, ...]) -> tuple[float, float]:
    if not curve:
        return 0.0, 0.0
    peak = curve[0].equity
    max_pct = 0.0
    max_abs = 0.0
    for point in curve:
        if point.equity > peak:
            peak = point.equity
        dd_abs = peak - point.equity
        dd_pct = (dd_abs / peak * 100.0) if peak else 0.0
        if dd_pct > max_pct:
            max_pct = dd_pct
        if dd_abs > max_abs:
            max_abs = dd_abs
    return max_pct, max_abs


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
    equity = initial_capital
    returns: list[float] = []
    for trade in trades:
        before = equity
        equity += trade.pnl
        if before <= 0.0:
            continue
        returns.append(equity / before - 1.0)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0.0:
        return None
    std = math.sqrt(variance)
    mean_bars = sum(t.bars_held for t in trades) / len(trades) if trades else 1
    bars_per_day = 25.0
    trades_per_year = (252.0 * bars_per_day) / max(1.0, mean_bars)
    annual_factor = math.sqrt(trades_per_year)
    return (mean / std) * annual_factor if std else None
