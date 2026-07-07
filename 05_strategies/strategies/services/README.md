# strategies/services/ — Strategy Services

## What Is Inside

| Service | Responsibility |
|---|---|
| `StrategyRegistry` | Maps strategy names to strategy classes (plugin registry) |
| `StrategyRunner` | Orchestrates strategy lifecycle: init → on_start → on_bar → signal_check → order_gen → on_stop |
| `PositionSizer` | Computes position quantity from configurable sizing rules (fixed, risk %, Kelly) |

---

## How a Strategy Runs

```
BarReceived → StrategyRunner → Strategy.should_enter()
                              → PositionSizer.compute_size()
                              → Strategy.generate_orders()
                              → OrderRequested (event)
```
