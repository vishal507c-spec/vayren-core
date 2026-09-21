# Unified Broker Layer — Architecture Audit & Contract Design (Phase 19)

> **STATUS: HISTORICAL (retired 2026-09-21, Phase 4).** The UBL this document
> designed is now implemented (`09_broker/broker/` — registry, selection,
> capabilities, faces, funds, credentials, health, adapters). Design-phase
> statements below like "implementation deliberately not done" and the
> "what actually happened: NOTHING" record describe Phase 19, not today.
> Current truth: `09_broker/broker/README.md` + code + `module_contracts.md`
> §5.10. This file is preserved verbatim as the design-evidence archive —
> read it only for *why* the UBL is shaped this way, never for current status.

**Status:** DESIGN ONLY — implementation deliberately not done (Phase-19 directive).
**Owns:** Current broker-facing architecture audit, duplication/conflict map, Unified
Broker Layer (UBL) target design, contracts, registry/capability model, selection
source-of-truth, migration plan, risks, required tests.
**Not owns:** Current runtime behavior (`architecture.md`, `module_contracts.md`);
language ownership (`ARCHITECTURE_CONSTITUTION.md`); speed instrumentation (`AGENTS.md`).
**When to read:** Before ANY Unified Broker Layer implementation task, before adding a
new broker adapter (data or execution), before touching `data/provider/*` or
`execution/broker/*` contracts.
**Related:** `module_contracts.md` §5.3 (data provider), §5.8/§5.9 (risk/execution),
`architecture.md` §3 (dependency graph), `event_catalog.md` §10.

---

## 1. Current architecture map (as inspected, file:line evidence)

Two **parallel, unconnected broker boundaries** exist today, each broker-agnostic
within its own chapter but unified nowhere:

### 1.1 Historical-data boundary — `02_data` (`data/provider/*`)

| Piece | File | Contract |
|---|---|---|
| Protocol | `data/provider/contract.py:56` | `Provider`: `available() / symbols() / fetch_candles(symbol, interval, start, end) / new_session() / renew()` |
| Errors | `contract.py:36-42` | 7 normalized codes (`AUTHENTICATION_FAILED` … `UNKNOWN_PROVIDER_ERROR`) |
| Sentinels | `contract.py:32-33` | `TOKEN_EXPIRED`, `RATE_LIMITED` (opaque control-flow objects) |
| Intervals | `contract.py:45` | `CANONICAL_INTERVALS = ("1m","5m","15m","30m","1h")` — engine never sees broker interval ids |
| Factory | `data/provider/factory.py:23-38` | `_PROVIDER_TYPES: name→class`, `register_provider(name, factory)`, `build_provider(settings)` dispatches `settings.provider` |
| Adapter | `data/provider/zerodha/adapter.py` | ZerodhaProvider — the ONLY broker-SDK-aware file (`kiteconnect`), also carries `display_name`, `credential_fields` (5), `build_credentials`, `reload_credentials` |
| Credentials | `data/provider/credentials.py`, `credentials_store.py`, `manager.py` | `CredentialField` schema per provider, `FileCredentialStore`/`WindowsCredentialStore` (`vayren:<provider>` keys), `ProviderCredentialsManager` (validate label-only/save/apply/test_connection/clear/reload) |
| Engine | `data/downloader/engine.py:44-360` | Injects `Provider` (ctor requires it, None→TypeError), consumes canonical vocabulary only; queue universe filter (`queue.py:51`), sweep chunking/renewal (`sweep.py:22`) |
| Selection source | `data/settings.py` | `DownloadSettings.provider: str = "zerodha"` (single string field) |

**Flow:** `settings.provider` → `build_provider()` → `HistoricalDownloadEngine(provider)` →
worker thread → bus events (`DownloadRequest`…`DownloadCoverage`) → data UI panel.

### 1.2 Live-execution boundary — `08_execution` (`execution/broker/*`, `execution/market_data/*`)

| Piece | File | Contract |
|---|---|---|
| Protocol | `execution/broker/adapter.py:27` | `BrokerAdapter`: `name / capabilities / connect / disconnect / health / account / positions / open_orders / place_order(plan, client_order_id) / cancel_order / modify_order / stream_events / on_market_price / reference_spread` |
| Capabilities | `adapter.py:15-24` | `BrokerCapabilities`: `orders.market`, `orders.limit`, `orders.cancel`, `orders.modify`, `account.positions`, `account.open_orders`, `stream.events` |
| Helper | `adapter.py:92` | `adapter_supports(adapter, capability)` |
| Errors | `adapter.py:75-89` | `BrokerError(msg, code)`, `NotConfiguredError` (no live adapter exists — deliberate) |
| Factory | `execution/broker/factory.py:18-57` | `_registry: name→factory`, `register_adapter`, `resolve_broker(mode, gates, adapter_name, paper_capital)` — PAPER always resolves; SANDBOX needs registration; LIVE needs registration + 5 gates, else PAPER downgrade |
| Paper | `execution/broker/paper.py` | `PaperBroker` — deterministic, capability subset (no MODIFY, no STREAMING), fill economics mirror backtest `ExecutionSimulator` |
| Sandbox | `execution/broker/sandbox.py` | `SandboxBroker` — contract-testing venue: account identity (`environment="sandbox"`), credential gate (`validate_credentials` inside `_require_usable`), scripted fills (full/partial/reject/delay), disconnect simulation; supersets Paper's capabilities |
| Live gates | `execution/broker/gates.py:28-41` | 5 named gates (BROKER_ADAPTER_READY, CREDENTIALS_READY, ACCOUNT_CONFIRMED, RISK_CONFIGURATION_VALID, EXECUTION_SAFETY_ENABLED) + `evaluate_live_gates()` fail-closed with reasons |
| Credentials | `execution/broker/credentials.py` | `BrokerCredentials` (identity + `key_refs`, **values never on objects**), `CredentialStore` protocol, `EnvCredentialStore` (`VAYREN_BROKER_*`), `validate_credentials(..., require_secrets, expected_environment)` |
| Modes | `execution/modes.py` | `ExecutionMode` (PAPER/SANDBOX/LIVE), `ModeGates` (5 flags from env, exact-`"true"` parsing), `resolve_mode` (LIVE without gates → PAPER, never silent), `LiveArm` state machine |
| Market data | `execution/market_data/provider.py:15` | `MarketDataProvider`: `name / capabilities / open(symbols, timeframe) / poll() / health / close` — normalized `MarketEvent` stream |
| Normalizer | `execution/market_data/normalizer.py` | `StreamNormalizer` — per-symbol seq gate, duplicate suppression, reorder buffer, staleness/heartbeat stats |
| Replay | `execution/market_data/replay.py:40` | `ReplayProvider(MarketDataProvider)` — deterministic tape from `Bar` rows (`bars_to_candles`) |
| Session | `execution/runtime/session.py` | `LiveSession` wires provider→normalizer→strategy→risk→planner→engine→broker; `resolve_broker` called at `session.py:263`; contract inspector checks strategy's `LIVE_BROKER_CAPABILITIES` vs broker capabilities (`inspector.py:72`, enforced `session.py:132-134`) |

**Flow:** `resolve_broker(mode, gates, adapter_name)` → `LiveSession` → poll/normalize →
signals → intents → risk → planner → `broker.place_order` → fills via `stream_events` →
portfolio/journal → reconciliation (ledger vs broker truth, mismatch `blocks_live`).

### 1.3 Composition & UI points — `00_app`

| Piece | File | What it does |
|---|---|---|
| `--paper` | `app/__init__.py:40-51, 96-100` → `app/services/paper_service.py` | Headless paper run: repository bars → `bars_to_candles` → `ReplayProvider` → `LiveSession` → `PaperBroker`; `--live` fails closed RC=2; `--check-live` prints gates |
| LIVE tab | `app/bootstrap/bootstrap.py:121-254` | `_live_state_provider()` — read-only state dict: broker placeholder `"NOT CONFIGURED"` (no adapter registered), `evaluate_live_gates(adapter=None, ...)` → gates + arm blockers; **hardcodes `environment="live"`, `key_refs=("API_KEY","API_SECRET")`** |
| Data panel | `02_data/data/ui/status_view.py` + `credentials_dialog.py` | Provider status card + `ProviderCredentialsDialog` (provider-agnostic via `ProviderCredentialsManager`) |
| Strategy Lab | `app/ui/strategy_lab_workspace.py` | Backtest UI; **no broker involvement** (research path is broker-free by design) |

### 1.4 Research path (must stay broker-free)

`06_backtest`: `BacktestRunner` → `ExecutionSimulator` (`backtest/engine/simulator.py`) —
pure replay fill math, no `BrokerAdapter` import anywhere in `05_strategy`/`06_backtest`
(validator-enforced: `backtest → execution` forbidden, `strategy → execution` forbidden).

### 1.5 Validators/tests guarding the boundaries

- `scripts/validate_imports.py`: `SDK_ALLOWLIST_PREFIXES = ("02_data/data/provider/",)` +
  `SDK_DENYLIST = {"kiteconnect"}` — broker SDKs may exist ONLY inside provider adapters;
  `DOMAIN_DEPS` forbids `strategy/backtest → execution`, `risk → non-core`.
- `scripts/validate_structure.py`: 9 domains incl. required `provider/`, `broker/` layouts.
- `08_execution/execution/tests/test_adapter_contract.py`: adapter conformance suite.
- `02_data/data/tests/test_provider.py`: provider isolation (fresh interpreter must not
  import `kiteconnect` when importing `data.downloader.engine`).

---

## 2. Duplication & conflicts found (the actual gaps)

| # | Finding | Evidence | Impact |
|---|---|---|---|
| D1 | **Two registries, two selection strings, no link.** `data.provider.factory._PROVIDER_TYPES` keyed by `settings.provider`; `execution.broker.factory._registry` keyed by `adapter_name` (only `"sandbox"` pre-registered). Nothing records "the selected broker" once. | `factory.py:23` (data) vs `factory.py:18` (execution) | UI cannot make one broker authoritative for BOTH history and trading |
| D2 | **Three capability vocabularies.** `BrokerCapabilities` (orders/account/stream), `MarketDataProvider.capabilities` (tick/quote/trade/candle-close/heartbeat), and the historical provider's *implicit* capability set (canonical intervals + symbols universe — there is no explicit capability object at all). | `adapter.py:15`, `provider.py:24`, absence in `data/provider/contract.py` | Capability discovery is per-layer; no single "what can broker X do?" answer |
| D3 | **Two credential systems.** Data: `CredentialField` schema + `WindowsCredentialStore`/`FileCredentialStore` (`vayren:<provider>`), values persisted (by design, in-app config). Execution: `BrokerCredentials` key-refs + `EnvCredentialStore`, values never persisted (fail-closed). | `data/provider/credentials_store.py` vs `execution/broker/credentials.py` | A real broker would need bridging logic; two mental models for one concept |
| D4 | **Two error vocabularies with overlapping semantics.** `ProviderError(code=...)` (7 codes) vs `BrokerError(code=...)` (free-form codes) vs `NotConfiguredError`. No shared normalized enum. | `contract.py:48`, `adapter.py:75` | Cross-layer failure handling would need per-layer translation tables |
| D5 | **Selection currently has 3 uncoordinated sources of truth:** (a) `DownloadSettings.provider` string for history; (b) `resolve_broker(adapter_name=...)` argument for trading (CLI defaults to `""`); (c) UI LIVE tab hardcodes `environment="live"` + key refs, and shows a static "NOT CONFIGURED" broker pill. | `settings.py`, `paper_service.py:395`, `bootstrap.py:192-196` | Today history = Zerodha (hardcoded default) while trading = PAPER — consistent only by accident of configuration, not by design |
| D6 | **Data-layer "broker" is really a data vendor.** `ZerodhaProvider` has no trading surface; `BrokerAdapter` has no historical surface. Both are called "provider/adapter" — naming collision that the target design must resolve explicitly. | file trees §1.1/§1.2 | Conceptual confusion: "broker" in UI could mean either |
| D7 | **`NotConfiguredError` duplicated concept.** `data.provider.factory` raises plain `ValueError` for unknown provider; `execution.broker.factory` raises `NotConfiguredError`. Same user-facing situation, different exception types. | `factory.py:35` vs `factory.py:51` | Inconsistent fail-closed reporting across layers |
| D8 | **No funds/margin surface anywhere.** `BrokerAdapter.account()` returns equity/currency; nothing exposes funds/margin detail (needed by real venues). | `adapter.py:42`, `paper.py:77`, `sandbox.py:98` | UBL Funds API must be designed in, not retrofitted |

Non-findings (checked, currently fine): no `if broker == ...` in core (greps clean);
backtest/research is broker-free (validator-enforced); no secret values in code/tests
(scan clean); Paper/Sandbox behavior is deterministic and test-pinned.

---

## 3. Target: Unified Broker Layer (UBL)

### 3.1 Shape

```
VAYREN Core (strategy · backtest · risk · execution engine · chart)   ← broker-free
    ↓  (speaks ONLY the UBL vocabulary below)
Unified Broker Layer                        [09_broker — NEW chapter]
    ├── Historical Data API   (canonical intervals/symbols — today's Provider)
    ├── Market Data API       (normalized MarketEvent stream — today's MarketDataProvider)
    ├── Account API           (identity, environment, equity)
    ├── Positions API         (net positions per symbol)
    ├── Orders API            (place/cancel/modify/open_orders)
    ├── Fills API             (fill events via stream)
    ├── Funds/Margin API      (funds detail — NEW, D8)
    ├── Connection/Health API (connect/disconnect/health + heartbeat)
    └── Trading/Execution API (orders+fills+account orchestration surface)
            ↓  implement capabilities, advertise subset
      Broker Adapter Plugin  (ONE adapter package per venue, three faces)
        ├── Broker A   (adapter A: historical? market-data? trading?)
        ├── Broker B
        └── Broker C
  + BrokerRegistry (single name→plugin map, single selection state)
  + CapabilityModel (typed, queryable: historical/market-data/trading × subcaps)
```

### 3.2 Non-negotiable design rules (from audit)

1. **Core stays broker-free.** Strategy/backtest/risk/chart never import UBL. Execution
   engine and download engine program against UBL *protocols*, not adapters.
2. **One registry, one selection.** `BrokerRegistry` is the ONLY name→plugin map. The
   selected broker is a single recorded fact (§7), not scattered settings fields.
3. **Capability-gated, fail-closed.** Every API call checks advertised capabilities;
   an unsupported capability raises `UnsupportedCapabilityError` BEFORE any transport call.
   No capability probing at call time — discovery is explicit.
4. **Paper/Sandbox preserved verbatim.** They become UBL plugins with existing
   capabilities; their tests keep passing unchanged.
5. **Historical capability is separable.** A broker may provide history but not trading
   (Zerodha today) or trading but not history. The three faces are independent.
6. **No `if broker == ...` anywhere in core.** Branching happens only inside adapters and
   via capability checks.
7. **Real SDKs stay in adapter packages only** (validator rule unchanged, extended to the
   new chapter).
8. **Fail-closed everywhere.** Unknown broker name → `BrokerNotRegisteredError`; unsupported
   capability → `UnsupportedCapabilityError`; missing credentials → `CredentialsNotReadyError`.
   PAPER downgrade semantics stay exactly as today.

---

## 4. Exact interfaces / protocols / contracts

New chapter `09_broker/broker/` (stdlib + `core` only; NO dependency on
`strategy/backtest/risk/chart/data/execution` — it sits beside them, consumed by `00_app`
composition and the two engines). Python per Constitution §2 reasoning: this is
integration/orchestration glue around AI-driven strategy workflows and stays testable in
the Python layer; per-adapter performance-critical paths can migrate to Rust later behind
the same protocols.

### 4.1 Vocabulary module — `broker/vocab.py`

```python
# Domain enums (frozen, exhaustive) — replaces ad-hoc strings across layers
class Domain(StrEnum):
    HISTORICAL_DATA = "historical_data"
    MARKET_DATA = "market_data"
    TRADING = "trading"

class Environment(StrEnum):
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"

# Normalized error codes — ONE vocabulary (merges D4; data-side 7 codes are a subset)
class ErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_CONNECTED = "NOT_CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    CREDENTIALS_NOT_READY = "CREDENTIALS_NOT_READY"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    NOT_REGISTERED = "NOT_REGISTERED"
    UNKNOWN = "UNKNOWN"

class BrokerError(RuntimeError):
    """Normalized UBL failure. code is always an ErrorCode value."""
    def __init__(self, message: str, code: ErrorCode = ErrorCode.UNKNOWN) -> None: ...

class BrokerNotRegisteredError(BrokerError): ...   # code NOT_REGISTERED
class UnsupportedCapabilityError(BrokerError): ... # code CAPABILITY_UNSUPPORTED
class CredentialsNotReadyError(BrokerError): ...   # code CREDENTIALS_NOT_READY
```

Existing `ProviderError`/`BrokerError` instances are translated to this vocabulary **at
the adapter edge** by thin shims (§9) — the two existing error types remain valid inside
their chapters during migration.

### 4.2 Capability model — `broker/capabilities.py`

```python
# Capability ids: "<domain>.<sub-capability>", all strings, centrally named.
class Caps:
    # historical_data domain
    HIST_CANDLES = "historical_data.candles"      # fetch OHLCV ranges
    HIST_SYMBOLS = "historical_data.symbols"      # symbol universe listing
    # market_data domain
    MD_CANDLE_STREAM = "market_data.candle_stream"
    MD_QUOTES = "market_data.quotes"
    MD_DEPTH = "market_data.depth"
    MD_HEARTBEAT = "market_data.heartbeat"
    # trading domain (superset of today's BrokerCapabilities)
    ORDERS_MARKET = "orders.market"
    ORDERS_LIMIT = "orders.limit"
    ORDERS_CANCEL = "orders.cancel"
    ORDERS_MODIFY = "orders.modify"
    ACCOUNT_POSITIONS = "account.positions"
    ACCOUNT_OPEN_ORDERS = "account.open_orders"
    ACCOUNT_FUNDS = "account.funds"               # NEW (D8)
    STREAM_FILLS = "stream.fills"

@dataclass(frozen=True)
class CapabilitySet:
    """Immutable, queryable capability declaration."""
    domains: tuple[Domain, ...]
    items: frozenset[str]

    def supports(self, cap: str) -> bool: ...
    def supports_domain(self, domain: Domain) -> bool: ...
    def missing(self, caps: Iterable[str]) -> tuple[str, ...]: ...
```

`BrokerCapabilities` (execution) becomes an alias/mirror of the trading-domain items;
`MarketDataProvider.capabilities` strings map 1:1 onto `MD_*` ids. Mapping tables live in
the shim (§9), so no existing caller changes.

### 4.3 The three protocol faces — `broker/faces.py`

```python
class HistoricalFace(Protocol):
    """Today's data.provider.Provider, restated in UBL vocabulary."""
    name: str
    def capabilities(self) -> CapabilitySet: ...
    def available(self) -> tuple[bool, str]: ...
    def symbols(self) -> set[str]: ...
    def fetch_candles(self, symbol: str, interval: str,
                      start: datetime, end: datetime) -> list[dict] | Sentinel: ...
    def new_session(self) -> None: ...
    def renew(self) -> None: ...

class MarketDataFace(Protocol):
    """Today's execution.market_data.provider.MarketDataProvider, restated."""
    name: str
    def capabilities(self) -> CapabilitySet: ...
    def open(self, symbols: tuple[str, ...], timeframe: str) -> None: ...
    def poll(self) -> tuple[MarketEvent, ...]: ...
    def health(self) -> tuple[bool, str]: ...
    def close(self) -> None: ...

class TradingFace(Protocol):
    """Today's BrokerAdapter surface + funds (D8)."""
    name: str
    environment: Environment
    def capabilities(self) -> CapabilitySet: ...
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def health(self) -> tuple[bool, str]: ...
    def account(self) -> dict[str, Any]: ...
    def funds(self) -> dict[str, float]: ...           # NEW: available/used/margin blocks
    def positions(self) -> list[dict[str, Any]]: ...
    def open_orders(self) -> list[dict[str, Any]]: ...
    def place_order(self, plan: OrderPlan, client_order_id: str) -> str: ...
    def cancel_order(self, broker_order_id: str) -> bool: ...
    def modify_order(self, broker_order_id: str,
                     quantity: float | None, price: float | None) -> bool: ...
    def stream_events(self) -> tuple[dict[str, Any], ...]: ...

class BrokerPlugin(Protocol):
    """One venue = one plugin object exposing any subset of the three faces."""
    name: str
    display_name: str
    def faces(self) -> tuple[Domain, ...]: ...         # which domains it serves
    def face(self, domain: Domain) -> object: ...      # the face object; raises
                                                       # UnsupportedCapabilityError when absent
    def capability_set(self) -> CapabilitySet: ...     # union across faces
    def credential_schema(self) -> tuple[CredentialField, ...]: ...
```

Sentinels (`TOKEN_EXPIRED`/`RATE_LIMITED`) are re-exported unchanged — the historical
engine's control flow depends on their identity (`is` checks), so they must remain the
same objects.

### 4.4 Registry & selection — `broker/registry.py`, `broker/selection.py`

```python
@dataclass(frozen=True)
class BrokerRecord:
    name: str                      # registry key: "paper", "sandbox", "zerodha", ...
    display_name: str
    plugin: BrokerPlugin
    capabilities: CapabilitySet
    credential_schema: tuple[CredentialField, ...]

class BrokerRegistry:
    """The ONLY name→plugin map in VAYREN. Replaces both existing registries
    (delegation shims keep old call sites working during migration)."""
    def register(self, record: BrokerRecord) -> None: ...      # duplicate name → BrokerError
    def unregister(self, name: str) -> None: ...
    def get(self, name: str) -> BrokerRecord: ...              # unknown → BrokerNotRegisteredError
    def list(self) -> tuple[BrokerRecord, ...]: ...
    def find_with(self, cap: str) -> tuple[BrokerRecord, ...]: ...
    def find_with_domain(self, domain: Domain) -> tuple[BrokerRecord, ...]: ...

@dataclass(frozen=True)
class BrokerSelection:
    """The single recorded fact: which broker is authoritative, for what."""
    name: str
    environment: Environment
    selected_at: str            # ISO timestamp
    reason: str                 # "user-selected", "default-paper", ...
    def with_capabilities(self, caps: CapabilitySet) -> bool: ...

class SelectionStore(Protocol):
    """Persistence for the selection (session-scoped file or workspace state)."""
    def load(self) -> BrokerSelection | None: ...
    def save(self, selection: BrokerSelection) -> None: ...
    def clear(self) -> None: ...
```

**Authoritative-broker rule (the requirement):** for a session, exactly one
`BrokerSelection` exists. Resolution rules, all fail-closed:

| Requested surface | Rule |
|---|---|
| Historical download | Selected broker's `historical_data` face; if it lacks the domain → **fail-closed** with explicit reason; user may explicitly pick an alternate data broker, which becomes a recorded override (visible in UI), never a silent fallback |
| Live/sandbox/paper trading | Selected broker's `trading` face; missing domain → PAPER (existing downgrade semantics, with the missing-capability reason added to the downgrade notes) |
| Market data (live) | Selected broker's `market_data` face; missing → `ReplayProvider`/offline path with recorded reason (current behavior for paper) |
| UI display | LIVE tab broker pill, data panel status, and CLI all render the SAME `BrokerSelection` — one source of truth |

Mode safety (PAPER default, 5 live gates, arming) is **orthogonal and unchanged** — UBL
never bypasses `resolve_mode`/`evaluate_live_gates`; gates keep operating on the
resolved adapter exactly as today.

### 4.5 Credentials — `broker/credentials.py`

Single schema layer: adapters declare `CredentialField`s (today's data-layer concept,
D3 resolved). Resolution order stays: **in-app store → env → not-configured** for
interactive flows; execution's key-ref model becomes the runtime view over the same
store (`BrokerCredentials.key_refs` populated from the schema). Values never enter
events/logs/UI; `WindowsCredentialStore`/`FileCredentialStore`/`EnvCredentialStore`
implement one `CredentialStore` protocol (execution's protocol shape wins — it is the
stricter one — with the data store adapted underneath).

---

## 5. Broker registry / plugin design

- **Plugin packaging:** one adapter package per venue, e.g. `09_broker/broker/adapters/zerodha/`
  (would absorb `data/provider/zerodha/` at migration) — SDK imports remain inside that
  package only; `validate_imports.py` SDK-allowlist extended to `09_broker/broker/adapters/`.
- **Registration:** bootstrap-only (mirrors event-subscription rule): `00_app` composition
  calls `registry.register(...)` for each installed plugin at startup. No scanning, no
  reflection, no plugin discovery magic (architecture rule: explicit registration).
- **Built-ins:** `paper` and `sandbox` register as plugins whose `TradingFace` wraps the
  existing `PaperBroker`/`SandboxBroker` objects (thin delegation, zero logic change) —
  their capability sets mirror today's tuples exactly.
- **`zerodha` registers with `faces=(HISTORICAL_DATA,)` only** — honest advertisement:
  no trading face exists, so selecting it for trading fails closed with
  "broker does not provide trading capability", which is exactly the REAL_BROKER_UNSPECIFIED
  verdict stated in UBL vocabulary.

## 6. Capability model (summary)

`CapabilitySet` is the single queryable truth (`supports`, `supports_domain`, `missing`).
Three consistency rules, test-pinned:
1. A face object must exist for every `Domain` advertised in `faces()`.
2. Trading-face capability strings ⊇ whatever the engine's contract inspector requires
   (`LIVE_BROKER_CAPABILITIES` checks unchanged — they now query the unified set).
3. `capability_set()` equals the union of per-face sets (no phantom capabilities).

## 7. Historical + live data relationship

- Same `BrokerSelection` covers both; **capability presence decides**, not name matching.
- Historical and market-data faces are independent: a venue may serve `historical_data`
  only (Zerodha today), `market_data`+`trading` only (typical live venue), or all three.
- The download engine and `LiveSession` both resolve through the registry; neither learns
  the broker's identity (name strings appear only in logs/UI, never in engine logic).
- Cross-broker data honesty: candles downloaded via broker A carry provenance
  (`source` field already exists on `Bar`), so a future mixed setup is auditable.

## 8. Selected-broker source-of-truth design

One `SelectionStore` (workspace-scoped JSON under the existing data dir, no secrets) +
one in-memory `BrokerSelection` per session, set via an explicit `select_broker(name,
environment, reason)` API (CLI flag `--broker NAME`, and the UI data panel becomes the
same setter). Every consumer (download engine bootstrap, `resolve_broker` call site,
LIVE tab state provider, status panel) reads THE selection object. There is no second
write path and no default that silently diverges: with no selection, history fails
closed ("no broker selected") instead of falling back to a hardcoded string — the
current `"zerodha"` default becomes an explicit, visible, first-run selection.

## 9. Migration plan (incremental, behavior-preserving)

| Step | Change | Risk | Behavior |
|---|---|---|---|
| M1 | Create `09_broker` with `vocab.py`, `capabilities.py` + tests. Nothing imports it yet. | None | Unchanged |
| M2 | `faces.py` + `registry.py` + `selection.py`; register paper/sandbox wrappers; `resolve_broker` unchanged. | None | Unchanged |
| M3 | **Shims:** `data.provider.factory.build_provider` delegates to `BrokerRegistry`; `execution.broker.factory.resolve_broker` resolves via registry. Old functions keep signatures; `ProviderError`→`ErrorCode` translation at shim edge. | Low | Unchanged (same instances returned) |
| M4 | Selection store + `--broker` flag + UI setter; data panel and LIVE tab read it. `DownloadSettings.provider` becomes derived (kept for backward compat). | Medium | First-run selection prompt replaces silent default |
| M5 | Zerodha adapter package moves under `09_broker/broker/adapters/zerodha/` (re-export from old path during one release). SDK allowlist updated. | Medium | Unchanged |
| M6 | Funds surface (D8): `TradingFace.funds()` + Paper/Sandbox deterministic implementations. | Low | Additive |
| M7 | Retire shims; engines import UBL directly; old registries deleted. | Medium | Unchanged |

Each step ships independently behind the full gate; M3 is the semantic pivot and gets the
most tests. M4 is the only user-visible change and is a design decision to confirm.

## 10. Files that would change in the implementation phase

**New:** `09_broker/broker/{__init__,vocab,capabilities,faces,registry,selection,credentials}.py`,
`09_broker/broker/adapters/{__init__,paper,sandbox,zerodha/...}.py`,
`09_broker/broker/tests/…`
**Modified (M3–M7):** `02_data/data/provider/factory.py` (delegation shim),
`08_execution/execution/broker/factory.py` (delegation shim),
`00_app/app/bootstrap/bootstrap.py` (registry wiring + selection), `00_app/app/__init__.py`
(`--broker` flag), `00_app/app/services/paper_service.py` (selection-aware resolve),
`00_app/app/services/slint_live_host.py` (selection display, native Slint live viewport), `02_data/data/ui/status_view.py`
(selection display), `02_data/data/settings.py` (derived provider field),
`pyproject.toml` (testpaths/pyright/coverage/wheel: `09_broker`), `scripts/validate_imports.py`
(chapter + SDK allowlist), `scripts/validate_structure.py` (new domain),
`scripts/run_tests.py` (partition), `90_brain/{module_contracts,event_catalog,architecture}.md`.
**Untouched by design:** everything in `01_core`, `04_chart`, `05_strategy`, `06_backtest`,
`07_risk`; `PaperBroker`/`SandboxBroker` internals; strategy/risk/backtest data flow.

## 11. Risks & backward-compatibility

| Risk | Mitigation |
|---|---|
| Sentinel identity breakage (engine uses `is` checks) | Sentinels re-exported as the same objects; test pins `TOKEN_EXPIRED is shim_sentinel` |
| Two registries temporarily alive (M3) | Shims are the ONLY delegation point; test asserts registry and shim resolve to the same instance |
| Selection change surprises existing users (M4) | Explicit design decision; `--broker` flag + recorded reason + docs; default remains PAPER for trading |
| SDK-allowlist regression (M5) | Validator updated in the same commit as the move; isolation test extended to `09_broker` |
| Capability drift between old tuples and new set | Mapping tables test-pinned both directions (old string ∈ new set ↔ vice versa) |
| Constitution friction (new chapter number) | `09_broker` sits after `08_execution` in the dependency order; consumes `core` only; consumed by `00_app`; no backward imports |
| Scope creep into "real broker" | Phase-19 verdict stands: no venue exists; UBL adds no adapter beyond paper/sandbox/zerodha-history |

## 12. Tests required for the implementation phase

1. `test_vocab.py` — error-code normalization: every legacy `ProviderError`/`BrokerError`
   code maps to an `ErrorCode`; unknown codes → `UNKNOWN` (never invented).
2. `test_capabilities.py` — `CapabilitySet` algebra (supports/missing/union); Paper and
   Sandbox sets byte-equal today's tuples; `zerodha` advertises `historical_data` only.
3. `test_registry.py` — register/duplicate-name rejection/unknown-name fail-closed/
   `find_with_domain`; registration order independence.
4. `test_selection.py` — single source of truth: selection read by all consumers;
   missing selection → fail-closed history + PAPER trading with recorded reason;
   persistence roundtrip; override visibility.
5. `test_faces.py` — Paper/Sandbox wrappers pass the existing adapter-conformance suite
   unchanged; `face()` raises `UnsupportedCapabilityError` for absent domains.
6. `test_shims.py` (M3) — `build_provider` and `resolve_broker` return the same instances
   as before; sentinel identity; error translation table.
7. Existing suites stay green unchanged (paper E2E, sandbox E2E, parity, gates, provider
   isolation, gate-coverage).

---

## 13. Phase-19 execution record (what actually happened)

- **Implemented:** NOTHING in product code. Zero product behavior change.
  The only writes were this document and the speed/forensics marker streams.
- **Design/audit only:** sections 1–12 above, grounded in the cited files.
- **Decision needed before implementation:** M4's selection UX (explicit first-run
  selection vs keeping the `zerodha` history default as a recorded default-selection).
