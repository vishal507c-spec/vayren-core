# risk/services/ — Risk Services

## What Is Inside

| Service | Responsibility |
|---|---|
| `PreTradeRisk` | Validates orders before submission against all active risk limits |
| `RiskMonitor` | Continuously monitors portfolio exposure against limits |
| `StressTestService` | Runs scenario simulations (market crash, volatility spike, gap down) |
