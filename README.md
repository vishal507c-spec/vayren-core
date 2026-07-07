# Volume 0 — Repository Foundation

## Chapter 1 — Repository Root

---

### Previously You Learned

This is the first chapter. There is no previous chapter. This is where you begin.

If you are reading this, you are standing at the entrance of a software repository designed to become the clearest codebase in the world. Everything you see from this point forward is intentional. Nothing here is accidental.

---

### Today You Will Learn

- What this repository is and why it exists
- What problem it solves
- How the entire system is organized
- How every piece connects to every other piece
- How to navigate 13 domains without getting lost
- How to read this repository as a textbook

---

### After This Chapter You Will Understand

- The purpose of an AI-native quantitative trading platform
- The data flow from market data to executed trades
- The domain-driven monorepo architecture pattern
- Why every folder has a README.md that reads like a lesson
- How to find any piece of code in under 30 seconds

---

## 1. Purpose

The purpose of this repository is to build an **AI-native quantitative trading platform** — a system that:

1. Ingests market data from multiple sources
2. Computes trading signals from that data
3. Executes strategies based on those signals
4. Manages risk across all positions
5. Tracks portfolio performance
6. Learns from results to improve

But that is the *what*. The *why* is more important.

This repository exists to demonstrate that software engineering and domain knowledge can be taught together. Every line of code is paired with explanation. Every folder has a README that explains not just what the code does, but *why* it exists, *how* it works, and *what principles* guided its design.

A first-year computer science student can read this repository sequentially and understand both quantitative trading and professional software engineering.

A senior engineer can open any folder and immediately recognize the architecture, the trade-offs, and the intent.

---

## 2. Why It Exists

Most trading platforms fall into one of two categories:

| Category | Problem |
|---|---|
| **Black-box platforms** | You cannot see how they work. You trust their output blindly. |
| **Research notebooks** | Code is unstructured. You cannot productionize it. They do not scale. |

This platform exists to be **neither**.

It is:

- **Transparent** — Every decision is documented. Every trade-off is explained.
- **Production-ready** — It runs on real exchanges with real money. It has tests, type checking, linting, and CI/CD.
- **Pedagogical** — It teaches as it runs. The codebase is a textbook you can execute.
- **AI-native** — Designed for one human and AI agents to collaborate effectively.

---

## 3. Mental Model

Think of this repository as an **assembly line**.

```
Raw Materials → Workstations → Quality Control → Finished Product → Analysis
```

| Assembly Line Step | Repository Domain |
|---|---|
| Raw materials arrive | **market/** — ingests price data |
| Materials are processed | **signals/** — computes indicators |
| Assembly instructions | **strategies/** — decides what to trade |
| Physical assembly | **execution/** — places orders with brokers |
| Inventory management | **portfolio/** — tracks positions and P&L |
| Safety inspection | **risk/** — prevents bad trades |
| Quality analysis | **analytics/** — measures performance |
| R&D for improvements | **research/** — builds ML models |
| Factory floor control | **platform/** — orchestrates everything |
| Shipping and receiving | **interfaces/** — API, CLI, web |
| Building maintenance | **infrastructure/** — Docker, CI, monitoring |
| Company memory | **knowledge/** — decisions, experiments |
| Shared tools | **lib/** — common utilities for all workstations |

Raw data enters at one end. Executed trades and performance reports come out the other end.

---

## 4. Real World Analogy

This repository is a **restaurant kitchen**.

| Kitchen Element | Repository Element |
|---|---|
| Head chef | **platform/Engine** — orchestrates everything |
| Ingredients | **market/** — raw market data |
| Recipes | **strategies/** — trading logic |
| Seasoning adjustments | **signals/** — indicators that modify strategies |
| Cooking (executing recipes) | **execution/** — sending orders to brokers |
| Plating and presentation | **interfaces/** — CLI, API, web dashboard |
| Inventory tracking | **portfolio/** — positions, P&L, allocations |
| Food safety checks | **risk/** — risk limits and validation |
| Taste testing | **analytics/** — performance evaluation |
| New recipe development | **research/** — ML models and experiments |
| Shared knives and pans | **lib/** — shared utilities |
| Walk-in fridge | **market/adapters/** — external data sources |
| Recipe book archive | **knowledge/** — decisions and experiments |
| Restaurant building | **infrastructure/** — Docker, monitoring |

The head chef (Engine) does not cook. The head chef coordinates. Each station works independently. When a station finishes its task, it passes the result to the next station.

---

## 5. Visual Diagram

```
                                    ┌──────────────────────────────────────────────────────────────┐
                                    │                        PLATFORM                              │
                                    │  Engine · EventBus · Clock · LifecycleManager · Container    │
                                    └──────────────────────────────────────────────────────────────┘
                                                    │
        ┌───────────────────────────────────────────┼───────────────────────────────────────────┐
        │                                           │                                           │
        ▼                                           ▼                                           ▼
┌──────────────────┐                      ┌──────────────────┐                      ┌──────────────────┐
│     MARKET       │                      │     SIGNALS      │                      │   STRATEGIES     │
│  Data Ingestion  │ ──── BarReceived ──► │  Indicator Comp  │ ── SignalGenerated ──► │  Trading Logic   │
│  Polygon, IEX    │                      │  RSI, MACD, BB   │                      │  Momentum, MR    │
└──────────────────┘                      └──────────────────┘                      └────────┬─────────┘
                                                                                              │
                                                                                              │ OrderRequested
                                                                                              ▼
┌──────────────────┐                      ┌──────────────────┐                      ┌──────────────────┐
│     RISK         │ ◄──── Check ──────── │    EXECUTION     │ ◄──── Submit ─────── │   PORTFOLIO      │
│  Pre-trade       │                      │  Order Manager   │                      │  Position Track  │
│  Limits          │                      │  Broker Adapters │                      │  P&L Calculation │
└──────────────────┘                      └──────────────────┘                      └────────┬─────────┘
                                                                                              │
                                                                                              ▼
┌──────────────────┐                      ┌──────────────────┐                      ┌──────────────────┐
│   ANALYTICS      │ ◄──── Compute ────── │    RESEARCH      │ ◄──── Train ──────── │  KNOWLEDGE       │
│  Performance     │                      │  ML Models       │                      │  Decisions       │
│  Attribution     │                      │  Feature Pipeline│                      │  Experiments     │
└──────────────────┘                      └──────────────────┘                      └──────────────────┘

┌──────────────────┐                      ┌──────────────────┐
│   INTERFACES     │                      │  INFRASTRUCTURE  │
│  API · CLI · Web │                      │  Docker · CI/CD  │
│  Webhooks        │                      │  Monitoring      │
└──────────────────┘                      └──────────────────┘

┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                                        LIB                                                     │
│                Types · Utils · Config · Logging · Patterns (used by everything)                 │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Data flows left to right. Dependencies flow inward toward lib/. Control flows top to bottom from platform.**

---

## 6. Folder Structure

```
vayren-core/
├── .editorconfig              ← Editor settings (indent, charset, line endings)
├── .gitignore                 ← Files git should ignore
├── .pre-commit-config.yaml    ← Automated checks before every commit
├── .github/                   ← GitHub templates and CI workflows
├── .vscode/                   ← VS Code settings (debug, extensions, launch)
├── AGENTS.md                  ← AI agent instructions for working in this repo
├── CONTRIBUTING.md            ← How to contribute (for humans and AI)
├── Makefile                   ← Common commands (test, lint, typecheck, setup)
├── README.md                  ← You are here
├── pyproject.toml             ← Python project configuration
│
├── lib/                       ← Source code begins here (13 domains)
├── market/
├── signals/
├── strategies/
├── execution/
├── portfolio/
├── risk/
├── analytics/
├── research/
├── platform/
├── interfaces/
├── infrastructure/
└── knowledge/
```

Exactly 13 folders at the top level. Everything in this repository lives in one of them.

---

## 7. Every File Explained

### Root Configuration Files

| File | Purpose | Why It Exists |
|---|---|---|
| `.editorconfig` | Forces consistent indentation (4 spaces Python, 2 spaces YAML) across every editor | Prevents whitespace diffs and style debates |
| `.gitignore` | Tells git what to ignore (`__pycache__`, `.venv`, `.env`, IDE files) | Keeps the repository clean of generated artifacts |
| `.pre-commit-config.yaml` | Runs ruff, pyright, pytest before every commit | Catches errors before they reach CI |
| `pyproject.toml` | Python project metadata, dependencies, tool configuration | Single source of truth for Python toolchain |

### Documentation Files

| File | Purpose | Why It Exists |
|---|---|---|
| `README.md` | This file. Entry point. Onboarding. Architecture overview. | First thing anyone sees — must explain everything |
| `AGENTS.md` | Instructions for AI agents working in this repository | AI agents need context too |
| `CONTRIBUTING.md` | Human and AI contribution guidelines | Ensures consistent quality over time |

### Build and Automation

| File | Purpose | Why It Exists |
|---|---|---|
| `Makefile` | Common commands — `make setup`, `make test`, `make lint`, `make check` | One command to remember instead of many |

### Dot Directories

| Directory | Purpose | Why It Exists |
|---|---|---|
| `.github/` | GitHub Actions CI workflow, issue templates, PR template | Automation and collaboration |
| `.vscode/` | VS Code debug configurations, recommended extensions, settings | Consistent development environment |

---

## 8. Every Subfolder Explained

### Source Code Domains (13)

| # | Domain | Purpose | Dependencies |
|---|---|---|---|
| 1 | **lib/** | Shared foundation — types, utilities, config, logging | Nothing |
| 2 | **market/** | Market data ingestion and serving | lib |
| 3 | **signals/** | Signal and indicator computation | market, lib |
| 4 | **strategies/** | Trading strategy execution | signals, platform, lib |
| 5 | **execution/** | Order management and broker connectivity | platform, lib |
| 6 | **portfolio/** | Portfolio allocation and P&L tracking | strategies, execution, lib |
| 7 | **risk/** | Risk management and limit enforcement | portfolio, lib |
| 8 | **analytics/** | Performance metrics and reporting | portfolio, risk, lib |
| 9 | **research/** | AI/ML models, experiments, and agents | market, analytics, lib |
| 10 | **platform/** | Event bus, runtime engine, configuration | lib |
| 11 | **interfaces/** | API, CLI, web dashboard, mobile | All domains |
| 12 | **infrastructure/** | Docker, CI/CD, monitoring | Nothing (config only) |
| 13 | **knowledge/** | Company memory — decisions, experiments, journals | Nothing (docs only) |

**Dependency Rule:** A domain may import from `lib/` and from upstream domains' **public API only** (what appears in `__init__.py`). It may never import from another domain's internal modules.

---

## 9. Data Flow

```
                          DATA FLOW THROUGH THE SYSTEM

  TIME
   │
   ▼
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  MARKET   │───▶│ SIGNALS  │───▶│STRATEGIES│───▶│EXECUTION │───▶│PORTFOLIO │───▶│ANALYTICS │
│ raw data  │    │indicators│    │decisions │    │ orders   │    │ positions│    │ metrics  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     │               │              │               │               │               │
     │               │              │               │               │               │
     ▼               ▼              ▼               ▼               ▼               ▼
  Polygon         RSI(14)       Momentum         AlpacaBroker     PnL             Sharpe
  IEX             MACD          MeanRev          Simulated        Allocation      Sortino
  CSV             BBands        Custom           Broker           Rebalance       Attribution
```

### Step-by-Step

1. **Market** receives a bar from Polygon (external data source)
2. Market publishes `BarReceived` event
3. **Signals** processes the bar — computes RSI, MACD, Bollinger Bands
4. Signals publishes `SignalGenerated` event
5. **Strategies** evaluates signals against active strategies
6. If entry condition met, Strategy publishes `OrderRequested` event
7. **Risk** validates order against all risk limits
8. **Execution** submits validated order to broker
9. Broker confirms fill → Execution publishes `OrderFilled` event
10. **Portfolio** updates position tracker and P&L
11. **Analytics** recomputes performance metrics

Everything is event-driven. Nothing waits. Nothing blocks.

---

## 10. Dependency Flow

```
                    DEPENDENCY ARROW DIAGRAM

  interfaces/ ──────────────────────────────────────────────────────┐
     │                                                               │
     │  (depends on all domains)                                     │
     │                                                               │
     ▼                                                               │
  platform/ ───────────────────────────────────────────────────────┐ │
     │                                                              │ │
     ▼                                                              ▼ ▼
  market/ ───→  signals/ ───→  strategies/ ───→  execution/ ───→  portfolio/
     │                                                              │
     │                                                              │
     ▼                                                              ▼
  research/ ───→  analytics/  ◄───────  risk/ ──────────────────────┘
                                                                    │
                                                                    ▼
                                                              knowledge/
                                                                    │
                                                                    ▼
                                                              infrastructure/
                                                                    │
                                                                    ▼
                                                              lib/ (everything depends on lib)

Rules:
  • Arrows point FROM the dependant TO the dependency
  • No domain may depend on interfaces/ (interfaces depends on them)
  • No domain may depend on infrastructure/ (infrastructure configures them)
  • lib/ has ZERO dependencies on any other domain
  • Knowledge/ imports nothing from code (it is documentation)
```

---

## 11. Industry Terminology

| Term | Meaning | In This Repository |
|---|---|---|
| **Bar** | A single unit of market data (open, high, low, close, volume) over a time period | `market/models/bar.py` |
| **Signal** | A computed value from market data that indicates something (e.g., RSI = 30 means oversold) | `signals/models/signal.py` |
| **Strategy** | A set of rules that decides when to buy and sell | `strategies/models/strategy.py` |
| **Order** | An instruction to buy or sell an asset | `execution/models/order.py` |
| **Fill** | A confirmed execution of an order | `execution/models/fill.py` |
| **Portfolio** | A collection of positions and cash | `portfolio/models/portfolio.py` |
| **Risk Limit** | A boundary on how much risk can be taken | `risk/models/risk_limit.py` |
| **Drawdown** | Peak-to-trough decline in portfolio value | `analytics/models/metrics.py` |
| **Sharpe Ratio** | Return per unit of risk (higher is better) | `analytics/services/calculator.py` |
| **Event Bus** | A messaging system for decoupled communication | `platform/services/event_bus.py` |
| **Adapter** | A bridge between internal code and an external system | `market/adapters/polygon.py` |
| **Engine** | The central coordinator that runs the system | `platform/models/engine.py` |

---

## 12. Architecture Notes

### Why Domain-Driven Monorepo?

A monorepo (single repository for everything) keeps all code in one place. No cross-repo versioning issues. No pulling five separate repos to understand a single trade.

Domains (13 folders) organize by *business function*, not by file type. Compare:

```
❌ Organized by file type (bad):
controllers/  models/  services/  tests/  utils/

✅ Organized by domain (good):
market/  signals/  strategies/  execution/  portfolio/ ...
```

In the first, a single feature touches all five folders. In the second, each feature lives entirely within its domain.

### Why Event-Driven Communication?

Domains never import each other's services directly. They communicate through events:

```python
# market/publishes event:
EventBus.publish(BarReceived(bar=bar))

# signals/subscribes to event:
EventBus.subscribe(BarReceived, self.on_bar_received)
```

This means:
- **market/** does not know **signals/** exists
- **signals/** does not know **strategies/** exists
- Each domain can be tested in isolation
- Adding a new subscriber does not change the publisher

### Why Inward Dependencies?

```
interfaces → platform → strategies → signals → market → lib
                                               ↘
                                    execution → portfolio → risk → analytics
                                                                   ↘
                                                            research
```

Everything eventually depends on `lib/`. Nothing depends on `interfaces/`. This prevents circular imports and keeps the dependency graph acyclic.

### Why Frozen Models?

Domain models are frozen dataclasses:

```python
@dataclass(frozen=True)
class Bar:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime
```

Frozen means immutable. Once created, a Bar cannot change. This prevents:
- Accidental mutation bugs
- Race conditions in multi-threaded code
- Confusing "who changed this object" debugging sessions

---

## 13. Best Practices

### Repository Navigation

1. **Start here.** Read this README completely before opening any folder.
2. **Follow the dependency order.** Read `lib/` first, then `market/`, then `signals/`, etc.
3. **Read domain READMEs.** Every domain has a README that explains its purpose and contents.
4. **Read subfolder READMEs.** Every models/, services/, events/, adapters/ folder has a README.
5. **Run the code.** `make setup` then `make test` to verify everything works.

### Code Conventions

1. **Type hints on every function.** No exceptions. `def foo(x: int) -> str:`
2. **Frozen dataclasses for models.** Never mutable classes for domain entities.
3. **Events are past tense.** `BarReceived`, not `BarReceive` or `NewBar`.
4. **No wildcard imports.** `from market import *` is forbidden.
5. **No circular imports.** Use events to break dependency cycles.
6. **Every public API is in `__init__.py`.** Cross-domain imports only go through here.
7. **Tests are colocated.** Each domain has its own `tests/` folder.

---

## 14. Common Mistakes

| Mistake | Why It Happens | How To Avoid |
|---|---|---|
| Importing from another domain's internals | Convenience — "it's just one import" | Only import from `__init__.py` |
| Making models mutable | Familiarity with dicts and classes | Use `@dataclass(frozen=True)` |
| Skipping type hints | "It works without them" | Configure IDE to warn on missing types |
| Direct service-to-service calls | "Events are too much ceremony" | Use events — they pay off at scale |
| Adding files outside domain structure | "This doesn't fit anywhere" | Ask: which domain does this belong to? |
| Editing a README without reading it | "I know what this does" | READMEs are lessons — read them first |
| Committing without `make check` | "It's just a small change" | Small changes break things too |

---

## 15. Quick Example

Here is the entire system in 10 lines (conceptual):

```python
# 1. Market ingests data
bar = polygon_adapter.fetch_latest("SPY")

# 2. Signals compute indicators
rsi = compute_rsi(bar)

# 3. Strategies make decisions
if rsi < 30:  # oversold
    order = strategy.request_buy("SPY", 100)

# 4. Risk validates
risk_checker.validate(order)

# 5. Execution submits
broker.submit(order)

# 6. Portfolio tracks
portfolio.update(order)

# 7. Analytics measures
sharpe = performance.sharpe_ratio(portfolio)
```

Each of these steps is a separate domain, connected by events, orchestrated by the platform.

---

## 16. Code Example

```python
# Minimal working example — importing from each domain's public API
from lib.types.currency import Currency, USD
from market import Bar, MarketDataQuery
from signals import Signal
from strategies import Strategy
from execution import Order
from portfolio import Portfolio
from risk import RiskLimit
from analytics import PerformanceMetrics
from research import FeaturePipeline
from platform import Engine, EventBus, Mode

print("All 11 domains import successfully.")
print(f"USD is a {type(USD)}")
print(f"A Bar has fields: open, high, low, close, volume, timestamp")
print(f"An Order has side: buy/sell, quantity, price_type: market/limit")
print(f"The Engine supports modes: {[m.value for m in Mode]}")
```

Run this to confirm everything is wired correctly:
```bash
python -c "from lib.types.currency import Currency; from platform import Mode; print('OK')"
```

---

## 17. Exercises

1. **Navigation:** Open `market/README.md` and write down what the market domain does in one sentence.

2. **Dependency mapping:** Draw the dependency graph for `strategies/`. Which domains does it depend on? Which domains depend on it?

3. **Data flow:** Trace what happens when a `BarReceived` event is published. Which domains consume it? What events do they publish in response?

4. **Import validation:** Find `execution/services/__init__.py`. Does it import anything from outside execution/ or lib/? If so, is it a public API import?

5. **Public API:** Look at `market/__init__.py`. What classes and functions does it expose? Which of these would `signals/` use?

---

## 18. Interview Questions

**Q: Why is the repository organized by domain instead of by file type?**

A: Domain organization keeps related code together. A single feature (e.g., "show RSI for SPY") touches one domain (signals) instead of scattering across controllers/models/services/tests. This makes the codebase navigable, testable, and maintainable as it grows.

**Q: What problem does event-driven architecture solve in this system?**

A: It decouples domains. Market does not know Signals exists — it just publishes BarReceived. Signals subscribes independently. This means: (1) domains can be developed and tested in isolation, (2) adding new subscribers does not change publishers, (3) the data flow is explicit and auditable through event logs.

**Q: Why do all dependencies point inward toward lib/?**

A: To prevent circular dependencies and keep the graph acyclic. lib/ has zero dependencies, so every domain can safely depend on it. If dependencies went both directions, you would eventually create cycles that make the code impossible to import.

**Q: What makes this repository "AI-native"?**

A: The structure is designed for AI agents. AGENTS.md provides explicit instructions. Folder structure follows a predictable pattern (models/services/events/adapters/tests) across all 13 domains. Public APIs are in `__init__.py`. Events are the communication protocol. This means an AI agent can navigate, understand, and contribute without human hand-holding.

---

## 19. Summary

- This is a **quantitative trading platform** organized into **13 domains**
- Domains are arranged by **data flow**: market → signals → strategies → execution → portfolio → risk → analytics → research
- Everything communicates through **events** (decoupled, async, auditable)
- All dependencies point **inward toward lib/** (acyclic, testable)
- Every folder is a **lesson** — READMEs are structured chapters
- The system is **AI-native** — designed for humans and AI to collaborate
- The repository is both a **production codebase** and an **engineering textbook**

---

## 20. Next Chapter Preview

In **Chapter 2 — AGENTS.md**, you will learn how AI agents are instructed to work in this repository. You will discover:

- The rules of engagement for AI
- How import rules prevent architecture decay
- The vocabulary system that makes communication precise
- The before/after checklists that every AI follows

Continue to **Volume 0, Chapter 2** to learn how AI navigates this repository.

---

### Knowledge Check

1. How many domains are in this repository? Name them.
2. What is the dependency rule for cross-domain imports?
3. Why are events preferred over direct service calls?
4. What does "inward dependencies" mean?
5. Where should you import from when importing from another domain?

### Exercises

See Section 17 above.

### Revision

Before proceeding, ensure you can:
- Explain the data flow from market to analytics
- Name all 13 domains and their purpose
- Explain why lib/ has zero dependencies
- Describe the event-driven communication pattern
- Locate each domain's public API

### Next Chapter

When you are ready, type **NEXT** to proceed to **Volume 0, Chapter 2 — AGENTS.md**.
