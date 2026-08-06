# strategies/events/ — Strategy Events

## What Is Inside

| Event | When It Is Published |
|---|---|
| `StrategyStarted` | Strategy has been activated and is listening for signals |
| `StrategyStopped` | Strategy has been deactivated |
| `StrategyUpdated` | Strategy configuration changed |
| `OrderRequested` | Strategy wants to place an order (consumed by execution) |

---

## OrderRequested Is the Bridge

`OrderRequested` is the event that crosses the **strategies → execution** boundary. The execution department subscribes to this event and converts it to an actual order submission.
