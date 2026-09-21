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

**Responsibility:** Native composition package. Owns the headless backend (`app/headless.py`) and the composition services (`app/services/`); the production window is the Rust + Slint shell.

**Public API** (`app/headless.py` + `scripts/launch_native.py`):
| Export | Contract |
|---|---|
| `make dev` | `launch_native.py` → `vayren-shell` binary → backend spawn → snapshots → Slint loop |
| `app.headless` | JSON protocol over stdio: `ready`/`symbols_listed`/`market_snapshot`/`system_snapshot`/`portfolio_snapshot`/`live_snapshot`/`research_snapshot`/`lab_snapshot`/`error` |
| `DEFAULT_DATA_DIR` / strategy dir | Env → legacy folder → per-user folder precedence (same family as the old CLI) |

**Consumes:** `core` (logging, registries, contracts), `data` (provider factory, settings, credentials), `market` (`SymbolRepository` only), `strategy` (storage, compiler, research), `backtest` (histories), `execution` (`LiveTradingService`), `risk`, `broker` (selection, manager).

**Produces:** backend snapshots consumed by `vayren-shell`: market (watchlist + bars), system (brokers + selection), portfolio (positions/orders/fills), live (session + gates), research (strategies + experiments), lab (library + config). `LiveTradingService` (one `LiveSession` per symbol over `SqliteTailProvider`, PAPER default, config/checkpoints under `<data_dir>/live`, never auto-starts). `ResearchService` over `strategy.research` (Python). Portfolio/Live/Research/Lab/System screens are native Slint (`rust/vayren-shell/ui/*.slint` + `src/*.rs`); backend state feeds them through `python_bridge.rs`, never bus/SQL/broker handles.

**Dependencies:** See §4.

**Forbidden:** Business logic, SQL, painting. No domain logic here.

**Invariants:**
- INVARIANT: Importing `app` must not start the app (no side-effects at import). The backend speaks JSON only.

**Implementation note:** the launcher resolves paths and picks the binary; the shell builds each view-model once from its snapshot at startup. No plugin scanning, no reflection.

**AI modification:** Add a screen → add a backend snapshot command + a Rust state + its Slint projection. Do not add UI frameworks.

**Validation:** `scripts/validate_imports.py` enforces app→allowed; `scripts/validate_structure.py` checks `00_app/app/{__init__,headless,tests}` exist.

---

### 5.2 Module: `01_core` — Foundation

**Responsibility:** Core/bus/registry/contracts/system are Rust-owned; their
Python twins were removed (ownership purity). Python retains `core.ai/*`
(AI, Python-owned), `core/native/*` (FFI bridge) and the zero-logic `Event`
marker base for Strategy/AI/Research event vocabulary.

**Public API** (`core/__init__.py`): `Event` only.

---

### 5.3 Module: `02_data` — Historical Download (Write Path)

**Responsibility:** Download missing historical OHLCV into per-symbol SQLite files that `market` reads. MODE 1 only: download, idempotent resume, never audit/repair/verify.

**Public API** (`data/__init__.py`):
`DownloadSettings`, `data_manifest`, events `DownloadRequest`, `CoverageRequest`, `CancelDownload`, `DownloadStarted`, `DownloadProgress`, `DownloadCompleted`, `DownloadFailed`, `DownloadCoverage`.
Flow mechanics ki sole authority Rust hai (`download` + `download_engine`); Python twins (`downloader/engine.py`, `sweep.py`, `queue.py`, `worker.py`) removed.

**Consumes:** `core` only (bus, contracts).

**Produces:** Files `<data_dir>/SYMBOL.db` + events. Capabilities `historical_data.download`, `.coverage`, `.status`.

**Dependencies:** `core` only. INVARIANT: Outside `data/provider/`, no module may load any broker SDK (`data.provider.contract` vocabulary only).

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

**Provider boundary** (`data/provider/contract.py`): `Provider` protocol `available()`, `symbols()`, `fetch_candles(symbol, interval, start, end)`, `new_session()`, `renew()`; sentinels `TOKEN_EXPIRED`/`RATE_LIMITED`; `ProviderError(message, code)` 7 codes (`AUTHENTICATION_FAILED` …). `ZerodhaProvider` in `data/provider/zerodha/adapter.py` is the history-only Kite venue. `FyersProvider` in `data/provider/fyers/adapter.py` is the FYERS venue (own credential schema `app_id`/`secret`/`client_id`/`totp_secret`/`pin`/`redirect_uri`, official API v3 OAuth in `fyers/live_auth.py`, fully automatic TOTP+PIN login in `fyers/auto_auth.py` mirroring the Zerodha auto-auth engine, read-only session in `fyers/session_adapter.py` — no order placement exists; history stays fail-closed `PROVIDER_UNAVAILABLE` in this phase). M7: the `register_provider` shim is retired — venues register directly in the single UBL `BrokerRegistry`; `build_provider(settings)` is retained only as a compatibility delegate while the composition root resolves the selected broker's historical face straight from the registry. `DownloadSettings.provider` is derived state (INVARIANT: always equals `BrokerSelection.name`; the ONLY product write path is `Bootstrap`). `store → env → Not Configured` credential priority via `ProviderCredentialsManager`.

**Implementation note:** Flow mechanics Rust-owned (`download_engine::DownloadEngine`, `download::forward_sweep`, chunk windows, coverage verdicts — Rust-side tested). Python holds the provider SDK seam, SQLite storage/scanner, settings, event vocabulary and manifest only.

**Performance constraints:** `get_quotes` not here; download is I/O-bound, not per-candle hot path.

**AI modification:** Never add `market`/`chart` imports. Keep broker IDs inside `zerodha/` only.

**Validation:** `data/tests` (provider boundary no-network; flow mechanics Rust-side tested).

---

### 5.4 Module: `03_market` — Storage & Query (Read Path)

**Responsibility:** Market storage/loading/aggregation are Rust-owned
(`rust/vayren-core`, `market` + `aggregate`); their Python twins
(database, repository, loader, timeframe, events, manifest) were removed
(ownership purity). Python retains `Bar` (zero-logic payload shape for
Strategy code) and the native bridges.

**Public API** (`market/__init__.py`): `Bar` only.

---

### 5.5 Presentation — Rust + Slint chart (`rust/vayren-shell`)

**Responsibility:** Snapshot → state → projection → screen. Pure view-models, viewport math, watchlist/tools/download console.

**Public API** (`vayren_shell::market` + `ui/market.slint`):
`MarketState` (`set_bars`, `interact`, `apply_snapshot_json`), `MarketBar`, `WatchEntry`, `MarketView` (`project`), `MarketScreen` bindings, `shell::apply_market`/`wire_market`.

**Consumes:** backend market snapshots (watchlist + bars + timeframes).

**Produces:** projected Slint chart (candles, axes, markers, header, status strip).

**Forbidden:** business logic in screens; bus/SQL handles in the view layer.

**Invariants:**
- `MarketState` holds ascending bars; `set_bars` anchors the latest readable window; viewport math is pure and headless-tested.
- Renderers are pure projections: candles (wicks/bodies/volume), time axis (calendar ladder), crosshair (1 px), header strip (symbol + meta + price/time pills). No state, no events.
- Interactions report to Rust (`interact`): wheel=zoom (cursor anchor), drag=2D pan (time+price), price-strip drag=vertical scale, symbol/timeframe refetch through the bridge.
- Download console state (`market_download.rs`) is local-optimistic; backend wires stay queued.

**Theme contract** (`ui/palette.slint` + `ui/components.slint`): `VayrenPalette` (bg `#101418`, surface, hairline, text/muted, teal accent) + `VayrenDesign` spacing/type scale. Slint logical pixels throughout.

**AI modification:** New visual → new projection field + screen binding; do not add business logic to screens.

**Validation:** `cargo test -p vayren-shell` (market/viewport/render suites) + `shell::tests::ui_bindings_and_navigation`.

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

**Responsibility:** Backtest runner/engine/validation/models are Rust-owned
(`rust/vayren-core`, `backtest` + `backtest_engine` + `metrics`); their
Python twins were removed (ownership purity). Python retains the native
bridges only.

---

### 5.8 Module: `07_risk` — Safety Gates

**Responsibility:** Risk engine/session/kill-switch/models are Rust-owned
(`rust/vayren-core`, `risk_engine` + `kill_switch`); their Python twins
were removed (ownership purity). Python retains the native bridges only.

---

### 5.9 Module: `08_execution` — Live/Paper Execution

**Responsibility:** Session/engine/venues/ledger/reconcile/journal/runtime/planner
are Rust-owned (`rust/vayren-core`, `execution_engine` + `execution` +
`order_state` + `live_readiness` + `resilience` + `sandbox_policy`); their
Python twins were removed (ownership purity). Python retains the AI-adjacent
layer (`adaptive/`, `regime.py`, `ml_interfaces.py` — Python-owned), the
`OrderState` enum + frozen market-event shapes consumed by it, and the
native bridges (`native_execution`, `native_order_state` — kernel-direct
legality checks, no materialized tables — `native_policy`,
`market_data/native_normalizer`).

---

### 5.10 Module: `09_broker` — Unified Broker Layer (M8 production adapter framework)

**Responsibility:** Broker-independent coordination boundary between VAYREN and any venue: one error vocabulary, one capability vocabulary (bool + tri-state), three protocol faces, the ONLY broker registry, the single selection contract, UBL-owned funds/health/credential models. No transport, no SDK, no network, no secrets here — adapters live in `broker/adapters/<broker>/`.

**Public API** (`broker/__init__.py`):
`ErrorCode`, `BrokerError`, `BrokerNotRegisteredError`, `UnsupportedCapabilityError`, `CredentialsNotReadyError`, `error_code_from_legacy`, `Domain`, `Environment`, `Caps`, `CapabilitySet`, `CapabilityStatus`, `capability_status`, `HistoricalFace`, `MarketDataFace`, `TradingFace`, `BrokerPlugin`, `PluginLike`, `StaticPlugin`, `FactoryPlugin`, `BrokerRecord`, `BrokerRegistry`, `DuplicateBrokerError`, `default_registry`, `BrokerSelection`, `SelectionError`, `SelectionStore`, `MemorySelectionStore`, `surface_resolution`, `surface_status`, `FileSelectionStore`, `SelectionLoadError`, `FundsSnapshot`, `FUNDS_UNKNOWN`, `FUNDS_UNSUPPORTED`, `is_unknown`, `is_unsupported`, `require_funds`, `CredentialScope`, `CredentialRef`, `CredentialMetadata`, `CredentialResolver`, `validate_refs`, `validate_metadata`, `HealthState`, `BrokerHealth`, `health_from_legacy`, `BrokerIdentity`, `identity_of`, `BrokerStatus`, `READY_STATES`, `BrokerSpec`
plus the universal authentication contract (`broker/auth.py`): `AuthCapability` (13 declared capabilities: API_KEY/API_SECRET/CLIENT_ID/USERNAME/PASSWORD/TOTP/ACCESS_TOKEN/OAUTH/AUTH_CODE/REDIRECT_URI/BROWSER_LOGIN/SESSION_REFRESH/CUSTOM — declared per-venue, never assumed), `AuthField` (key/label/secret/required/help/default/capabilities), `ConnectionState` (DISCONNECTED/AUTH_REQUIRED/AUTHENTICATING/CONNECTED/EXPIRED/REAUTH_REQUIRED/ERROR), `AuthErrorCode` (AUTH_REQUIRED/INVALID_CREDENTIALS/AUTH_TIMEOUT/REDIRECT_FAILED/TOKEN_EXCHANGE_FAILED/SESSION_EXPIRED/CONNECTION_FAILED/BROKER_UNAVAILABLE), `AccountIdentity`, `AuthResult` (secret-free), `BrokerAuthContract` (protocol: broker_id/display_name/authentication_schema/auth_capabilities/connect/disconnect/is_connected/connection_status/authenticate/refresh_session_if_supported/validate_credentials/get_account_identity/get_connection_metadata/secure_store_credentials/secure_store_session/load_session/clear_session), `mask_secret`, `schema_keys`, `describe_schema`.

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
- `BrokerSpec` additionally carries venue-neutral credential-shape metadata
  (`key_field`/`secret_field` defaulting to `api_key`/`api_secret`,
  `redirect_uri_field`, `config_key_map` for UI→storage key translation) so
  `BrokerManager` never branches on broker names; per-broker `callback_url`
  is surfaced per card (top-level kept for compatibility). The optional
  `auto_authenticate(config, session_store)` hook lets a venue run startup
  automatic login on the manager worker thread (`None` = legacy
  LOGIN_REQUIRED path, Zerodha default).
- Venues: `broker/adapters/zerodha/` (history-only, unchanged behavior) and
  `broker/adapters/fyers/` (identity + honestly EMPTY capability set in the
  authentication phase — history fails closed, management/selection work).
  A future broker joins by supplying one adapter package + one `BrokerSpec`.
- Network boundary: Core → UBL → Adapter → Network. Network clients only in `09_broker/broker/adapters/` or retained `02_data/data/provider/` (validator-enforced `NETWORK_DENYLIST`).
- Zerodha is history-only by default (trading/funds NOT_SUPPORTED); no `if broker == ...` outside adapters; LIVE_BROKER_INTEGRATION = NOT_CONFIGURED unless explicitly activated. Explicit activation (`VAYREN_ENABLE_LIVE_VENUE=true` + stored credentials, or the SYSTEM → BROKERS login flow) registers a SEPARATE `zerodha-live` venue (TRADING + MARKET_DATA faces over the SDK transport in `02_data/data/provider/zerodha/`); the `zerodha` history record is never modified. Management-plane states live in `BrokerStatus` (NOT_CONFIGURED … LIVE_READY); a future broker joins by supplying one `BrokerSpec` (adapter + auth flow + capabilities) — no strategy/LIVE/UI changes.

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
| `data/provider` | `data` | Rust `download_engine` via `Provider` protocol seam | `market`, `chart` | canonical `1m..1h` intervals | Kite interval IDs, tokens |
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
