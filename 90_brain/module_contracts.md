# Module Contracts — VAYREN Institutional Specification
**Owns:** Module boundaries, public APIs, dependency rules, cross-module interfaces, invariants. **Not owns:** Language ownership → `AI_ENTRY.md` §1 (machine: `ownership_policy.json`); module map/layers → `architecture.md`; events list → `event_catalog.md`; current state → `ai_memory.md`.
**When to read:** Before modifying any module or adding cross-module import.
**Related:** `AGENTS.md` (workflow), `architecture.md` (layers), `event_catalog.md` (events). **Status:** Authoritative — imports MUST use only public contracts (`__init__.py` exports); internal paths are not contracts.

## 1. Purpose
Single source of truth for boundaries, public APIs, dependencies, interfaces, invariants. Read the relevant contract before modifying that module.

## 2. Contract Philosophy
| Principle | Rule |
|---|---|
| **Public surface = `__init__.py`** | `__all__` is the contract. Anything not exported is internal. |
| **Event-driven only** | Modules never call each other directly; cross-module work is bus/bridge traffic. |
| **One responsibility** | New behavior → new module. Existing modules are never expanded. |
| **No circular dependencies** | Imports flow forward in chapter order only (see §4). |
| **Data over logic at boundaries** | Events carry data (`tuple[Bar]`, `str`, `int`) — never widgets, connections, or callables. |

> **INVARIANT:** Consumers may depend only on the documented public contract, never on internal packages.

## 3. Global Rules
| # | Rule |
|---|---|
| G1 | `EventBus` is sync, exact-type, fail-safe. Handler failure → log, app continues. |
| G2 | Subscriptions live **only** at the composition root. No other file calls `bus.subscribe`. |
| G3 | Widgets receive data via `set_model(model)` — never via bus, SQL, or loader. Painting only in `renderer`. |
| G4 | `limit: int \| None = None` means **full history**. `None` is never bound as SQL `LIMIT ?`. |
| G5 | Real market data must never be fabricated. Missing buckets are omitted, not synthesized. |
| G6 | Credentials, tokens, secrets never appear in events, settings, logs, UI text, or `SystemModel`. |
| G7 | `Bar` / events are frozen dataclasses (`tuple[Bar, ...]` is ordered). |

## 4. Module Dependency Rules
Allowed graph (machine truth: `DOMAIN_DEPS` in `scripts/validate_imports.py`). Qualifiers below are what the validator cannot express. `chart` = chart domain when present (no Python `chart` package exists).

| Module | May Import | Forbidden |
|---|---|---|
| `00_app` | `core`, `data`, `market`, `chart`, `strategy`, `backtest`, `risk`, `execution`, `broker` | — |
| `01_core` | stdlib only | every other chapter |
| `02_data` | `core`, `broker` (UBL registry/faces only) | `market`, `app`, `strategy`, `backtest`, `risk`, `execution` |
| `03_market` | `core` | every other chapter |
| `05_strategy` | `core`, `market` | `data`, `app` internals, `risk`, `execution`, `broker` |
| `06_backtest` | `core`, `market`, `strategy` | `data`, `app` internals, `risk`, `execution`, `broker` |
| `07_risk` | `core` | everything except `core` |
| `08_execution` | `core`, `market` (`Bar` only), `strategy` (public surface), `risk`, `broker` (UBL faces only) | `data`, `app` internals, `backtest`, `market.database` |
| `09_broker` | stdlib only | every other chapter |

**INVARIANT:** `from market.database import ...`, `from ..market import ...`, `from x import *` are violations. **`if TYPE_CHECKING:`** imports are annotations-only (no runtime coupling); never use them to hide a real dependency.


## 5. Module Contracts
### 5.1 Module: `00_app` — Manager
**Responsibility:** Native composition package: headless backend (`app/headless.py`) + services; production window is the Rust + Slint shell.
**Public API:** JSON protocol over stdio (`ready`/`symbols_listed`/`market_snapshot`/`system_snapshot`/`portfolio_snapshot`/`live_snapshot`/`research_snapshot`/`lab_snapshot`/`error`); `make dev` → launcher → `vayren-shell` → snapshots → Slint loop.
**Consumes:** all chapters (composition root). **Produces:** backend snapshots for `vayren-shell`.
**Forbidden:** Business logic, SQL, painting, domain logic.
**Invariants:** Importing `app` must not start the app. Backend speaks JSON only.

### 5.2 Module: `01_core` — Foundation
**Responsibility:** Bus/registry/contracts/system are Rust-owned (Python twins removed). Python retains `core.ai/*` (AI, Python-owned), `core/native/*` (FFI bridge), zero-logic `Event` marker base (sole public API).

### 5.3 Module: `02_data` — Historical Download (Write Path)
**Responsibility:** Download missing historical OHLCV into per-symbol SQLite files. MODE 1 only: download, idempotent resume, never audit/repair/verify. Flow mechanics are Rust-owned (`download` + `download_engine`).
**Public API:** `DownloadSettings`; `Provider` protocol (`available/symbols/fetch_candles/new_session/renew`, sentinels `TOKEN_EXPIRED`/`RATE_LIMITED`, `ProviderError`); `build_provider(settings)`; venues `ZerodhaProvider` (history-only), `FyersProvider` (auth-phase, history fail-closed). Boundary: broker SDKs only inside `data/provider/`.
**Invariants:** Never fabricate (`INSERT OR IGNORE` only); state from DB itself; NSE market hours/holidays respected; `DownloadSettings.provider` is derived (always equals `BrokerSelection.name`); credentials store→env→Not Configured, values never in events/logs.
**Data contract (per-symbol file):** `ohlcv(candle_time TEXT PRIMARY KEY, open, high, low, close, volume)`, `non_trading`, `history_boundaries`; canonical intervals `1m/5m/15m/30m/1h`.
**Validation:** `02_data/data/tests` (provider boundary, no network).

### 5.4 Module: `03_market` — Storage & Query (Read Path)
**Responsibility:** Storage/loading/aggregation are Rust-owned (`market` + `aggregate`); Python twins removed. Python retains `Bar` (zero-logic payload for Strategy code, sole public API) + native bridges.

### 5.5 Presentation — Rust + Slint chart (`rust/vayren-shell`)
**Responsibility:** Snapshot → state → projection → screen. Pure view-models, viewport math, watchlist/tools/download console.
**Public API:** `MarketState` (`set_bars`, `interact`, `apply_snapshot_json`), `MarketBar`, `WatchEntry`, `MarketView`, screen bindings.
**Consumes:** backend snapshots → **Produces:** projected Slint chart.
**Forbidden:** Business logic in screens; bus/SQL handles in views.
**Invariants:** Ascending bars; pure render projections (no state/events); interactions report to Rust; canonical tokens in `ui/palette.slint` only.
**Validation:** `cargo test -p vayren-shell`.

### 5.6 Module: `05_strategy` — Strategy Platform
**Responsibility:** Strategy registry, Python-native runtime, storage, research, Lab (allowed: `core`, `market` — see §4).
**Public API:** `StrategyRegistry`, `StrategyRegistryError`, `StrategyDefinition`, `StrategyParameters`, `ParameterSpec`, `ParameterError`, `Signal`, `SignalKind`, `StrategyState`, `StrategyRuntime`, `StrategyLogic`, `BarView`, `BacktestForm`, `ResearchDataset`, `strategy_manifest`, plot contract (`PlotEvent`, `PlotType`, `MarkerType`, `PlotLifecycle`, `RenderLayer`, `PlotValidationError`, `default_layer`, `make_event_id`), events `StrategiesListed`, `StrategySelected`, `PaperTradeRequested`, `LabReset`.
**Invariants:** `class Strategy(PythonStrategy)` is the ONLY execution path (no DSL/VM/`exec`); `StrategyParameters` frozen; `StrategyRegistry` is the definition registry; `strategy.research` never imports `backtest` at runtime (variant re-execution is injected); backtest uses `compile_strategy`, never file parsing.
**Validation:** `05_strategy/strategy/tests` — note: package entry currently import-debt (see `ai_memory.md`).

### 5.7 Module: `06_backtest` — Research Engine
**Responsibility:** Runner/engine/validation/models are Rust-owned (`backtest` + `backtest_engine` + `metrics`). Python retains native bridges only.

### 5.8 Module: `07_risk` — Safety Gates
**Responsibility:** Engine/session/kill-switch/models are Rust-owned (`risk_engine` + `kill_switch`). Python retains native bridges only.

### 5.9 Module: `08_execution` — Live/Paper Execution
**Responsibility:** Session/engine/venues/ledger/reconcile/journal/runtime/planner are Rust-owned (`execution_engine` + `execution` + `order_state` + `live_readiness` + `resilience` + `sandbox_policy`). Python retains the AI-adjacent layer (`adaptive/`, `regime.py`, `ml_interfaces.py`), the `OrderState` enum + frozen market-event shapes, and native bridges (kernel-direct legality checks, no materialized tables).

### 5.10 Module: `09_broker` — Unified Broker Layer
**Responsibility:** Broker-independent coordination boundary: one error vocabulary, one capability vocabulary (bool + tri-state), three protocol faces, the ONLY registry, single selection contract, funds/health/credential models. No transport/SDK/network/secrets — adapters live in `broker/adapters/<broker>/` (stdlib only, imports nothing — enforced).
**Public API:** `ErrorCode`, `BrokerError`, `BrokerNotRegisteredError`, `UnsupportedCapabilityError`, `CredentialsNotReadyError`, `Domain`, `Environment`, `Caps`, `CapabilitySet`, `CapabilityStatus`, `HistoricalFace`, `MarketDataFace`, `TradingFace`, `BrokerPlugin`, `BrokerRecord`, `BrokerRegistry`, `default_registry`, `BrokerSelection`, `SelectionStore`, `FundsSnapshot`, `require_funds`, `CredentialRef`, `CredentialResolver`, `validate_refs`, `HealthState`, `BrokerHealth`, `BrokerIdentity`, `BrokerStatus`, `BrokerSpec`, auth contract (`AuthCapability`, `AuthField`, `ConnectionState`, `AuthErrorCode`, `AuthResult`, `BrokerAuthContract`, `mask_secret`, `describe_schema`).
**Invariants:** One registry, one selection, one capability/error vocabulary (AST-enforced); unsupported capability fails closed before transport; credentials are key-references only (never in logs/events/UI); health is connection-only (never implies LIVE readiness); `UNKNOWN`/`unsupported` funds ≠ `0.0`; network boundary Core → UBL → Adapter → Network; Zerodha history-only; no `if broker == ...` outside adapters. New venue = one adapter package + `BrokerRegistry.register(...)` + generic harness.
**Validation:** `09_broker/broker/tests` + `validate_imports.py` (SDK/network) + `validate_structure.py`.

## 6. Cross-Module Interfaces
| Interface | Owner | Consumer | Data |
|---|---|---|---|
| `market → chart` | `market` emits `DataLoaded(symbol, bars)` | chart engine consumes | `tuple[Bar]` |
| `chart → app` | `chart` emits `ChartReady(model)` | chart window consumes | `ChartModel` |
| `app → market` | `app` emits `LoadSymbol`, `TimeframeChanged`, `ListSymbols` | loaders consume | symbol/limit |
| `data → app` | `data` emits download events | side panel consumes | counts |
| `strategy → backtest` | `strategy` provides `StrategyRuntime` | backtest runner consumes | `Signal` |
| `strategy → execution` | `strategy` provides `PythonStrategy`/`Signal`/`BarView` | live session consumes | same `Signal`, no rewrite |
| `risk → execution` | `risk` provides `RiskDecision` | live session consumes | approved/denied + reasons |
| `execution → broker` | `execution` drives venue face | registered venue | `OrderPlan` → `Fill` |

> **Rule:** Consumers depend on provider capabilities and event payloads, not internal files.

## 7. Event Contracts
Authoritative list: `90_brain/event_catalog.md`. Shape for all events: frozen dataclass `Event` subclasses, exact-type dispatch, no widget/connection/callable in payload, `limit None` = full history.

## 8. Data Contracts
**`Bar`** (`market/models/bar.py`): `symbol, open, high, low, close, volume, timestamp ISO-8601` + optional `bar_size/vwap/trades/source` — frozen.
**Database (per-symbol file):** `ohlcv(candle_time TEXT PRIMARY KEY, open, high, low, close, volume)` under `<data_dir>/SYMBOL.db`.
**Timeframes** (`TIMEFRAME_LADDER`): `("1m","3m","5m","15m","30m","45m","1h","2h","4h","1D","1W")`, multiples only.
**Execution/live events** (`execution/events/`, frozen): `MarketEvent` + `Quote/Trade/Candle/OrderBook/HeartbeatEvent` (`symbol`, UTC ISO `timestamp`, `seq`); `SignalGenerated`, `RiskApproved`/`RiskDenied`, `OrderPlanned`/`Submitted`/`Acknowledged`/`Fill`/`Rejected`, `PositionUpdated`, `KillSwitchEngaged`.

## 9. Boundary & Security Rules
| Boundary | Who owns | Who may call | Who may NOT | Data crossing | Must NOT cross |
|---|---|---|---|---|---|
| `core` | platform | everyone | — | events, manifests | domain concepts |
| `market.database` | `market` | `market.repository` only | `chart`, `data`, `app` | `Bar` | raw rows, SQL handles |
| `data/provider` | `data` | Rust `download_engine` via `Provider` seam | `market`, `chart` | canonical intervals | broker interval IDs, tokens |
| `strategy` | `strategy` | `backtest`, `execution` via public surface | `chart`, `data`, `app` internals | `Signal` | `exec` strings |
| `risk` | `risk` | `execution` via gates | everyone else | `RiskRequest` | market state, broker handles |
| `execution` venues | `execution` | engine via venue faces | `strategy`, `risk`, `chart`, `backtest`, UI | `OrderPlan` | SDKs, credential values |
| `broker SDKs` | venue packages only | isolated adapters | strategy, risk, execution, chart, app, backtest | nothing | `kiteconnect` etc. outside adapters |
| Credentials | stores only | auth paths | events, logs, UI, journals | key refs | secret values |
| Live orders | `risk`+`execution` | PAPER default; LIVE needs 5 gates | default configs, unconfigured venues | `RiskDecision` | real orders without gates |

**Security invariants:** label-only credential errors; OS/file stores (`vayren:<provider>`); redacted `repr`.

## 10. Performance Contracts
No invented guarantees: claims need fresh measurement on the current tree (`cargo test` / `pytest`); record method + revision.

## 11. Contract Change Rules
Public change (export in `__init__.py`, event payload, DB schema)? NO (internal) → change freely. YES → update this file + consumers + tests/validators (+ event catalog for events) → verify with `scripts/validate_*`. Public API change ⇒ bump manifest version, update `__all__`, add covering test. Never change contract + implementation in one undocumented step.

## 12. AI Agent Rules
Read this module's contract (§5) before modifying it. Do not expand responsibility. Stay inside §4 via public APIs. Preserve invariants (§3, §5, §9). Never fabricate data, bypass `Provider`, or bypass `AiBoundary`. Workflow: `AGENTS.md` (boundaries here, workflow there).

## 13. Contract Validation
| Check | Command |
|---|---|
| Manifests | `strategy_manifest()` structural contract (import-debt; live enforcement = future rewire) |
| Events | `90_brain/event_catalog.md` + type-exact dispatch tests |
| Tests | `pytest` / `make check` (contract behavior lives here) |

If a contract is violated, validators and tests must fail — do not weaken validators to make a change pass.
