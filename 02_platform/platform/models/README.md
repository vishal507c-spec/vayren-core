# platform/models/ — Platform Models

## What Is Inside

| Model | What It Represents | Key Fields |
|---|---|---|
| `Engine` | The runtime coordinator | mode (backtest/paper/live), status (starting/running/stopping/stopped), clock |
| `Mode` | The execution environment | type (backtest/paper/live), name (optional), is_live |
| `Clock` | Time management abstraction | now, speed (live = 1.0, backtest = variable), trading_calendar |
| `Event` | Base event model | event_type, timestamp, source, payload |
