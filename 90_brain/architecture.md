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
| `rust` | `vayren-core` / `vayren-shell` | Rust-owned authorities: order lifecycle table, backtest numeric kernels, timeframe aggregation (cdylib via `core.native` ctypes boundary); native-UI target (egui broker panel) | — (std only; egui for shell) |

> `90_brain/` is documentation, not a runtime module.

---

## 3. How Modules Are Connected

**Allowed dependency graph** (enforced by `scripts/validate_imports.py`):

```
01_core ─────────────────────► (none)
  ↑                          
02_data ─────────────────────► 01_core, 09_broker
03_market ───────────────────► 01_core
05_strategy ─────────────────► 01_core, 03_market
04_chart ────────────────────► 01_core, 03_market
06_backtest ─────────────────► 01_core, 03_market, 05_strategy
07_risk ─────────────────────► 01_core
08_execution ─────────────────► 01_core, 03_market, 05_strategy, 07_risk, 09_broker
09_broker ───────────────────► (none)
00_app ──────────────────────► 01_core, 02_data, 03_market, 04_chart, 05_strategy, 06_backtest, 07_risk, 08_execution, 09_broker
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
engine    (Event → ChartModel)
    ↓
renderer  (stateless QPainter: grid, candles, volume, axes)
    ↓
widgets   (viewport: zoom/pan/crosshair, watchlist, tools rail)
    ↓
windows   (host: splitter, title, WindowRendered)
```

- One layer = one responsibility. Skipping a layer is forbidden.
- `database` never emits events; `widgets` never touch bus/SQL.

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
Bootstrap (00_app/app/bootstrap/bootstrap.py)
  ├── EventBus + services Registry (name lookup, unchanged)
  ├── subscriptions (ONLY place subscribe is called)
  └── _build_architecture() — once at startup
        ├── market_manifest() → ComponentRegistry (live SymbolRepository)
        ├── chart_manifest()  → ComponentRegistry (live ChartEngine)
        ├── data_manifest() , strategy_manifest(), backtest_manifest()
        └── SystemModel(registry) → components / system_model
```

- Single runtime: same bus, same bootstrap, same hot paths.
- Capability lookup `components.providers("data.query.candles")` coexists with legacy `services.get("symbol_repository")`.
- Explicit registration — no scanner, no reflection. `SystemModel` built once at startup (not per-event).

---

## 5. Runtime Flow

```
App.main() → parse_args + configure_logging + QApplication
    ↓
Bootstrap(data_dir, limit)  // builds bus, repos, loaders, engine, widgets
    ↓
Bootstrap.start() → AppStarted (bus)
    ↓
AppLifecycle.on_app_started → ListSymbols
    ↓
SymbolListLoader → SymbolsListed → QuoteLoader → QuotesLoaded (watchlist)
    ↓
(user click) LoadSymbol / TimeframeChanged → MarketDataLoader → DataLoaded
    ↓
ChartEngine.on_data_loaded → ChartReady
    ↓
ChartWindow.on_chart_ready → widget.set_model → WindowRendered
    ↓
Qt event loop
```

- **Synchronous bus:** the entire chain completes in one `publish` before Qt loop starts.
- `limit: int | None = None` = full history (no `LIMIT NULL` bind). `DataLoaded` is the chart's only data source.

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
| `scripts/validate_language_ownership.py` (AST) | Constitutional language ownership: no new Python in Rust-owned domains, no new Qt UI surfaces, no reintroduced authorities, frozen baseline, Rust crate hygiene |
| `scripts/build_rust.py` | Builds the `vayren-core` cdylib + handshake; required before any Python test run (`make check`/`run_tests.py`/CI build it first) |
| `make check` | `rust` + `ruff format --check` + `ruff check` + `pyright` + `pytest` + validators — must pass before merge |

> Principles for evolution (not a roadmap): new module → lower-numbered public APIs, bus only, own responsibility, contracts in `module_contracts.md`+`event_catalog.md`. Migration is **invisible feature-driven** per `CONSTITUTION.md` §5, §13, §17: new feature → target arch + directly related legacy slice (smallest useful) → validate; no unrelated migration, no big-bang.

