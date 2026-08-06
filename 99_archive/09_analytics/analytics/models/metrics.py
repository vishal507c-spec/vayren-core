from dataclasses import dataclass, field


@dataclass
class PerformanceMetrics:
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    volatility_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    total_trades: int = 0
    avg_trade_return_pct: float = 0.0
    profit_factor: float = 0.0

    @property
    def is_profitable(self) -> bool:
        return self.total_return_pct > 0

    @property
    def risk_adjusted_return(self) -> float:
        if self.volatility_pct == 0:
            return 0.0
        return self.total_return_pct / self.volatility_pct
