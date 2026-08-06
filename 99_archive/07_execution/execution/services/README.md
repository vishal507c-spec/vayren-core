# execution/services/ — Execution Services

## What Is Inside

| Service | Responsibility |
|---|---|
| `OrderManager` | Central coordinator: validates orders, submits to broker, tracks lifecycle, publishes events |
| `PositionManager` | Tracks open positions, updates P&L on fills, enforces position limits |
| `FillProcessor` | Processes incoming fills from broker, reconciles with pending orders |

---

## Dependencies

```
OrderManager → Broker (submits orders)
             → FillProcessor (handles fills)
             → PositionManager (updates positions)
             → EventBus (publishes order events)
```
