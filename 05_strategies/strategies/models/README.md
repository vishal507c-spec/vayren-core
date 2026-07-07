# strategies/models/ — Strategy Models

## What Is Inside

| Model | What It Represents | Key Fields |
|---|---|---|
| `StrategyConfig` | Configuration for a strategy instance | name, parameters dict, symbols, status (active/paused/stopped) |
| `PositionSizer` | Determines position size given market conditions | base_size, max_size, risk_per_trade, method (fixed/kelly/risk_pct) |
| `SignalThreshold` | Maps signal values to entry/exit decisions | signal_name, entry_threshold, exit_threshold, direction (long/short) |
