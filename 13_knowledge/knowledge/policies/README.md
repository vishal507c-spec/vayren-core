# knowledge/policies/ — Policies

## What Are Policies?

**Policies** are the operating rules of the trading system. They define boundaries and constraints:

- What is the maximum position size?
- How much can we lose in a day?
- How long do we keep market data?

---

## Why This Exists

Policies encode **operational knowledge** that is separate from code. Changing a policy (e.g., "reduce max position size") should not require changing code. It should be a configuration change.

---

## Available Policies

| Policy | What It Defines |
|---|---|
| `risk-limits.yaml` | Maximum position size, order value, daily loss, concentration, leverage |
| `data-retention.yaml` | How long different types of data are kept |
