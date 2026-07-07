# analytics/models/ — Analytics Models

## What Is Inside

| Model | What It Represents | Key Fields |
|---|---|---|
| `PerformanceMetrics` | Summary statistics for a strategy or portfolio | total_return, sharpe_ratio, sortino_ratio, max_drawdown, win_rate, profit_factor, total_trades |
| `Attribution` | Which factors contributed to returns | factor_name, contribution_pct, time_series_of_exposures |
| `Report` | A structured analytics output | metrics, attribution, charts, period |
