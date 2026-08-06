from typing import Sequence

from analytics.models.metrics import PerformanceMetrics


class PerformanceAnalyzer:
    def calculate(self, returns: Sequence[float], risk_free_rate: float = 0.05) -> PerformanceMetrics:
        if not returns:
            return PerformanceMetrics()
        total_return = ((1 + sum(returns) / len(returns)) ** len(returns)) - 1 if len(returns) > 0 else 0
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        volatility = variance ** 0.5
        negative_returns = [r for r in returns if r < 0]
        downside_var = sum(r ** 2 for r in negative_returns) / len(returns) if returns else 0
        downside_std = downside_var ** 0.5
        sharpe = (mean_return - risk_free_rate / 252) / volatility if volatility > 0 else 0
        sortino = (mean_return - risk_free_rate / 252) / downside_std if downside_std > 0 else 0
        peak = max(returns)
        trough = min(returns)
        max_dd = (trough - peak) / peak if peak != 0 else 0
        winning = sum(1 for r in returns if r > 0)
        win_rate = (winning / len(returns)) * 100 if returns else 0
        gains = sum(r for r in returns if r > 0)
        losses = abs(sum(r for r in returns if r < 0))
        profit_factor = gains / losses if losses > 0 else float("inf") if gains > 0 else 0
        return PerformanceMetrics(
            total_return_pct=total_return * 100,
            annualized_return_pct=mean_return * 252 * 100,
            volatility_pct=volatility * (252 ** 0.5) * 100,
            sharpe_ratio=sharpe * (252 ** 0.5),
            sortino_ratio=sortino * (252 ** 0.5),
            max_drawdown_pct=max_dd * 100,
            win_rate_pct=win_rate,
            total_trades=len(returns),
            avg_trade_return_pct=mean_return * 100,
            profit_factor=profit_factor,
        )
