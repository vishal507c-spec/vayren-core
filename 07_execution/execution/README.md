# execution/ — Execution

## What Is Execution?

**Execution** is the process of turning a trading decision into an actual order sent to a broker. It manages:

- **Order lifecycle:** pending → submitted → filled (or rejected)
- **Broker connectivity:** talking to Alpaca, Interactive Brokers, etc.
- **Fill reconciliation:** matching our records with the broker's records
- **Position tracking:** what do we currently hold?

---

## Purpose

`execution/` manages everything that happens after a strategy decides to trade. It handles the mechanical details of placing, tracking, and confirming orders.

---

## Why This Folder Exists

Execution is complex. Orders can be:
- **Submitted** (sent to broker) → **Filled** (executed) → **Rejected** (cancelled)
- **Partially filled** (only some shares executed) → **Remaining** (rest stays open)
- **Cancelled** (manually or by the broker)

Without a dedicated department, this complexity would leak into strategies.

---

## Visual Diagram

```
    strategies/                     execution/                         Broker
    
    OrderRequested       ──▶  OrderManager          ──▶  AlpacaBroker.submit()
    .symbol = "SPY"            .submit(order)             .post("/v2/orders")
    .side = "buy"                   │
    .quantity = 100                 ▼                              │
                               OrderSubmitted                    │
                               event published                    │
                                                                  ▼
                               ┌─────────────────────────────────────┐
                               │  Broker responds: filled at $450.50 │
                               └─────────────────────────────────────┘
                                      │
                                      ▼
                               FillValidator.validate()
                                      │
                                      ▼
                               OrderFilled event published
                                      │
                                      ▼
                               OrderManager._update_position()
                                      │
                                      ▼
                               Position(symbol="SPY", quantity=100, avg_cost=450.50)
```

---

## Folder Structure

```
execution/
├── __init__.py      ← Public API: Order, Fill, Position, OrderManager
├── README.md        ← This lesson
├── models/          ← Things: Order, Fill, Position
├── services/        ← Actions: order management, fill validation, commission
├── events/          ← Messages: OrderSubmitted, OrderFilled, OrderRejected
├── brokers/         ← Connections: Alpaca, Simulated
├── tests/
└── conftest.py
```

---

## Quick Example

```python
from execution.models.order import Order
from execution.services.order_manager import OrderManager
from execution.models.fill import Fill

# Create an order
order = Order(symbol="SPY", side="buy", quantity=100, order_type="market", id="ord-001")

# Submit it
manager = OrderManager()
event = manager.submit(order)
print(f"Order {event.order.id}: {event.order.status}")  # "submitted"

# Later, a fill arrives
fill = Fill(order_id="ord-001", symbol="SPY", side="buy", quantity=100, price=450.50, timestamp="2025-07-05T20:00:00Z")
event = manager.fill(fill)

# Check position
position = manager.get_position("SPY")
print(f"Position: {position.quantity} @ {position.avg_cost}")  # "100 @ 450.50"
```

---

## Continue to the Next Lesson

→ `portfolio/` — How positions are tracked
