# execution/models/ — Order Models

## What Is Inside

| Model | What It Represents | Key Fields |
|---|---|---|
| `Order` | A request to buy or sell | symbol, side (buy/sell), quantity, type (market/limit), status (pending/submitted/filled/rejected) |
| `Fill` | A confirmed execution of an order | order_id, symbol, side, quantity, price, timestamp |
| `Position` | A currently held amount of a symbol | symbol, quantity, avg_cost, current_price, realized_pnl, unrealized_pnl |

---

## The Order Lifecycle

```
pending → submitted → partially_filled → filled
                  ↘ rejected
```

Each status transition publishes a corresponding event.

---

## Quick Example

```python
from execution.models.order import Order

order = Order(symbol="SPY", side="buy", quantity=100, order_type="market")
print(order.is_buy)           # True
print(order.is_pending)       # True
print(order.remaining_quantity)  # 100
```
