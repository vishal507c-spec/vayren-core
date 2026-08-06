# execution/events/ — Execution Events

## What Is Inside

| Event | When It Is Published |
|---|---|
| `OrderSubmitted` | Order has been sent to broker |
| `OrderFilled` | Order has been fully or partially executed |
| `OrderRejected` | Broker rejected the order |
| `OrderCancelled` | Order was cancelled before filling |
| `PositionOpened` | New position created (first fill for a symbol) |
| `PositionClosed` | Position reduced to zero |
