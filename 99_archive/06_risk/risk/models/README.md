# risk/models/ — Risk Models

## What Is Inside

| Model | What It Represents | Key Fields |
|---|---|---|
| `RiskLimit` | A configurable boundary | limit_type (max_position/max_orders/max_daily_loss/max_leverage), value, current_usage |
| `Exposure` | Current risk exposure | symbol, net_exposure, gross_exposure, var, beta |
| `StressTestResult` | Scenario simulation output | scenario_name, max_drawdown, worst_loss, pass/fail |
