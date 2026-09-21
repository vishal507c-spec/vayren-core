"""Metrics — the display metrics are assembled by the Rust report kernel.

All 8 displayed metrics plus the equity + drawdown series come from
`rust/vayren-core` (`backtest_engine::report_for_curve`, `metrics`); this
module only maps domain models to the kernel's float sequences and the
kernel's aggregate back to `PerformanceMetrics`. Timestamps and the
`"--"`-vs-value distinction stay Python domain concerns.
"""

from __future__ import annotations

from backtest.models.equity import EquityPoint
from backtest.models.metrics import PerformanceMetrics
from backtest.models.trade import TradeRecord
from backtest.native_metrics import equity_curve_points
from backtest.native_metrics import report as _native_report


def compute_metrics(
    trades: tuple[TradeRecord, ...],
    equity_curve: tuple[EquityPoint, ...],
    initial_capital: float,
) -> PerformanceMetrics:
    """Ask the report kernel to aggregate `trades` against `equity_curve`."""
    report = _native_report(
        pnls=[trade.pnl for trade in trades],
        bars_held=[float(trade.bars_held) for trade in trades],
        equities=[point.equity for point in equity_curve],
        initial_capital=initial_capital,
    )
    return PerformanceMetrics(
        net_profit=report.net_profit,
        net_profit_pct=report.net_profit_pct,
        total_trades=report.total_trades,
        win_rate=report.win_rate,
        profit_factor=report.profit_factor,
        max_drawdown_pct=report.max_drawdown_pct,
        max_drawdown_abs=report.max_drawdown_abs,
        avg_trade=report.avg_trade,
        expectancy=report.expectancy,
        sharpe_ratio=report.sharpe_ratio,
        gross_profit=report.gross_profit,
        gross_loss=report.gross_loss,
        starting_capital=report.starting_capital,
        ending_capital=report.ending_capital,
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
