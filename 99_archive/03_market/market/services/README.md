# market/services/ — Services

## What Are Services?

**Services** are classes that perform actions on models. If models are nouns (things), services are verbs (actions).

Services contain the **business logic** of a department — the operations that make the department useful.

---

## What Is Inside

| Service | What It Does |
|---|---|
| `MarketDataIngestion` | Normalizes raw data from external sources into our standard models |
| `MarketDataQuery` | Provides an interface for querying stored market data |
| `DataQualityService` | Validates data correctness before it propagates downstream |

---

## Relationship to Models

```
Service (verb)          Model (noun)
────────────────────────────────────
Ingestion.normalize()  → Bar, Trade
Query.get_bars()       → returns list[Bar]
Quality.validate()     → accepts Bar, returns issues
```
