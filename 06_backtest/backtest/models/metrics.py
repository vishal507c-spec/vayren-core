"""PerformanceMetrics — the 8 displayed backtest metrics."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PerformanceMetrics:
    """The 8 metrics shown in the Strategy Tester header row.

    All values are real computed results — None means unavailable (e.g.
    no trades). Callers format None as ``"--"``.

    Attributes:
        net_profit: Final equity minus initial capital (currency).
        net_profit_pct: ``(net_profit / initial_capital) * 100``.
        total_trades: Count of closed positions.
        win_rate: Winners / total (0-1), None when no trades.
        profit_factor: Gross profit / gross loss, None when undefined.
        max_drawdown_pct: Largest peak-to-trough drawdown, in percent.
        max_drawdown_abs: Same drawdown in currency.
        avg_trade: Mean P&L per trade (currency), None when no trades.
        expectancy: Mean P&L per trade (currency) — intentionally the same
            as ``avg_trade`` here, kept as a distinct field so future
            risk-scaled definitions do not require a UI change.
        sharpe_ratio: Sharpe of per-trade returns annualised from the mean
            holding period; None when undefined (fewer than 2 trades or
            zero variance).
        gross_profit: Sum of winning trade P&L (currency).
        gross_loss: Sum of losing trade P&L as a negative number (currency).
        starting_capital: The run's initial capital.
        ending_capital: Final equity after all trades.
    """

    net_profit: float = 0.0
    net_profit_pct: float = 0.0
    total_trades: int = 0
    win_rate: float | None = None
    profit_factor: float | None = None
    max_drawdown_pct: float = 0.0
    max_drawdown_abs: float = 0.0
    avg_trade: float | None = None
    expectancy: float | None = None
    sharpe_ratio: float | None = None
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    starting_capital: float = 0.0
    ending_capital: float = 0.0
