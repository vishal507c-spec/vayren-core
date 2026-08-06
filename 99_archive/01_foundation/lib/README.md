# lib/ — Library

## What Is a Library?

A **library** is a collection of reusable code that other parts of a system can use. It is not a standalone program — it is a toolkit that programs import from.

Think of a library as a **hardware store**. When you need a hammer, you go to the hardware store. You do not forge your own hammer every time. The hardware store contains tools that many different workers need, and every tool is tested, reliable, and consistent.

In professional software engineering, libraries are distinguished from **frameworks**:

- **Library:** Your code calls the library. You are in control.
- **Framework:** The framework calls your code. It is in control.

`lib/` is a library. You are always in control.

---

## Purpose

`lib/` provides the **basic building blocks** that every other department needs.

It contains:
- **Types** (things like Currency, Timestamp) that represent fundamental concepts
- **Utilities** (tools like time helpers, math helpers) that perform common operations
- **Configuration** (loading and defining settings)
- **Logging** (recording what the system does)
- **Patterns** (proven solutions to recurring problems)

---

## Why This Folder Exists

If every department had to build its own basic tools, they would:

1. Build them differently (inconsistent behavior)
2. Build them wrong (buggy implementations)
3. Waste time rebuilding the same thing (duplication)

`lib/` provides **one correct version** that everyone shares. A bug fixed in `lib/` is fixed for every department at once.

---

## What Problem Does It Solve?

**Duplication and inconsistency.** Without a shared library, every department would need to define its own concept of "money" or "time" or "configuration." These would inevitably differ in subtle ways, leading to bugs when departments interact.

---

## Mental Model

Imagine you are building a house.

Before you can build walls (departments), you need:
- A **foundation** (this folder — `lib/`)
- A **hammer, saw, and measuring tape** (`utils/`)
- A way to know what a "2x4" means (`types/`)

`lib/` is all of these. It is not exciting. It is not visible. But everything depends on it.

---

## Real World Analogy

A library is like the **concrete foundation** of a skyscraper. Nobody visits the building to see the foundation. But without it, the building collapses.

---

## Visual Diagram

```
                        lib/
                         │
                         │  "I provide tools. I need nothing."
                         │
    ┌────────────────────┼────────────────────┐
    │                    │                    │
    ▼                    ▼                    ▼
┌─────────┐        ┌─────────┐          ┌─────────┐
│  MARKET │        │ SIGNALS │          │STRATEGIES│
│  DEPT   │        │  DEPT   │          │  DEPT   │
└─────────┘        └─────────┘          └─────────┘
    │                    │                    │
    └────────────────────┼────────────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │ OTHER DEPTS  │
                  │ (execution,  │
                  │ portfolio,   │
                  │ risk, etc.)  │
                  └──────────────┘

EVERY department uses lib/.  lib/ uses NOTHING inside the project.
```

---

## Where It Fits in the System

`lib/` is the **bottom layer** of the entire system. Every other department depends on it. It depends on nothing internal.

```
Dependency direction:  EVERYTHING → lib/ → nothing
```

---

## What Comes Before It

Nothing. `lib/` is the first department you should read after the root documentation. Understanding `lib/` first makes every subsequent department easier to understand.

---

## What Comes After It

`market/` — the first department that does real work using `lib/` types.

After `lib/`, you can read any department. But the recommended order is:

```
lib/ → market/ → signals/ → strategies/ → execution/ → portfolio/ → risk/ → analytics/ → research/
```

---

## Folder Structure

```
lib/
├── __init__.py      ← Public API: exports everything other departments need
├── README.md        ← This lesson
├── types/           ← Core value objects (Currency, Timestamp, Bounded, Nullable)
├── utils/           ← Utility functions (time, math, serialization, decorators)
├── config/          ← Configuration loading and schema definition
├── logging/         ← Centralized logging setup
├── patterns/        ← Reusable design patterns (Registry, Singleton, Observable)
└── tests/           ← Tests for all lib components
```

---

## What Every Subfolder Means

| Subfolder | Purpose | Key Files |
|---|---|---|
| `types/` | **Things** — fundamental concepts represented as code | `currency.py`, `timestamp.py`, `bounded.py`, `nullable.py` |
| `utils/` | **Tools** — functions that perform common operations | `time_utils.py`, `math_utils.py`, `serialization.py`, `decorators.py` |
| `config/` | **Settings** — how to load and define configuration | `loader.py`, `schemas.py` |
| `logging/` | **Recording** — how to keep a record of what the system does | `setup.py` |
| `patterns/` | **Solutions** — proven ways to solve common problems | `registry.py`, `singleton.py`, `observable.py` |

---

## What Every Important File Means

| File | What It Defines | Why It Exists |
|---|---|---|
| `types/currency.py` | `Currency` — exact monetary amounts | Computers are bad at decimal math. Currency uses proper decimal arithmetic. |
| `types/timestamp.py` | `Timestamp` — precise moments in time | Trading requires knowing exactly when something happened. |
| `types/bounded.py` | `Bounded` — a value with minimum and maximum limits | Many trading concepts have valid ranges (e.g., position size 0-1000). |
| `types/nullable.py` | `Nullable` — a value that might be missing | Data is often incomplete. Nullable makes missing data explicit. |
| `utils/time_utils.py` | `now_utc()`, `to_iso()`, `parse_iso()` | Working with time is surprisingly complex. These functions make it simple. |
| `utils/math_utils.py` | `round_to_tick()`, `weight()` | Common financial math operations. |
| `utils/serialization.py` | `to_json()`, `from_json()` | Converting data to/from storage formats. |
| `utils/decorators.py` | `@retry`, `@timed` | Common function wrappers for retry logic and timing. |
| `config/loader.py` | `load_config()`, `save_config()` | Reading settings from YAML files. |
| `config/schemas.py` | `AppConfig`, `MarketConfig` | Defining what valid settings look like. |
| `logging/setup.py` | `configure_logging()` | Setting up the system's record-keeping. |
| `patterns/registry.py` | `Registry[T]` | A catalog of available items (strategies, signals, etc.). |
| `patterns/singleton.py` | `Singleton` | Ensures only one instance of a class exists. |
| `patterns/observable.py` | `Observable`, `Observer` | A pattern for one-to-many notifications. |

---

## Data Flow

```
Data does not flow through lib/.

lib/ does not process data. lib/ provides TOOLS that data processors use.

    ┌──────────────────────────────────────────┐
    │             Other Departments             │
    │                                          │
    │  market/  ──uses──▶  lib/types/          │
    │  signals/ ──uses──▶  lib/utils/          │
    │  platform/──uses──▶  lib/config/         │
    │  ...       ──uses──▶  lib/logging/       │
    │                    ▶  lib/patterns/      │
    └──────────────────────────────────────────┘
```

---

## Dependency Flow

```
lib/ depends on:
  ├── Python standard library (os, datetime, decimal, json, etc.)
  ├── pydantic (for config schemas)
  └── pyyaml (for config files)

lib/ does NOT depend on:
  └── Any other department in this project
```

This is the most important rule: **`lib/` imports nothing from inside the project.** This ensures that `lib/` can never cause circular dependencies, and it can always be trusted to work independently.

---

## Typical Workflow

You rarely work directly inside `lib/`. You mostly import from it:

```python
# Step 1: Import what you need
from lib.types.currency import Currency
from lib.utils.time_utils import now_utc

# Step 2: Use it
price = Currency("450.50", "USD")
timestamp = now_utc()
```

---

## Quick Example

```python
# --- types/currency.py ---
from lib.types.currency import Currency

# Create a monetary amount
price = Currency("150.50", "USD")
fee = Currency("1.50", "USD")

# Arithmetic — always precise
total = price + fee       # Currency("152.00", "USD")
half = price / 2          # Currency("75.25", "USD")

# Comparison
if price > Currency("100", "USD"):
    print("Over $100")    # This prints

# Display
print(str(price))         # "USD 150.50"
```

```python
# --- types/timestamp.py ---
from lib.types.timestamp import Timestamp

# Current moment
now = Timestamp.now()
print(now.iso)            # "2025-07-05T20:00:00.000000Z"

# From a string
meeting = Timestamp.from_iso("2025-07-06T09:30:00Z")
print(meeting.epoch)      # Seconds since Unix epoch
```

```python
# --- utils/time_utils.py ---
from lib.utils.time_utils import now_utc, to_iso

print(now_utc())          # datetime(2025, 7, 5, 20, 0, 0, tzinfo=UTC)
print(to_iso())           # "2025-07-05T20:00:00+00:00"
```

---

## Common Mistakes

| Mistake | Why It Is Wrong |
|---|---|
| Adding business logic to `lib/` | Business logic belongs in its own department (market, signals, strategies, etc.). `lib/` is only for generic, reusable tools. |
| Making `lib/` import from another department | If `lib/` imports from `market/`, then `market/` cannot import from `lib/` without creating a circular dependency. This would break everything. |
| Adding things "just in case" | Only add to `lib/` when at least two departments need the same thing. Otherwise it is speculative generalization. |
| Modifying `lib/` frequently | `lib/` should be the most stable part of the system. Changes here affect every department. Design carefully, change rarely. |

---

## Industry Best Practices

| Practice | Why |
|---|---|
| **Zero internal dependencies** | `lib/` imports nothing from the project. This prevents circular dependencies and keeps it independently testable. |
| **Immutable types** | Currency and Timestamp cannot be changed after creation. This prevents bugs from accidental modification. |
| **Frozen dataclasses** | Models use `@dataclass(frozen=True)` to enforce immutability at the language level. |
| **Single responsibility** | Each file in `lib/` has exactly one job. `currency.py` handles money. `timestamp.py` handles time. Never mixed. |

---

## Engineering Notes

`lib/` is the **bottom of the dependency graph**. This is the single most important architectural property of the entire project.

Benefits of this design:
- `lib/` can be tested in complete isolation
- `lib/` can be extracted into its own package with zero changes
- `lib/` never causes circular imports
- `lib/` changes never break downstream as long as the public API is maintained

Costs of this design:
- `lib/` must be designed well from the start. A mistake here propagates everywhere.
- `lib/` changes must be careful and well-tested.
- `lib/` can only use external dependencies (Python standard library, pydantic, yaml).

---

## Future Growth

`lib/` grows slowly. New things are added only when:

1. At least **two** different departments need the same thing
2. The thing is truly universal (not department-specific)
3. Adding it now saves more work than it creates

**Examples of future additions:**
- `Percentage` type (when multiple departments need to handle percentages)
- `Country` type (when international trading begins)
- `CurrencyConverter` (when multi-currency becomes common)

---

## What Should NEVER Go Here

| ❌ Never Put This In `lib/` | Where Does It Go? |
|---|---|
| Business logic | Its own department (market, signals, strategies, etc.) |
| Department-specific types | That department's `models/` folder |
| Secret keys or passwords | Environment variables or secrets manager |
| Large data files | Database or data storage |
| Code that depends on another department | That department or upstream |
| Frequently changing code | A department where change is expected |

---

## Summary

`lib/` is the **foundation** of the entire system.

- It provides shared types, utilities, configuration, logging, and patterns
- It depends on nothing inside the project
- Every other department depends on it
- It should be the most stable and most tested part of the system
- It grows slowly and carefully

**Core rule:** If you are writing code and think "someone else might need this," put it in `lib/`. If you are not sure, keep it in your department until a second department needs it.

---

## Continue to the Next Lesson

→ `market/` — How market data enters the system
