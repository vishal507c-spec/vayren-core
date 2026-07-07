# AGENTS.md — AI Agent Configuration

## What This Document Is

This document tells **AI agents** how to work inside this repository. When an AI arrives here, it reads this file first to understand:

- How is this repository organized?
- What conventions must it follow?
- What should it do before making changes?
- Where should it look for context?

Humans should also read this document to understand how AI will behave.

---

## Repository Structure

This repository is organized into **13 numbered chapters** — a navigation layer that tells the story of the software from top to bottom. Inside each chapter folder lives the actual Python package.

```
01_foundation/     → lib/       (shared toolkit)
02_platform/       → platform/  (runtime orchestration)
03_market/         → market/    (data ingestion)
04_signals/        → signals/   (indicator computation)
05_strategies/     → strategies/(trading logic)
06_risk/           → risk/      (safety enforcement)
07_execution/      → execution/ (order management)
08_portfolio/      → portfolio/ (position tracking)
09_analytics/      → analytics/ (performance measurement)
10_research/       → research/  (AI/ML experiments)
11_interfaces/     → interfaces/(API, CLI, web)
12_infrastructure/ → infrastructure/ (Docker, CI/CD)
13_knowledge/      → knowledge/ (company memory)
```

### Chapter Structure

Each numbered chapter contains the Python package (e.g. `03_market/market/`). Every package follows the same internal structure:

```
market/                  ← Same as always — imports still use "from market import Bar"
├── __init__.py          ← Public API
├── README.md            ← Lesson
├── models/              ← Domain entities
├── services/            ← Business logic
├── events/              ← Published messages
├── adapters/            ← External integrations
├── tests/               ← Verification
└── conftest.py          ← Shared test fixtures
```

### Departments and Dependencies

| Department | Chapter | Purpose | Depends On |
|---|---|---|---|
| `lib/` | 01_foundation | Shared toolkit (types, utilities, configuration) | Nothing |
| `platform/` | 02_platform | Event bus, runtime engine, configuration | lib |
| `market/` | 03_market | Market data ingestion and serving | lib |
| `signals/` | 04_signals | Signal and indicator computation | market, lib |
| `strategies/` | 05_strategies | Trading strategy execution | signals, platform, lib |
| `risk/` | 06_risk | Risk management and limit enforcement | portfolio, lib |
| `execution/` | 07_execution | Order management and broker connectivity | platform, lib |
| `portfolio/` | 08_portfolio | Portfolio allocation and P&L tracking | strategies, execution, lib |
| `analytics/` | 09_analytics | Performance metrics and reporting | portfolio, risk, lib |
| `research/` | 10_research | AI/ML models, experiments, and agents | market, analytics, lib |
| `interfaces/` | 11_interfaces | API, CLI, web dashboard, mobile | All departments |
| `infrastructure/` | 12_infrastructure | Docker, CI/CD, monitoring | None (configuration only) |
| `knowledge/` | 13_knowledge | Company memory: decisions, experiments, journals | None (documentation only) |

**Dependency rule:** A department may import from `lib/` and from its upstream dependencies' **public API only** (what appears in `__init__.py`). It may never import from another department's internal modules.

---

## Vocabulary

| Term | Meaning | Example |
|---|---|---|
| **Chapter** | A numbered top-level folder telling a story | `03_market/`, `04_signals/` |
| **Department** | The Python package inside a chapter | `market/`, `signals/` |
| **Model** | A structured representation of a real-world thing | `Bar`, `Order`, `Trade` |
| **Service** | A class that performs actions on models | `MarketDataQuery`, `OrderManager` |
| **Event** | An immutable message saying something happened | `BarReceived`, `OrderFilled` |
| **Adapter** | A bridge between our system and an external system | `PolygonAdapter`, `AlpacaBroker` |
| **Public API** | Classes and functions exposed in `__init__.py` | What external code is allowed to import |

---

## Conventions

### 1. Read Before Modifying

Before changing any code, read:

1. The department's chapter README (e.g. `03_market/market/README.md`)
2. `13_knowledge/knowledge/decisions/` — check for existing ADRs
3. `13_knowledge/knowledge/prompts/` — check for task-specific AI instructions
4. `13_knowledge/knowledge/memory/current-context.yaml` — current focus and priorities
5. The department's `__init__.py` — understand the public API
6. The department's tests — understand expected behavior

### 2. Import Rules

```python
# ✅ CORRECT import patterns
from lib.types.currency import Currency
from market import Bar, MarketDataQuery
from signals import Signal
from execution import Order
from .services.order_manager import OrderManager

# ❌ INCORRECT import patterns
from market.services.ingestion import _normalize_bar  # WRONG
from ..market import Bar  # WRONG
from signals import *  # WRONG
```

### 3. Naming Conventions

| What | Convention | Example |
|---|---|---|
| Chapter folders | Numbered, lower_case | `03_market/`, `04_signals/` |
| Python packages | Lowercase, singular | `market/`, `signals/` |
| Python files | snake_case | `bar.py`, `order_manager.py` |
| Classes | PascalCase | `Bar`, `OrderManager` |
| Functions | snake_case | `get_bar()`, `validate_order()` |
| Events | Past tense, PascalCase | `BarReceived`, `OrderFilled` |
| Tests | `test_` prefix | `test_bar.py` |

### 4. File Organization

- **Chapter depth:** Up to 4 levels (`03_market/market/services/ingestion.py`)
- **Package depth:** Up to 3 levels (`department/module/file.py` — unchanged)
- **Tests are colocated** in the department's `tests/` folder
- **Public API** is defined in `__init__.py` — all cross-department imports go through here
- **Chapter folders never contain `__init__.py`** — they are pure navigation containers

### 5. Event Naming

Events describe something that **already happened**. Always use past tense:

```
✅ BarReceived     → A bar was received
✅ OrderFilled     → An order was filled
✅ LimitBreached   → A limit was breached
❌ BarReceive      → Not past tense
❌ ReceiveBar      → Wrong order
❌ NewBar          → Ambiguous
```

---

## Before Making Changes

```
□ Read the department's README.md
□ Read relevant ADRs in 13_knowledge/knowledge/decisions/
□ Check 13_knowledge/knowledge/memory/current-context.yaml for current priorities
□ Read the department's __init__.py for public API
□ Read existing tests to understand expected behavior
□ Read 13_knowledge/knowledge/prompts/ for task-specific instructions
```

## After Making Changes

```
□ Write an ADR (13_knowledge/knowledge/decisions/) if architecture changed
□ Write experiment results (13_knowledge/knowledge/experiments/) if experimental
□ Update 13_knowledge/knowledge/memory/current-context.yaml
□ Run make check (lint + format + typecheck + test + validate)
□ Ensure type hints are present on all new functions
□ Update department README.md if public API changed
```

---

## How AI Should Navigate This Repository

When you (the AI) are asked to work on something:

1. **Start at README.md** — understand the system
2. **Read AGENTS.md** (this file) — understand the rules
3. **Read the relevant chapter's README** — understand its purpose
4. **Read the department's `__init__.py`** — understand its public API
5. **Check 13_knowledge/** — relevant decisions, experiments, and context
6. **Make changes** following the conventions above
7. **Run `make check`** to verify correctness
8. **Update 13_knowledge/knowledge/memory/current-context.yaml**

Python imports use the original package names (e.g. `from market import Bar`). Python discovers packages through the numbered chapter directories because `pip install -e .` adds the project root to `sys.path`, and each chapter contains the real package.

---

## One Sentence Summary

> AGENTS.md defines the rules of engagement for AI agents working in this repository — read it before making any changes.

---

## Continue to the Next Lesson

→ `CONTRIBUTING.md` — How humans (including future you) contribute to this repository
