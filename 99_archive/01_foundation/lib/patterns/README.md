# lib/patterns/ — Design Patterns

## What Are Design Patterns?

**Design patterns** are proven, reusable solutions to common problems in software engineering.

They are not code you copy-paste. They are **templates** — ways of structuring code that experienced engineers have found to work well.

A pattern has:
- A **name** (so engineers can discuss it: "Let's use a Registry here")
- A **problem** it solves
- A **solution** (the structure of the code)
- **Consequences** (trade-offs of using it)

---

## What Is Inside

| Pattern | Problem It Solves | When To Use It |
|---|---|---|
| `Registry` | "I need to keep track of available items (strategies, signals, brokers) and look them up by name" | When you have a collection of named items that can be registered and retrieved |
| `Singleton` | "I need exactly one instance of this class to exist" | For shared resources like configuration or database connections |
| `Observable` | "I need to notify multiple parts of the system when something happens" | For event-driven communication between departments |

---

## Quick Example

```python
from lib.patterns.registry import Registry
from strategies.strategy import Strategy

# Create a registry for strategies
strategy_registry = Registry[Strategy]()

# Register strategies by name
strategy_registry.register("momentum", MomentumStrategy)
strategy_registry.register("mean_reversion", MeanReversionStrategy)

# Look up a strategy by name
strategy = strategy_registry.get("momentum")
```

---

## Continue to the Next Lesson

→ `market/` — Market Data Department
