# Module Contracts — VAYREN Institutional Specification

**Owns:** Module boundaries, public APIs, dependency rules, cross-module interfaces, invariants. **Not owns:** Language ownership → `ARCHITECTURE_CONSTITUTION.md`; module map/layers → `architecture.md`; events list → `event_catalog.md`; current state → `ai_memory.md`.
**When to read:** Before modifying any module or adding cross-module import.
**Related:** `AGENTS.md` (workflow), `architecture.md` (layers), `event_catalog.md` (events).

**Status:** Authoritative. Cross-module imports MUST use only the public contracts documented here (`__init__.py` exports). Internal paths are not contracts.

---

## 1. Purpose

Defines for every VAYREN module: what it owns, what it exposes, what it may consume, what it must never access, and what invariants it must preserve. This is the single source of truth for module boundaries, public APIs, dependency rules, and cross-module interfaces.

AI agents must read the relevant module contract before modifying that module.

---

## 2. Contract Philosophy

| Principle | Rule |
|---|---|
| **Public surface = `__init__.py`** | `module/__init__.py::__all__` is the contract. Anything not exported is internal. |
| **Capability over name** | Capability IDs (`data.query.candles`) describe what a component does; registry resolves who provides it. |
| **Event-driven only** | Modules never call each other directly. All cross-module work is `EventBus` publish/subscribe. |
| **One responsibility** | New behavior → new module. Existing modules are never expanded beyond their responsibility. |
| **No circular dependencies** | Imports flow forward in chapter order only (see §4). |
| **Data over logic at boundaries** | Events carry data (`tuple[Bar]`, `str`, `int`) — never widgets, connections, or callables. |

> **INVARIANT:** Cross-module consumers may depend only on the documented public contract, never on `module.database`, `module.renderer`, or other internal packages.

---

## 3. Global Rules

| # | Rule |
|---|---|
| G1 | `EventBus` is sync, exact-type, fail-safe. Handler failure → log, app continues. No async/replay/queue at bus level. |
| G2 | Subscriptions are created **only** in `00_app/app/bootstrap/bootstrap.py`. No other file calls `bus.subscribe`. |
| G3 | Widgets receive data via `set_model(model)` — never via bus, SQL, or loader. Painting only in `renderer`. |
| G4 | `limit: int | None = None` means **full history** (2016→2026, ~60k rows). `None` is not bound as SQL `LIMIT ?` (SQLite `datatype mismatch`). `None` ⇒ ascending full scan. |
| G5 | Real market data must never be fabricated. Aggregation uses only real rows; missing buckets are omitted, not synthesized. |
| G6 | Credentials, tokens, and secrets never appear in events, settings, logs, UI status text, or `SystemModel`. |
| G7 | `Bar` / `SymbolQuote` / events are frozen dataclasses (`tuple[Bar, ...]` is ordered). |

---

## 4. Module Dependency Rules

### Allowed graph (from `scripts/validate_imports.py`)

```
00_app ──► 01_core, 02_data, 03_market, 04_chart, 05_strategy, 06_backtest, 07_risk, 08_execution, 09_broker
01_core ──► (none — stdlib only)
02_data ──► 01_core, 09_broker (UBL registry/faces only)
03_market ──► 01_core
04_chart ──► 01_core, 03_market
05_strategy ──► 01_core, 03_market
06_backtest ──► 01_core, 03_market, 05_strategy
07_risk ──► 01_core
08_execution ──► 01_core, 03_market, 05_strategy, 07_risk, 09_broker (UBL registry/faces only)
09_broker ──► (none — stdlib only, self-contained coordination boundary)
```

### Table

| Module | May Import | Forbidden |
|---|---|---|
| `00_app` | `core`, `data`, `market`, `chart`, `strategy`, `backtest`, `risk`, `execution`, `broker` | — |
| `01_core` | stdlib only | `data`, `market`, `chart`, `app`, `strategy`, `backtest`, `risk`, `execution`, `broker` |
| `02_data` | `core`, `broker` (UBL registry/faces only) | `market`, `chart`, `app`, `strategy`, `backtest`, `risk`, `execution` |
| `03_market` | `core` | `02_data`, `04_chart`, `app`, `strategy`, `backtest`, `risk`, `execution`, `broker` |
| `04_chart` | `core`, `market` (`Bar` only) | `02_data`, `app`, `strategy`, `backtest`, `risk`, `execution`, `broker`, `market.database` |
| `05_strategy` | `core`, `market` | `02_data`, `04_chart`, `app` internals, `risk`, `execution`, `broker` |
| `06_backtest` | `core`, `market`, `strategy` | `02_data`, `04_chart`, `app` internals, `risk`, `execution`, `broker` |
| `07_risk` | `core` | everything except `core` (events carry data in, decisions out) |
| `08_execution` | `core`, `market` (`Bar` only), `strategy` (public surface), `risk`, `broker` (UBL registry/faces only) | `02_data`, `04_chart`, `app` internals, `backtest`, `market.database` |
| `09_broker` | stdlib only (self-contained coordination boundary) | `data`, `market`, `chart`, `app`, `strategy`, `backtest`, `risk`, `execution` |

**INVARIANT:** `from market.database import ...` or `from ..market import ...` or `from x import *` is a contract violation.

**INVARIANT:** `chart` never imports `data`; `market` never imports `chart`.

**STATIC-ONLY IMPORTS:** `if TYPE_CHECKING:` imports are annotations-only and create no runtime coupling — `scripts/validate_imports.py` ignores them (runtime graph is what is enforced). Use them when a lower layer only needs an upper layer's type for annotations (e.g. strategy annotating with a backtest type, backtest annotating with a chart viewport). Runtime attribute access in those cases must be duck-typed. Never use TYPE_CHECKING to hide a real runtime dependency.

---

## 5. Module Contracts

### 5.1 Module: `00_app` — Manager

**Responsibility:** Composition root. Parses args, configures logging, creates bus/registries, wires every subscription, hosts windows, runs Qt loop.

**Public API** (`app/__init__.py`):
| Export | Contract |
|---|---|
| `App.main(argv)` / `main(argv)` | Entry: `parse_args` → `configure_logging` → `QApplication` → `Bootstrap(...).start()` → `qt_app.exec()` |
| `DEFAULT_DATA_DIR` (`D:\ZerodhaTradingData`) | Default `<data_dir>` if neither `--data-dir` nor `VAYREN_DATA_DIR` set |
| `DEFAULT_LIMIT = None` | Default candles limit |

**Consumes:** `core` (EventBus, logging, registries, SystemModel), `data` (data_manifest, DownloadWorker, settings, credentials), `market` (SymbolRepository, loaders, manifests), `chart` (ChartEngine, widgets, windows, manifests), `strategy` (StrategyRegistry, Lab UI), `backtest` (BacktestRunner/Worker), `execution` (PaperService for `--paper`; headless, no QApplication).

**Produces:** `Bootstrap` with properties `bus: EventBus`, `services: Registry` (name lookup, unchanged), `components: ComponentRegistry` (capability lookup), `system_model: SystemModel`. Emits `AppStarted` on `start()`. Hosts `LiveWorkspace` (LIVE tab): pure view over an injected state dict + arm/halt callbacks, never bus/SQL/broker. Hosts `ResearchWorkspace` (RESEARCH tab): pure view over `ResearchService` (datasets from backtest histories, experiments, real analysis); research computation stays in `strategy.research` (Python). Hosts `PortfolioWorkspace` (PORTFOLIO tab): pure view over the shared workspace-state dict (no duplicated state). Shared `app.ui.ui_kit` widgets (Section/Badge/KVBlock/GateRow/tables) keep all screens consistent.

**Dependencies:** See §4.

**Forbidden:** Business logic, SQL, painting. No domain logic here.

**Invariants:**
- INVARIANT: Only `Bootstrap` calls `subscribe`. INVARIANT: Importing `app` must not start the app (no side-effects at import).

**Implementation note:** `_build_architecture()` registers `market_manifest()` + `chart_manifest()` (live instances as providers) and builds `SystemModel` once at startup (~0.13 ms, ~2% of startup). No plugin scanning, no reflection.

**AI modification:** Add a module → add import + service + subscription line in `Bootstrap`. Do not move wiring elsewhere.

**Validation:** `scripts/validate_imports.py` enforces app→allowed; `scripts/validate_structure.py` checks `00_app/app/{__init__,bootstrap,lifecycle,tests}` exist.

---

### 5.2 Module: `01_core` — Foundation

**Responsibility:** Platform foundation: bus, base event, logging, registries, universal contracts (`contracts/`), system intelligence (`system/`), AI boundary (`ai/`).

**Public API** (`core/__init__.py`):
- **Bus/events:** `EventBus`, `Event`, `AppStarted`
- **Logging:** `configure_logging`, `get_logger`
- **Registries:** `Registry[T]`, `ComponentRegistry`, `CapabilityRegistry`, `RegisteredComponent`, `ComponentRegistryError`
- **Contracts:** `ComponentId`, `ComponentVersion`, `CapabilityId`, `CapabilityDecl`, `ComponentManifest`, `ComponentContract`, `CapabilityContract`, `validate_manifest`, `Health`, etc.
- **System:** `SystemModel`, `ChangeImpact`, `RiskLevel`, `SystemSnapshot`, `Workflow`, `WorkflowRegistry`, `WorkflowStep`, `analyze_change` (plus `analyze_*`), `build_snapshot`
- **AI:** `Intent`/`IntentKind`/`classify`/`parse_intent`, `Plan`/`PlanChange`/`PlanRisk`/`Rollback`/`validate_plan`, `Policy`/`PlanValidator`, `simulate_plan`/`ChangeSimulation`, `Sandbox`/`SandboxStage`/`SandboxDeployment`, `AiBoundary`/`ActionKind`/`BoundaryViolation`, `EngineeringMemory`, `PerformanceMemory`, `OptimizationStudy`, `AiProvider`/`OfflineProvider`/`AiProviderRegistry`, `ContextBuilder`/`AiContext`

**Consumes:** Stdlib only.

**Produces:** Bus dispatch, capability discovery, system model, AI guardrails.

**Forbidden:** Domain concepts (`symbol`, `candle`, `chart`, `provider`). Core is domain-agnostic.

**Invariants & contracts (selection):**
- `EventBus.subscribe(type, handler)`, `publish(event)`, `unsubscribe`, `clear` — exact-type, sync, fail-safe.
- `Plan.id` regex `^[a-z][a-z0-9_]*$`; `reused` ∩ `new` overlap rejected.
- `Sandbox` lifecycle `PLAN→SANDBOX→TEST→BENCHMARK→VALIDATE→APPROVE→DEPLOY`; skip → `SandboxError`; `deploy()` = recorded decision only.
- `AiBoundary` fail-closed: 6 forbidden actions (`execute_trade`, `bypass_risk`, `delete_production_data`, `modify_protected_system`, `deploy_unvalidated`, `override_contract`) → `BoundaryViolation`. Unknown action → denied.
- `OfflineProvider` is default (always unavailable) → AI optional.

**AI modification:** Core changes must stay stdlib-only, preserve deterministic semantics, not add network/LLM calls in runtime.

**Validation:** `core/tests` (+ `scripts/validate_imports.py` core→none).

---

### 5.3 Module: `02_data` — Historical Download (Write Path)

**Responsibility:** Download missing historical OHLCV into per-symbol SQLite files that `market` reads. MODE 1 only: download, idempotent resume, never audit/repair/verify.

**Public API** (`data/__init__.py`):
`DownloadSettings`, `HistoricalDownloadEngine`, `DownloadWorker`, `data_manifest`, events `DownloadRequest`, `CoverageRequest`, `CancelDownload`, `DownloadStarted`, `DownloadProgress`, `DownloadCompleted`, `DownloadFailed`, `DownloadCoverage`

**Consumes:** `core` only (bus, contracts).

**Produces:** Files `<data_dir>/SYMBOL.db` + events. Capabilities `historical_data.download`, `.coverage`, `.status`.

**Dependencies:** `core` only. INVARIANT: Engine import must not load any broker SDK (`data.provider.contract` only).

**Forbidden:** `market`, `chart`, `app`, `strategy`, `backtest`. No market reads, no chart imports.

**Invariants:**
- INVARIANT: Never fabricate candles. `INSERT OR IGNORE` only.
- INVARIANT: State derived from DB itself; no progress files.
- INVARIANT: Market hours `09:15–12:40 IST` block downloads; NSE holidays respected.
- INVARIANT: `DownloadSettings` frozen config: `chunk_days=200`, `max_history_years=10`, `max_retries=5`, `max_consecutive_429=3`, `lock_stale_seconds=120`, `max_passes=5`, `default_interval="15m"`, canonical intervals `("1m","5m","15m","30m","1h")`.

**Data contract (per-symbol file):**
```sql
CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL);
CREATE TABLE non_trading(candle_time TEXT PRIMARY KEY);
CREATE TABLE history_boundaries(symbol TEXT PRIMARY KEY, earliest TEXT, latest TEXT);
```
WAL, `V9/V9.1` migrations.

**Provider boundary** (`data/provider/contract.py`): `Provider` protocol `available()`, `symbols()`, `fetch_candles(symbol, interval, start, end)`, `new_session()`, `renew()`; sentinels `TOKEN_EXPIRED`/`RATE_LIMITED`; `ProviderError(message, code)` 7 codes (`AUTHENTICATION_FAILED` …). `ZerodhaProvider` in `data/provider/zerodha/adapter.py` is the ONLY place knowing Kite interval IDs. M7: the `register_provider` shim is retired — venues register directly in the single UBL `BrokerRegistry`; `build_provider(settings)` is retained only as a compatibility delegate while the composition root resolves the selected broker's historical face straight from the registry. `DownloadSettings.provider` is derived state (INVARIANT: always equals `BrokerSelection.name`; the ONLY product write path is `Bootstrap`). `store → env → Not Configured` credential priority via `ProviderCredentialsManager`.

**Implementation note:** `HistoricalDownloadEngine(provider, ...)` requires provider (None→TypeError). `forward_sweep(fetch_chunk)` uses 200-day chunks + jitter. `DownloadWorker(QThread)` bridges bus requests ↔ engine signals; bootstrap bridges signals → `bus.publish`.

**Performance constraints:** `get_quotes` not here; download is I/O-bound, not per-candle hot path.

**AI modification:** Never add `market`/`chart` imports. Keep engine library-only (no CLI). Keep broker IDs inside `zerodha/` only.

**Validation:** `data/tests` (engine no-network), `test_provider` isolation (fresh interpreter must not import `kiteconnect` when importing `data.downloader.engine`).

---

### 5.4 Module: `03_market` — Storage & Query (Read Path)

**Responsibility:** Read-only candle storage: `database → repository → loader`. Serves quotes, timeframes, and bars.

**Public API** (`market/__init__.py`):
`Bar`, `SymbolQuote`, `SqliteCandleDatabase`, `OhlcvCandleDatabase`, `CandleRepository`, `SymbolRepository`, `MarketDataLoader`, `SymbolListLoader`, `TimeframeListLoader`, `QuoteLoader`, `market_manifest`, `TIMEFRAME_LADDER`, `timeframe_seconds`, `timeframe_name`, `available_timeframes`, events `LoadSymbol`, `ListSymbols`, `DataLoaded`, `SymbolsListed`, `QuotesLoaded`, `TimeframeChanged`, `ListTimeframes`, `TimeframesListed`

**Consumes:** `core`.

**Produces:** `Bar`/`SymbolQuote`/`DataLoaded` etc. Capabilities `data.query.candles`, `data.query.timeframes`, `data.query.quotes`, `data.transform.aggregate`.

**Forbidden:** `data`, `chart`, `app`. INVARIANT: Chart must not import `market.database`.

**Invariants:**
- `Bar(symbol, open, high, low, close, volume, timestamp: ISO str, bar_size?, vwap?, trades?, source?)` frozen, loop-free.
- `OhlcvCandleDatabase.fetch_candles(symbol, limit)` — `limit None` ⇒ full ascending scan; never bind `LIMIT NULL`.
- `fetch_candles(symbol, limit, start?, end?)` — optional inclusive timestamp bounds on the aggregation window only; detection (base duration, session anchor) always uses the unbounded latest sample, so bucket alignment never changes. Bounds compose with `limit`.
- `CandleRepository.get_candles` is the only rows→Bar mapping.
- Timeframe detection: base = mode of bar-to-bar deltas; `available_timeframes` pure function (ladder entries that are whole multiples of base). Existence always from DB, never cached.
- Aggregation: session-anchored intraday (session start = mode of first-bar-per-day, NSE 09:15 aligned), daily=midnight, weekly=Monday midnight; OHLC = first/max/min/last, volume=sum; zero fabricated. Windowed fetch `(limit+1)×ratio` with `bars[-limit:]` drop of oldest partial bucket; `limit None` ⇒ full fetch no drop. Parsed via `datetime.fromisoformat` (not `strptime`).

**Data contract:** See §8.

**Performance note:** `aggregate_bars` bucketing/accumulation is Rust-owned (`rust/vayren-core`, `aggregate` + `stats` kernels; measured ~1.5× end-to-end on 60k rows, kernel 8×); Python keeps timestamp parsing/formatting. `get_quotes` is `fetch_candles(symbol,1)` per symbol (~1 ms/symbol, ~0.5 s for 527 symbols, one-time at `SymbolsListed`, cached via `QuoteLoader._last_symbols` no-op on identical universe).

**AI modification:** Add provider/timeframe → extend repository/loader, not UI. Preserve `_last_symbols` guard and no-re-query.

**Validation:** `market/tests` + `scripts/validate_imports.py`.

---

### 5.5 Module: `04_chart` — Presentation

**Responsibility:** Model → render → viewport → window. Stateless painting, viewport math, watchlist/tools.

**Public API** (`chart/__init__.py`):
`ChartModel`, `CrosshairValue`, `infer_timeframe`, `ChartEngine`, `CandleRenderer`, `CrosshairRenderer`, `LabelRenderer`, `OverlayRenderer`, `TimeAxisRenderer`, `CandleChartWidget`, `IndicatorVisibilityPanel`, `SymbolListWidget`, `TimeframeToolbar`, `ChartToolsToolbar`, `WatchlistWidget`, `ChartWindow`, `chart_manifest`, events `ChartReady`, `WindowRendered`

**Universal plot pipeline** (`chart/renderer/plot_renderer.py`, internal path — same pattern as the existing `PlotOverlay` import in `bootstrap.py`):
`PlotStore` (Qt-free: `ingest`/`ingest_batch` atomic frames, `update`/`remove`/`remove_strategy`, `query_visible(first, last, ...)` indexed viewport query, `consume_dirty`, `stats` measured telemetry), `PlotOverlay.ingest_plot_events/update_plot/remove_plot/total_plot_count/visible_plot_count/plot_stats/consume_plot_dirty`. The renderer consumes generic `PlotEvent` shapes (10 plot types, 9 marker types, text None default, exact logical coordinates) via duck-typing — no runtime `chart → strategy` import. New marker styles (`marker_circle/marker_circle_x/marker_diamond/marker_triangle_up/marker_triangle_down/marker_dot`) + glyph-only `"kind|none"`; legacy styles/paths byte-identical.

**Consumes:** `core`, `market` (`Bar` only).

**Produces:** `ChartReady`/`WindowRendered`; capability `chart.render` (consumes `data.query.candles`).

**Forbidden:** `data`, `app`, `strategy`, `backtest`, `market.database`. INVARIANT: Widgets never touch `EventBus`/SQL.

**Invariants:**
- `ChartModel(symbol, bars: tuple[Bar,...] ascending, timeframe?, exchange?)` frozen; `ChartEngine.on_data_loaded` sorts then `ChartReady`.
- Renderers stateless QPainter: `CandleRenderer` (wicks/bodies/volume), `TimeAxisRenderer` (calendar ladder, ≥96 px spacing), `CrosshairRenderer` (1 px), `OverlayRenderer` (header strip two-tone: `SYMBOL_TEXT` bright + meta muted; crosshair-following price/time pills). No state, no events.
- `CandleChartWidget.set_model(model)` only. Wheel=zoom (cursor anchor), drag=2D pan (time+price), price-strip drag=vertical scale, reset=latest 150 bars (`INITIAL_BARS`). Pixmap cache keyed by `(model id, first, last, price range, volume_max, size)` — crosshair moves are blits.
- `ChartWindow` splitter `[tools|watchlist|download|chart/container]`; tool rail `TOOLBAR_WIDTH=40` with exactly 2 nav buttons (watchlist + download); `_active_panel` single state.

**Theme contract** (`chart/theme.py`): `APP_PALETTE` (Window `#101418`, AlternateBase `#161c26`, Highlight teal `#26a69a`) + `APP_STYLE` palette-only QSS. `QApplication.setPalette(APP_PALETTE)` required; `WA_StyledBackground` needed for plain-QWidget QSS.

**AI modification:** New visual → new renderer method + widget wiring; do not add business logic to widgets.

**Validation:** `chart/tests` (185) + structure check for `manifest.py`, `widgets/tools_toolbar.py`.

---

### 5.6 Module: `05_strategy` — Strategy Platform

**Responsibility:** Strategy registry, Python-native runtime (`PythonStrategy` → `StrategyLogic`), storage, research, Lab UI.

**Public API** (`strategy/__init__.py`):
`StrategyRegistry`, `StrategyRegistryError`, `StrategyDefinition`, `StrategyParameters`, `ParameterSpec`, `ParameterError`, `Signal`, `SignalKind`, `StrategyState`, `StrategyRuntime`, `StrategyLogic`, `BarView`, `BacktestForm`, `ResearchDataset`, `strategy_manifest`, events `StrategiesListed`, `StrategySelected`, `PaperTradeRequested`, `LabReset`
plus the universal plot contract (`strategy/models/plot_event.py`): `PlotEvent`, `PlotType` (LINE/RAY/SEGMENT/MARKER/SHAPE/LABEL/ZONE/HORIZONTAL_LEVEL/VERTICAL_MARK/AREA), `MarkerType` (UP_ARROW/DOWN_ARROW/CIRCLE/CIRCLE_X/SQUARE/DIAMOND/TRIANGLE_UP/TRIANGLE_DOWN/TRIANGLE_BLUE/DOT), `PlotLifecycle`, `RenderLayer`, `PlotValidationError`, `default_layer`, `make_event_id`. Renderer pill style is solid marker-color fill + white text (reference style); directional triangles are tip-anchored; UP pills prefer below-anchor, DOWN above (screen-space only, logical coordinates untouched). Strategy-facing emitters on `PythonStrategy` (`plot_marker/plot_line/plot_ray/plot_segment/plot_zone/plot_label/plot_level`, `update_plot/remove_plot`, `get_plot_events`, `set_owner_id`) are visual-only and never touch trading state; `compiler.create_logic(..., owner_id)` feeds `source_strategy`.

**Research rule:** `strategy.research` never imports `backtest` at runtime. Variant re-execution is injected: `run_parameter_sensitivity(..., variant_executor=None)` accepts a backtest-layer callable (e.g. `backtest.runner.run_variant_backtest`); without one it returns structured variants (`trades=()`, `backtest_required` metadata).

**Consumes:** `core`, `market`.

**Produces:** `Signal` streams, strategy records (`.py` Python code, no IR).

**Forbidden:** `data` internals, `chart` internals, `app` internals.

**Invariants:**
- Python `class Strategy(PythonStrategy)` is the ONLY execution path — no `.vstrat` DSL, no parser/compiler/IR/VM, no `exec` of DSL.
- `StrategyParameters` frozen; `StrategyRegistry` is the registry of definitions.
- Backtest must obtain strategies via `strategy` public API (`compile_strategy` → Python class), not via file parsing.

**AI modification:** Keep Python-native path; do not reintroduce `.vstrat` DSL. Add strategy via new `.py` record (Python class).

**Validation:** `05_strategy/strategy/tests` (Python strategy tests).

---

### 5.7 Module: `06_backtest` — Research Engine

**Responsibility:** Historical replay, execution simulation, positions, journal, metrics. Python orchestration over Rust numeric kernels.

**Public API** (`backtest/__init__.py`):
`BacktestRunner`, `run_variant_backtest`, `BacktestWorker`, `BatchEnqueued`, `BacktestConfig`, `BacktestResult`, `StrategyResult`, `TradeRecord`, `EquityPoint`, `PerformanceMetrics`, `RunBacktest`, `BacktestStarted`, `BacktestProgress`, `BacktestCompleted`, `BacktestFailed`, `backtest_manifest`, `validate_backtest_form`
`BatchSpec` / `SymbolBatchResult` / `run_symbol_batch` / `default_batch_workers` / `execute_bars`: bounded parallel multi-symbol batch (one strategy × N symbols; compile-once per worker, shared `execute_bars` core with the single path, deterministic symbol order, per-symbol error isolation, stock-level progress, `include_plots=False` default for bulk ranking).
`derive_symbol_results(base, symbols)`: single-pass grouped equivalent of per-symbol `derive_symbol_result` (identical math, used by stock ranking).
`BacktestWorker` batch queue: `enqueue_batch(BatchEnqueued)` / `cancel_batch()` + `batch_progress` / `batch_done` / `batch_failed` Qt signals (payloads are plain tuples, not bus events — only the final merged `BacktestCompleted` travels the bus).
`StrategyResult.chart_plots: tuple[Any, ...]` (defaulted): universal strategy-owned plot events (same contract live/backtest/replay), forwarded from `logic.get_plot_events()`; directional/symbol derivations preserve it alongside `chart_series`.
`StrategyResult.muted_bars: tuple[int, ...]` (defaulted, generic ints): strategy-declared visually silent bars from `logic.get_muted_signal_bars()` (`PythonStrategy.mute_signal_bar`), consumed by `TradeOverlay` coverage alongside marker bars; directional derivations preserve it. Trading data is never affected.

**Consumes:** `core`, `market`, `strategy`.

**Produces:** `TradeRecord`/`EquityPoint`/metrics, events.

**Forbidden:** `data` provider SDK, `chart` rendering.

**Invariants:**
- `BacktestRunner(repository, registry, data_dir)` loads Python strategy via `get_strategy_by_id` → `compile_strategy` → `PythonStrategy`; no `.vstrat`/VM fallback.
- Batch execution reuses the single-run core (`execute_bars`) and the same repository/compile/metrics helpers — same bars in, same results out; only scheduling differs.
- Metrics math is Rust-owned (`rust/vayren-core`, `metrics` kernels: drawdown/equity/Sharpe; measured 4–5× on 20–100k inputs); `engine/metrics.py` keeps model assembly + `None`-semantics only.
- `validate_backtest_form` is honest validation (no fake results).
- `BacktestWorker` off-UI-thread (like `DownloadWorker`).
- Cross-module imports use the provider's public surface (`strategy`, `market`, `core`)
  wherever the name is exported. Accepted exceptions (documented, not accidental):
  `strategy.language.*` (strategy exposes no public loader API; function-level imports
  in `runner.py`) and `strategy.research.lineage` (optional, lazy, try/except-guarded
  in `execution.py` by design). The domain-level arrow backtest → strategy is still
  enforced by `validate_imports.py`.

**AI modification:** Preserve Python dispatch; respect `PerformanceMemory` measured-only metrics.

**Validation:** `06_backtest/backtest/tests` + `scripts/run_tests.py` partitions.

---

### 5.8 Module: `07_risk` — Safety Gates

**Responsibility:** Fail-closed pre-order policy evaluation, latched kill switches, session/clock rules. No order planning, no broker knowledge, no market reads.

**Public API** (`risk/__init__.py`):
`RiskPolicy`, `RiskRequest`, `RiskCheck`, `RiskDecision`, `RiskEngine`, `KillSwitch`, `KillSwitchState`, `SessionRules`, `within_session`, `clock_sane`, `risk_manifest`

**Consumes:** `core` (contracts for the manifest only).

**Produces:** `RiskDecision` (approved + per-check audit trail, or denied with reasons).

**Forbidden:** Everything except `core`. In particular: no `market` reads, no `strategy` imports, no broker access — the engine judges data snapshots handed to it.

**Invariants:**
- `RiskEngine.evaluate` never raises: any internal error denies with reason `risk engine error — fail closed`.
- Denied decisions always carry non-empty reasons; approved decisions record every check.
- Approved intent IDs are remembered; a repeated intent ID is denied (duplicate protection).
- Kill-switch state persists to JSON; an engaged switch survives restarts and is only released by explicit `disengage`.
- `clock_sane` requires caller-supplied epoch on the event-time basis (deterministic, no hidden tz).

**AI modification:** Add a gate by adding a named check (never by weakening an existing one). Keep evaluation side-effect-free apart from the seen-intent set and kill-switch file.

**Validation:** `07_risk/risk/tests` + `scripts/run_tests.py` partitions.

---

### 5.9 Module: `08_execution` — Live/Paper Execution

**Responsibility:** Run registered strategies against normalized market events through risk → plan → engine → broker → portfolio, with journal/replay/regime/adaptive observation. Strategy logic is reused from `05_strategy`, never reimplemented.

**Public API** (`execution/__init__.py`, additions Phase 15):
`SandboxBroker`, `ReadOnlyBroker`, `BrokerCredentials`, `CredentialStore`, `EnvCredentialStore`, `validate_credentials`, `evaluate_live_gates`, `LiveGatesReport`, `confirm_account`, `risk_configuration_valid`, `RateLimiter`, `RetryKind`, `classify_retry`, `clock_drift_ok`, `LiveArm`, `arm_transition`, `TimeoutPolicy`, `BackoffPolicy`, `ReconnectPolicy`, `ActivationReport`, `ActivationStep`, `evaluate_activation`, `record_activation`, `funds_snapshot_from_face`, `funds_valid_for_live`, `risk_capital_from_funds`.
`execution_manifest`, events (`MarketEvent`, `QuoteEvent`, `TradeEvent`, `CandleEvent`, `OrderBookEvent`, `HeartbeatEvent`, `SignalGenerated`, `RiskApproved`, `RiskDenied`, `OrderPlanned`, `OrderSubmitted`, `OrderAcknowledged`, `OrderFill`, `OrderRejected`, `PositionUpdated`, `KillSwitchEngaged`, `ACTIVATION_EVALUATED`, `RECONCILED`, `IDEMPOTENCY_RESTORED`), models (`StrategySignal`, `ExecutionIntent`, `make_intent_id`, `OrderState`, `OrderPlan`, `BrokerOrder`, `Fill`, `Position`, `AccountSnapshot`, `StrategyRuntimeContract`), `MarketDataProvider`, `ReplayProvider`, `StreamNormalizer`, `inspect_strategy`, `LiveSession`, `OrderPlanner`, `ExecutionEngine`, `BrokerAdapter`, `PaperBroker`, `resolve_broker`, `ExecutionMode`, `ModeGates`, `PositionLedger`, `ReconcileStatus`, `ReconciliationVerdict`, `verdict_of`, `record_verdict`, `reconcile_positions/orders/funds`, `StatisticalRegimeDetector`, `LiveEventRecorder`, `replay_and_compare`, `ExecutionJournal`, `LatencyTracker`

**Consumes:** `core`, `market` (`Bar` only), `strategy` (public surface: logic, signals, params, definitions), `risk` (engine, kill switch, policy).

**Produces:** Fills, positions, journal facts, replay tapes, reconciliation reports.

**Forbidden:** `02_data`, `04_chart`, `app` internals, `backtest`, `market.database`. In particular: strategy code never touches the broker (no path exists), risk never sees adaptive output as authority, adaptive code never touches risk limits or kill switches.

**Invariants:**
- Default mode is PAPER; LIVE without all five gates degrades to PAPER with recorded reasons. LIVE_BROKER_INTEGRATION = NOT_CONFIGURED (no live adapter ships).
- REAL_BROKER_UNSPECIFIED (Phase 15 verdict): the only broker in the repo (Zerodha/KiteConnect) is a historical-data provider in `02_data`; no execution venue is configured anywhere. No Zerodha order code exists or may be inferred from data credentials.
- LIVE submissions additionally require explicit arming (`DISARMED` default; consent never inferred). Read-only verification is available via `ReadOnlyBroker` (mutations raise before reaching any venue).
- No order without a risk approval; the FINAL planned quantity is re-validated after adaptive shrink.
- `UNKNOWN` order states exit only via explicit `reconcile()`; never blind-resubmit. Engine idempotency snapshots (`snapshot`/`restore`) persist through checkpoint/recover; restored UNKNOWN orders reconcile before any new submission; unresolved restart mismatch blocks start (fail-closed; operator clears checkpoint for a clean paper restart).
- Lifecycle includes `RECONCILING`: recovered sessions run `reconcile_now()` (positions+orders+verdict journaling) before VALIDATING. Funds reconcile via explicit `reconcile_funds` (ledger-vs-venue cash semantics differ by design — paper deducts notional on fill; the activation ceremony carries the funds verdict).
- Activation ceremony (`evaluate_activation`, 12 ordered steps, pure verifier): broker → LIVE env → credentials → account → market data → funds → risk → reconcile → gates → kill switch → explicit ARM → START (never auto-ok). Every evaluation journals `ACTIVATION_EVALUATED` without secrets.
- Intent IDs are deterministic (`strategy:version:event_seq:intent_seq`); duplicates are denied at both risk and engine.
- Startup order RECOVER → RECONCILE → VALIDATE → WARMUP → READY is enforced; orders are impossible before RUNNING.
- Warmup feeds history with signals discarded; restart recovery re-warms from persisted bar windows (no logic pickling).
- Backtest ↔ paper parity: identical logic + identical bars → identical signal stream (modulo the documented one-bar warmup-boundary transient); identical fill-price math. Sizing models differ by design and are not compared.
- Language ownership (FINAL migration): the order lifecycle TABLE and terminal set are Rust-owned (`rust/vayren-core`, `order_state`; ABI v1). `execution.models.order` re-exports `TRANSITIONS`/`TERMINAL_STATES` as a read-only projection (no Python table); `execution.native_order_state.transition_allowed` is the single legality check used by `ExecutionEngine`. Public import paths unchanged; reintroducing a Python table fails `validate_language_ownership.py`.

**AI modification:** New venue → new `BrokerAdapter` implementation + direct `BrokerRegistry.register(...)` in `09_broker` (M7: the `register_adapter` shim is retired; never touch strategy/risk). New order type → planner + engine transition coverage + tests. Keep the runtime synchronous and deterministic.

**Validation:** `08_execution/execution/tests` + `scripts/run_tests.py` partitions.

---

### 5.10 Module: `09_broker` — Unified Broker Layer (M8 production adapter framework)

**Responsibility:** Broker-independent coordination boundary between VAYREN and any venue: one error vocabulary, one capability vocabulary (bool + tri-state), three protocol faces, the ONLY broker registry, the single selection contract, UBL-owned funds/health/credential models. No transport, no SDK, no network, no secrets here — adapters live in `broker/adapters/<broker>/`.

**Public API** (`broker/__init__.py`):
`ErrorCode`, `BrokerError`, `BrokerNotRegisteredError`, `UnsupportedCapabilityError`, `CredentialsNotReadyError`, `error_code_from_legacy`, `Domain`, `Environment`, `Caps`, `CapabilitySet`, `CapabilityStatus`, `capability_status`, `HistoricalFace`, `MarketDataFace`, `TradingFace`, `BrokerPlugin`, `PluginLike`, `StaticPlugin`, `FactoryPlugin`, `BrokerRecord`, `BrokerRegistry`, `DuplicateBrokerError`, `default_registry`, `BrokerSelection`, `SelectionError`, `SelectionStore`, `MemorySelectionStore`, `surface_resolution`, `surface_status`, `FileSelectionStore`, `SelectionLoadError`, `FundsSnapshot`, `FUNDS_UNKNOWN`, `FUNDS_UNSUPPORTED`, `is_unknown`, `is_unsupported`, `require_funds`, `CredentialScope`, `CredentialRef`, `CredentialMetadata`, `CredentialResolver`, `validate_refs`, `validate_metadata`, `HealthState`, `BrokerHealth`, `health_from_legacy`, `BrokerIdentity`, `identity_of`

**Consumes:** Stdlib only (INVARIANT: `broker` imports nothing — enforced by `validate_imports.py`).

**Produces:** Registry records, selection facts, capability verdicts, funds/health/credential views for `00_app` composition, `02_data` history and `08_execution` trading.

**Forbidden:** Every other chapter (`core`, `market`, `chart`, `data`, `strategy`, `backtest`, `risk`, `execution`, `app`); broker SDKs; network clients; secret values.

**Invariants:**
- One registry (`BrokerRegistry`), one selection (`BrokerSelection`), one capability vocabulary (`Caps`), one error vocabulary (`ErrorCode`) — AST-enforced, no second systems.
- Tri-state declaration: SUPPORTED (advertised) / NOT_SUPPORTED (known id, absent) / NOT_CONFIGURED (unknown id or undeclared context). Bool `supports`/`surface_allowed` preserved for compatibility.
- Unsupported capability → `UnsupportedCapabilityError` before any transport call; unknown broker → `BrokerNotRegisteredError`; corrupt selection → `SelectionLoadError`. No silent fallback; PAPER downgrade semantics unchanged.
- Credentials are key-references only (`CredentialRef` has no value field); values never in selection persistence, logs, events, journals or UI. PAPER/SANDBOX/LIVE references isolated by environment.
- Health is connection-only (`HealthState`: DISCONNECTED/CONNECTING/CONNECTED/DEGRADED/AUTH_REQUIRED/AUTH_FAILED/RATE_LIMITED/UNKNOWN); health never implies LIVE readiness (five gates authoritative).
- `UNKNOWN` funds ≠ `0.0`; unsupported funds ≠ `0.0` (`FUNDS_UNKNOWN`/`FUNDS_UNSUPPORTED` sentinels + `require_funds` gate).
- Order lifecycle is broker-independent (`execution.models.order`): CREATED/VALIDATED/SUBMITTED/ACKNOWLEDGED/PARTIALLY_FILLED/FILLED/REJECTED/CANCEL_PENDING/CANCELLED/MODIFY_PENDING/MODIFIED/EXPIRED/UNKNOWN; UNKNOWN exits only via `reconcile()`; unknown submissions reconcile first, never blind-retry (`RetryKind.MUST_RECONCILE_FIRST`).
- Network boundary: Core → UBL → Adapter → Network. Network clients only in `09_broker/broker/adapters/` or retained `02_data/data/provider/` (validator-enforced `NETWORK_DENYLIST`).
- Zerodha is history-only (trading/funds NOT_SUPPORTED); no `if broker == ...` outside adapters; LIVE_BROKER_INTEGRATION = NOT_CONFIGURED.

**AI modification:** New venue → new `broker/adapters/<venue>/` package (identity + capability matrix + record builder, constructor-injected transport) + `BrokerRegistry.register(...)` at the composition root; run the generic harness `broker/tests/test_adapter_contract.py` against it. Never add SDK/network imports outside the adapter package; never invent capabilities.

**Validation:** `09_broker/broker/tests` + `scripts/run_tests.py` partitions + `validate_imports.py` (SDK/network) + `validate_structure.py` (broker domain).

---

## 6. Cross-Module Interfaces

| Interface | Owner | Consumer | Data |
|---|---|---|---|
| `market → chart` | `market` emits `DataLoaded(symbol, bars)` | `ChartEngine` consumes | `tuple[Bar]` |
| `chart → app` | `chart` emits `ChartReady(model)` | `ChartWindow` consumes | `ChartModel` |
| `app → market` | `app` (lifecycle/window) emits `LoadSymbol`, `TimeframeChanged`, `ListSymbols` | loaders consume | symbol/limit |
| `data → app` | `data` emits `DownloadCompleted(new_rows, db_total)` | side panel consumes | counts |
| `strategy → backtest` | `strategy` provides `StrategyRuntime` | `BacktestRunner` consumes | `Signal` |
| `strategy → execution` | `strategy` provides `PythonStrategy`/`Signal`/`BarView` | `LiveSession` consumes | same `Signal`, no rewrite |
| `risk → execution` | `risk` provides `RiskDecision` | `LiveSession` consumes | approved/denied + reasons |
| `execution → broker` | `execution` drives `BrokerAdapter` | `PaperBroker` (or registered venue) | `OrderPlan` → `Fill` |

> **Rule:** Consumers depend on the provider's **manifest capability** (`data.query.candles`, `chart.render`, `historical_data.download`) and on the **event** payload, not on internal files.

---

## 7. Event Contracts

Authoritative list: `90_brain/event_catalog.md` (19 events). Summary:

| # | Event | Owner | Payload |
|---|---|---|---|
| 1 | `AppStarted` | `core` | — |
| 2 | `ListSymbols` | `market` | — (request) |
| 3 | `SymbolsListed` | `market` | `symbols: tuple[str,...]` |
| 4 | `QuotesLoaded` | `market` | `quotes: tuple[SymbolQuote,...]` |
| 5 | `LoadSymbol` | `market` | `symbol, limit: int|None` |
| 6 | `ListTimeframes` | `market` | `symbol` |
| 7 | `TimeframesListed` | `market` | `symbol, timeframes: tuple[str,...]` |
| 8 | `TimeframeChanged` | `market` | `symbol, timeframe, limit: int|None` |
| 9 | `DataLoaded` | `market` | `symbol, bars: tuple[Bar,...]` |
| 10 | `ChartReady` | `chart` | `model: ChartModel` |
| 11 | `WindowRendered` | `chart` | — |
| 12 | `DownloadRequest` | `data` | `symbol, interval, from_date, to_date` |
| 13 | `CoverageRequest` | `data` | `symbol, interval` |
| 14 | `CancelDownload` | `data` | `request_id` |
| 15 | `DownloadStarted` | `data` | `request_id, symbol, interval, from_date, to_date` |
| 16 | `DownloadProgress` | `data` | `request_id, symbol, interval, message` |
| 17 | `DownloadCompleted` | `data` | `request_id, symbol, interval, new_rows, db_total` |
| 18 | `DownloadFailed` | `data` | `request_id, symbol, interval, error` |
| 19 | `DownloadCoverage` | `data` | `request_id, symbol, interval, info: SymbolInfo` |
| 20 | `RunBacktest` etc. | `backtest` | `BacktestConfig` |
| 21 | `StrategiesListed` etc. | `strategy` | lab events |
| 22 | `MarketEvent` + `Quote/Trade/Candle/OrderBook/HeartbeatEvent` | `execution` | normalized live data (`symbol, timestamp, seq`) |
| 23 | `SignalGenerated` / `RiskApproved` / `RiskDenied` | `execution` | `request_id, signal/intent ids` |
| 24 | `OrderPlanned` / `OrderSubmitted` / `OrderAcknowledged` | `execution` | `request_id, client_order_id` |
| 25 | `OrderFill` / `OrderRejected` / `PositionUpdated` | `execution` | fills, quantities |
| 26 | `KillSwitchEngaged` | `execution` | `level, reason` |

Contracts: frozen dataclass `Event` subclasses, exact-type bus dispatch, no widget/connection/callable in payload, `limit None` semantics preserved.

---

## 8. Data Contracts

**`Bar`** (`market/models/bar.py`): `symbol, open, high, low, close, volume, timestamp: ISO-8601 str, bar_size?, vwap?, trades?, source?` — frozen.

**`SymbolQuote`:** `symbol, price=close, change_pct=(close-open)/open*100, timestamp` — derived from latest real candle only, never fabricated.

**`ChartModel`:** `symbol, bars: tuple[Bar] ascending, timeframe?, exchange?` — frozen.

**Database (per-symbol file):** `D:\ZerodhaTradingData\<SYMBOL>.db` / `<data_dir>/SYMBOL.db`
```sql
CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume INTEGER);
```
Legacy `candles(symbol, timestamp, ...)` only in `SqliteCandleDatabase` tests.

**Timeframe ladder:** `TIMEFRAME_LADDER = ("1m","3m","5m","15m","30m","45m","1h","2h","4h","1D","1W")`; helpers `timeframe_seconds`, `available_timeframes(base)` (multiples only).

**Execution intents/orders:** `ExecutionIntent` (strategy wish + traceable ids, never an order), `OrderPlan` (deterministic planner output), `BrokerOrder` (§12 state machine), `Fill` (fill economics) — all frozen.
**Risk:** `RiskPolicy` (hard limits), `RiskRequest` (snapshot), `RiskDecision` (approved + named checks or denied with reasons) — frozen.
**Live data:** `CandleEvent`/`QuoteEvent`/`TradeEvent`/`OrderBookEvent`/`HeartbeatEvent` (`symbol, timestamp UTC ISO, seq`) — frozen.

---

## 9. Boundary & Security Rules

| Boundary | Who owns | Who may call | Who may NOT | Data crossing | Must NOT cross |
|---|---|---|---|---|---|
| `core` | platform | everyone | — | events, manifests | domain concepts |
| `market.database` | `market` | `market.repository` only | `chart`, `data`, `app` | `Bar` | raw rows, SQL handles |
| `data/provider` | `data` | `HistoricalDownloadEngine` via `Provider` protocol | `market`, `chart` | canonical `1m..1h` intervals | Kite interval IDs, tokens |
| `chart/renderer` | `chart` | `CandleChartWidget` | loaders | `Bar`/`ChartModel` | bus, SQL |
| `strategy` | `strategy` | `backtest` via public registry; `execution` via public surface | `chart` | `Signal` | `exec` strings |
| `risk` | `risk` | `execution` via `RiskEngine.evaluate` | everyone else (no reads, no broker) | `RiskRequest` | market state, broker handles |
| `execution/broker` | `execution` | `ExecutionEngine` via `BrokerAdapter` protocol | `strategy`, `risk`, `chart`, `backtest`, UI | `OrderPlan` | broker SDKs, credential values |
| `execution/sandbox` | `execution` tests/sessions | explicit `SandboxBroker(...)` construction or direct `BrokerRegistry.register` | production code paths, live mode | scripted fills | real-venue behavior |
| `execution/credentials` | `execution` | `validate_credentials` pre-submit; values in store only | logs, journal, repr, events | key refs + identity | secret values |
| `execution/arming` | `execution` session | explicit `arm(reason)` call only | credentials, gates, kill switch | DISARMED default | inferred consent |
| `execution/readonly` | tests/diagnostics | read-only verification of any adapter | order submission paths | reads | mutations |
| `broker SDKs` | `02_data/data/provider/*` only | isolated venue packages | strategy, risk, execution, chart, app, backtest | nothing | `kiteconnect` etc. outside providers |
| Credentials | `data/provider` | `AuthEngine` at auth time | events, logs, UI, `SystemModel` | store→env fallback | secrets in plain text |
| Live orders | `risk`+`execution` | PAPER default; LIVE needs 5 explicit gates | default configs, unconfigured venues | `RiskDecision` | real orders without gates |

**Security invariants:** `ProviderCredentialsManager` validates label-only errors; `WindowsCredentialStore` uses `vayren:zerodha` target; `ZerodhaCredentials.from_env()` redacts `repr`.

---

## 10. Performance Contracts

| Area | Contract | Notes |
|---|---|---|
| Timeframe aggregation | Use `fromisoformat` + single-pass buckets (not `strptime` per row) | Preserves correctness; 100× faster |
| Quote load | One-time `get_quotes(527)` at `SymbolsListed` (~0.5s), then `QuoteLoader` no-op cache | Never per-paint DB query |
| Chart paint | Static pixmap cache `(model, window, price, size)`; crosshair = blit only (~0.9 ms/frame) | `paint_grid` only on cache rebuild |
| SystemModel | Built once at `Bootstrap._build_architecture()` (~0.13 ms) | Not per-event |
| Live event dispatch | Sync in-process pipeline, no threads; measured ~0.2 ms/200-candle batch (~932k ev/s) | `scripts/bench_execution.py` baseline |
| Risk evaluation | Single request ~0.017 ms median | Same baseline script |
| Paper session | 60 candles end-to-end ~3 ms median (~20k ev/s) | Same baseline script |
| Sandbox bootstrap | Startup ~0.3 ms, event processing ~3.4 ms, reconcile ~0.03 ms, shutdown ~0.01 ms | `sandbox_bootstrap_e2e` baseline |

No performance guarantee invented beyond measured facts in `ai_memory.md`.

---

## 11. Contract Change Rules

```
Existing contract
  ↓ is change public (export in __init__.py, event payload, DB schema)?
  ├─ NO (internal) → change freely, keep contract doc unchanged
  └─ YES (public) → update 90_brain/module_contracts.md + affected consumers + tests/validators + event_catalog if event → verify with scripts/validate_*
```

- Public API change ⇒ bump version in manifest (`market_manifest v1.0.0`), update `__all__`, add test covering new contract.
- Never change contract and implementation in one undocumented step.

---

## 12. AI Agent Rules

Before modifying a module:
1. Read this module's contract (§5).
2. Identify responsibility — do not expand it.
3. Check allowed dependencies (§4) — do not import forbidden modules.
4. Use only public APIs (`__init__.py`).
5. Preserve invariants (§3, §5, §9).
6. Do not fabricate data; never bypass `Provider` boundary for convenience.
7. Update this file if public contract legitimately changes.
8. Run `python scripts/validate_imports.py` + `python scripts/validate_structure.py`.
9. Never bypass `AiBoundary` forbidden actions.
10. Migration: follow invisible feature-driven rule per `ARCHITECTURE_CONSTITUTION.md` §5, §13, §17 — migrate directly related legacy slice as part of the feature (smallest useful), no unrelated migration.

Do not duplicate `AGENTS.md` — this file owns boundaries, `AGENTS.md` owns workflow.

---

## 13. Contract Validation

| Check | Command |
|---|---|
| Structure (required files per domain) | `python scripts/validate_structure.py` — checks `__init__.py`, `README.md`, `manifest.py`, `models/`, `events/`, etc. (7 domains: app/core/data/market/chart/strategy/backtest) |
| Import boundaries (allowed graph) | `python scripts/validate_imports.py` / `--json` — AST check forward-only runtime imports (`if TYPE_CHECKING:` blocks exempt, see §4) |
| Tests (contract behavior) | `pytest` / `make check` (lint+format+typecheck+test+validators) |
| Manifests | `market_manifest()`, `chart_manifest()`, `data_manifest()`, `strategy_manifest()`, `backtest_manifest()` raise `ManifestError` on violation |
| Events | `90_brain/event_catalog.md` + type-exact dispatch tests |

If a contract is violated, the validator and tests must fail — do not weaken validators to make a change pass.
