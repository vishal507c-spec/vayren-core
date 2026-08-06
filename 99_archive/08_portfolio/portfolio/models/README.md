# portfolio/models/ — Portfolio Models

## What Is Inside

| Model | What It Represents | Key Fields |
|---|---|---|
| `Portfolio` | The aggregate account | total_value, cash, positions, timestamp |
| `Allocation` | A target assignment for a symbol | symbol, target_weight, min_weight, max_weight |
| `PnL` | Profit and loss for a period | realized_pnl, unrealized_pnl, total_pnl, period_start, period_end |
