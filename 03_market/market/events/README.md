# market/events/ — Events

## What Are Events?

**Events** are immutable messages saying that something **happened**. They use past tense because they describe a completed fact:

- `BarReceived` — a bar was received
- `TradeReceived` — a trade was received

Events enable **event-driven architecture**: departments communicate by publishing and subscribing to events, without knowing about each other's internal code.

---

## What Is Inside

| Event | Published When | Contains |
|---|---|---|
| `BarReceived` | A new Bar is ingested and validated | The Bar itself, plus the data source name |
| `TradeReceived` | A new Trade is ingested | The Trade itself, plus the data source name |

---

## Event Flow

```
market/services/ingestion.py
  ↓ normalizes data
  ↓ validates quality
  ↓ publishes event
      ↓
  BarReceived(event_bus)
      ↓
  signals/ receives: "New bar available for SPY"
  strategies/ receives: "New data to evaluate"
  Other departments receive: "Something happened, react if needed"
```
