# market/adapters/ — Adapters

## What Are Adapters?

**Adapters** bridge our system to external systems. They "adapt" the outside world's interface to our internal interface.

The **adapter pattern** is a design pattern that allows two incompatible interfaces to work together.

---

## What Is Inside

| Adapter | External System | Format Converts From → To |
|---|---|---|
| `PolygonAdapter` | Polygon.io REST API | Polygon JSON → Our model format |
| `IEXAdapter` | IEX Cloud API | IEX JSON → Our model format |
| `CSVImportAdapter` | Local CSV files | CSV rows → Our model format |

---

## Why Adapters Exist

Every external data source sends data in a different format:
- Polygon uses short keys: `{"o": 450.0, "h": 455.0}`
- IEX uses descriptive keys: `{"open": 450.0, "high": 455.0}`
- CSV files use whatever the user named the columns

Adapters convert all of these into our standard models, so the rest of the system never needs to know about external formats.

---

## The Adapter Pattern

```
External API  ──▶  Adapter  ──▶  Normalized Model  ──▶  Rest of System
(Polygon)          (converts)     (Bar, Trade)          (doesn't know Polygon exists)
```

To add a new data source, you write one new adapter. Nothing else changes.
