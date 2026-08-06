# strategies/ — Strategies

## What Is a Strategy?

A **trading strategy** is a set of rules that determines when to buy or sell. It answers:

- **Entry:** "When should I open a position?"
- **Exit:** "When should I close a position?"
- **Sizing:** "How much should I trade?"

Strategies consume **signals** (from signals/) and produce **order requests** (for execution/).

---

## Purpose

`strategies/` defines the trading logic of the system. Each strategy is a class that implements a standard interface, making strategies interchangeable and testable.

---

## Why This Folder Exists

Without `strategies/`, trading logic would be scattered across scripts, notebooks, and ad-hoc code. By putting every strategy in one place with a consistent interface:

- Strategies can be compared fairly
- Strategies can share code (position sizing, entry/exit rules)
- New strategies follow a known pattern
- Strategies are testable in isolation

---

## Visual Diagram

```
    signals/                       strategies/                    execution/
    
    Signal("rsi", 72)     ──▶  MomentumStrategy                OrderRequested
    Signal("macd", 0.5)   ──▶  .should_enter() → True           .symbol = "SPY"
    Signal("vol", 0.3)    ──▶  .generate_orders()               .side = "sell"
                                  │                              .quantity = 100
                                  ▼
                            PositionSizer
                            .calculate_size(capital, price, confidence)
                                  │
                                  ▼
                            StrategyRegistry
                            (catalog of available strategies)
```

---

## Folder Structure

```
strategies/
├── __init__.py      ← Public API: Strategy, PositionSizer, StrategyRegistry
├── README.md        ← This lesson
├── models/          ← Things: Strategy (abstract), PositionSizer, EntryExitRule
├── services/        ← Actions: orchestrate, registry
├── events/          ← Messages: OrderRequested, PositionTargeted
├── library/         ← Implementations: MomentumStrategy, MeanReversionStrategy
├── tests/
└── conftest.py
```

---

## Quick Example

```python
from strategies.library.momentum import MomentumStrategy
from strategies.models.position_sizer import PercentPositionSizer

# Create a strategy with its position sizer
strategy = MomentumStrategy(rsi_threshold=70.0)
sizer = PercentPositionSizer(percent=0.1)  # 10% of capital per trade

# The strategy is called by the engine on every tick:
# should_enter(context) → True/False
# generate_orders(context) → [OrderRequested, ...]
```

---

## Common Mistakes

| Mistake | Why |
|---|---|
| Putting multiple strategies in one file | One file per strategy. Clear ownership. |
| Mixing signal computation with strategy logic | Strategy should consume signals, not compute them. |
| Hard-coding position sizes | Use PositionSizer. Sizing is a separate concern. |
| Not handling strategy lifecycle | Every strategy needs on_start/on_stop for setup/cleanup. |

---

## Continue to the Next Lesson

→ `execution/` — How decisions become orders
