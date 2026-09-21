# Architecture — VAYREN Current System

**Owns:** Module map, layers, dependency graph, event flow, runtime. **Not owns:** Language ownership → `ARCHITECTURE_CONSTITUTION.md`; detailed boundaries/events/contracts/state → `module_contracts.md`, `event_catalog.md`, `ai_memory.md`.
**When to read:** ALWAYS first, before any code change. **Related:** `ARCHITECTURE_CONSTITUTION.md` (languages), `90_brain/` (contracts/state).

**Status:** Authoritative. Current system only — not a diary. For language/migration see `ARCHITECTURE_CONSTITUTION.md`.

---

## 1. What is VAYREN?

Desktop charting platform: `SQLite per-stock OHLCV → EventBus → candlestick chart + watchlist`. Event-driven, layered, single composition root (`00_app/app/bootstrap/bootstrap.py`). No trading logic in chart, no chart logic in storage.

---

## 2. Current Modules

| Chapter | Package | Owns | Depends on |
|---|---|---|---|
| `00_app` | `app` | Entry, wiring, lifecycle (`App`, `Bootstrap`, `AppLifecycle`) | `core`, `data`, `market`, `chart`, `strategy`, `backtest`, `risk`, `execution`, `broker` |
| `01_core` | `core` | Foundation: `EventBus`, `Event`, logger, `Registry`, contracts, `SystemModel`, AI boundary | — (stdlib only) |
| `02_data` | `data` | Historical download (write path): engine, worker thread, storage, provider boundary | `core`, `broker` (UBL registry/faces only) |
| `03_market` | `market` | Read path: per-symbol SQLite → `Bar`/`SymbolQuote` | `core` |
| `04_chart` | `chart` | Chart model, engine, renderers, widgets, windows, theme | `core`, `market` |
| `05_strategy` | `strategy` | Strategy registry, Python-native runtime, research, Lab UI | `core`, `market` |
| `06_backtest` | `backtest` | Replay, execution simulation, positions, journal, metrics | `core`, `market`, `strategy` |
| `07_risk` | `risk` | Fail-closed pre-order gates, kill switches, session/clock rules | `core` |
| `08_execution` | `execution` | Live/paper strategy sessions: market-data intake, runtime, planner, engine, broker boundary, portfolio, journal/replay, regime, adaptive | `core`, `market`, `strategy`, `risk`, `broker` (UBL registry/faces only) |
| `09_broker` | `broker` | Unified Broker Layer: vocab, tri-state capabilities, faces, single registry, selection, funds, credentials, health | — (stdlib only) |
| `rust` | `vayren-core` / `vayren-shell` | Rust-owned authorities: order lifecycle table, backtest numeric kernels, timeframe aggregation (cdylib via `core.native` ctypes boundary); native-UI target (Slint application shell) | — (std only; Slint for shell) |

> `90_brain/` is documentation, not a runtime module.

---

## 3. How Modules Are Connected

**Allowed dependency graph** (machine truth: `DOMAIN_DEPS` in
`scripts/validate_imports.py`; qualified per-module rules: `module_contracts.md` §4):

```text
00_app → all chapters (composition root) · 01_core → none · 09_broker → none
02_data → core, broker · 03_market → core · 05_strategy → core, market
06_backtest → core, market, strategy · 07_risk → core
08_execution → core, market, strategy, risk, broker
```

| Dependency | Allowed? | Reason |
|---|---|---|
| `market → core` | ✅ | Read layer needs bus/contracts only |
| `chart → market` (`Bar` only) | ✅ | Chart renders bars, must not touch `market.database` |
| `chart → data` | ❌ | Write path isolated from presentation |
| `market → chart` | ❌ | Storage must not know presentation |
| `core → any` | ❌ | Foundation is domain-agnostic |
| `app → any` | ✅ | Composition root wires all |
| `* → app` | ❌ | No backward import |
| `strategy → execution/risk` | ❌ | Strategies never reach live machinery; execution consumes strategy |
| `backtest → execution/risk` | ❌ | Research replay never touches live/paper paths |
| `execution → backtest/chart/data` | ❌ | Live layer is independent of research, UI and download paths |
| `risk → non-core` | ❌ | Gates judge snapshots; they read nothing and call nothing |

**INVARIANT:** Cross-module imports use only `module/__init__.py` public surface. Internal paths (`market.database`, `chart.renderer`) are never imported cross-module. Relative cross-module imports (`..market`) and `from x import *` are forbidden.

---

## 4. Layers

### 4.1 Per-domain pipeline

```
database  (SQL)
    ↓
repository (rows → Bar/SymbolQuote)
    ↓
loader    (Event → repository → Event)
    ↓
bridge    (repository → JSON snapshot over stdio)
    ↓
state     (Rust view-model: MarketState, watchlist, viewport, indicators)
    ↓
project   (pure state → Slint render view: candles, axes, markers)
    ↓
screen    (Slint: chart, tools rail, status strip)
```

- One layer = one responsibility. Skipping a layer is forbidden.
- `database` never emits events; Slint screens never touch bus/SQL.

### 4.2 Core internals

```
contracts/  (Component/Capability/Manifest — what exists)
    ↓
registry/   (ComponentRegistry + CapabilityRegistry — who provides)
    ↓
system/     (SystemModel, graphs, analyze_change — how it fits)
    ↓
ai/         (Intent → Plan → Validator → Simulation → Sandbox — deterministic, optional)
```

- `core/ai` is pure model/observation: no LLM call, no runtime mutation.
- `AiBoundary` fail-closed; `Sandbox.deploy()` = recorded decision only; `OptimizationStudy.adopt()` always errors.

### 4.3 Runtime composition (single root)

```
launch_native.py (scripts/launch_native.py)
  ├── resolves repo root, data/strategy dirs, picks the binary
  └── vayren-shell (Rust + Slint production entry)
        ├── spawns app.headless (JSON over stdio)
        ├── snapshots → MarketState/BrokerWorkspace/LiveState/ResearchState/LabState/PortfolioState
        └── projects each state onto its Slint screen
```

- Single runtime: one backend process, one native window, same hot paths.
- Explicit construction — no scanner, no reflection. Snapshots load once at startup (not per-frame).

---

## 5. Runtime Flow

```
make dev → launch_native.py → vayren-shell binary
    ↓
backend spawn (app.headless: ready + list_symbols)
    ↓
market snapshot → MarketState (watchlist + bars, viewport anchored)
    ↓
lab/portfolio/research/live/system snapshots → native states
    ↓
Slint event loop (screens project state; interactions report to Rust)
```

- **Fail-closed bridge:** every snapshot is validated before it reaches Rust state; backend errors never touch the UI.
- `limit: int | None = None` = full history (no `LIMIT NULL` bind). The market snapshot is the chart's only data source.

---

## 6. Boundaries & Invariants

| Boundary | Rule |
|---|---|
| Event-driven only | Modules communicate only via `EventBus`. Direct calls cross-module are forbidden. |
| Single wiring point | `bus.subscribe` only in `bootstrap.py`. Widgets never subscribe. |
| One module one responsibility | New behavior → new module. Existing modules are not expanded. (`07_risk`+`08_execution` cover the roadmap's risk/execution slots.) |
| Layers isolated | UI: no SQL; loader: no painting; business logic: never in UI. Strategy never calls broker; risk never reads market. |
| Public contract | Consumers depend on `__init__.py` exports + events, never internals. |
| No fabrication | Bars/quotes derived only from real `ohlcv` rows. Aggregation never invents candles. Paper fills derive from real event prices. |
| Secrets | Credentials/tokens never in events, settings, logs, or UI. |
| No placeholder | No mock/sample trading logic, no `TODO/FIXME`, no dead code. |
| Live safety | PAPER default; LIVE needs 5 explicit gates; kill switch persisted; UNKNOWN reconciled, never resubmitted. |

---

## 7. Enforcement

| Check | What it validates |
|---|---|
| `scripts/validate_imports.py` (AST) | Dependency graph §3; forbids internal/relative/star imports; runtime imports only (`if TYPE_CHECKING:` exempt) |
| `scripts/validate_structure.py` | Required layout: `__init__.py`, `README.md`, `manifest.py`, `models/`, `database/`/`renderer/` etc. per domain (7 domains: app/core/data/market/chart/strategy/backtest) |
| `scripts/validate_language_ownership.py` (AST) | Constitutional language ownership: no new Python in Rust-owned domains, no new Python UI surfaces, no reintroduced authorities, Rust crate hygiene |
| `scripts/build_rust.py` | Builds the `vayren-core` cdylib + handshake; required before any Python test run (`make check`/`run_tests.py`/CI build it first) |
| `make check` | `rust` + `ruff format --check` + `ruff check` + `pyright` + `pytest` + validators — must pass before merge |

> Principles for evolution (not a roadmap): new module → lower-numbered public APIs, bus only, own responsibility, contracts in `module_contracts.md`+`event_catalog.md`. Migration is **invisible feature-driven** per `CONSTITUTION.md` §5, §13, §17: new feature → target arch + directly related legacy slice (smallest useful) → validate; no unrelated migration, no big-bang.

