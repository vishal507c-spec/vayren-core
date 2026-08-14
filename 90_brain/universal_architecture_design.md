# Universal VAYREN Architecture — Design (v0.1 Draft for Review)

> Status: **DRAFT — approval pending. Production code untouched.**
> Date: 2026-08-14
> This document is the deliverable for the architecture discovery phase (documents A–J).
> No implementation code was written. No production file was modified.

---

## A. CURRENT SYSTEM MAP

### A.1 Repository layout (active)

```
00_app/app/     entry, Bootstrap (wiring), AppLifecycle
01_core/core/   EventBus, Event, AppStarted, logger, Registry
02_market/market/  Bar, SymbolQuote, 2 DB adapters, 2 repositories, 4 loaders, timeframe, 8 events
03_chart/chart/ ChartModel, ChartEngine, 5 renderers, 6 widgets, ChartWindow, theme, 2 events
90_brain/       9 permanent knowledge docs
99_archive/     13 retired domains (foundation→knowledge) — not importable
scripts/        seed_sample_db.py, validate_structure.py, validate_imports.py
.github/        CI (ruff + pytest on ubuntu), issue/PR templates
```

### A.2 Major components

| Component | Kaam | Lines-ish scale |
|---|---|---|
| `EventBus` | Sync publish/subscribe, exact-type dispatch, fail-safe handler logging | tiny |
| `Registry[T]` | String-named service registry | tiny |
| `SqliteCandleDatabase` / `OhlcvCandleDatabase` | SQLite read adapters (sample + real Zerodha schema) | small |
| `CandleRepository` / `SymbolRepository` | Row→Bar mapping, timeframe detection/aggregation, batch quotes | medium |
| 4 loaders | Event→repository→event glue | small |
| `ChartEngine` | DataLoaded → ChartModel → ChartReady | small |
| 5 renderers + 6 widgets + ChartWindow | Stateless QPainter rendering, pixmap cache, dark theme | medium |

### A.3 Existing contracts (implicit)

- Public API = `__init__.py` exports, documented in prose in `90_brain/module_contracts.md`.
- No machine-readable contract, no versioning, no behavioral declarations.

### A.4 Events — 11 frozen dataclasses (exact-type dispatch, sync)

| Event | Direction |
|---|---|
| `AppStarted` | fact (core) |
| `ListSymbols`, `LoadSymbol`, `ListTimeframes`, `TimeframeChanged` | requests (market) |
| `SymbolsListed`, `QuotesLoaded`, `DataLoaded`, `TimeframesListed` | results (market) |
| `ChartReady`, `WindowRendered` | results (chart) |

### A.5 Registries

- `Registry[Any]` — 8 services registered in `Bootstrap._register_services` (string names).
- No capability registry, no event registry, no manifest registry.

### A.6 Dependencies

- Runtime: **only PySide6**. (httpx appears only inside `99_archive`.)
- Module graph enforced by AST validator: `app → {core, market, chart}`, `market → core`, `chart → {core, market}`, `core → {}`.
- 12 subscriptions, all in `bootstrap.py` (the single wiring point).

### A.7 Storage

- Per-stock SQLite files (`D:\ZerodhaTradingData`, 527 files), table `ohlcv(candle_time TEXT PK, open/high/low/close REAL, volume INTEGER)`.
- **Read-only platform today.** No write path, no download, no repair.

### A.8 Configuration

- argparse (`--data-dir`, `--limit`, `--log-level`) + env `VAYREN_DATA_DIR`. No config file, no config object.

### A.9 Runtime

- `App.main` → QApplication → `Bootstrap.start()` → window.show + `AppStarted` → full sync chain → Qt event loop.
- Bus is synchronous; the whole startup chain completes before the Qt loop starts.

### A.10 Testing architecture

- 234 tests, pytest (testpaths per module), ruff, pyright (0 errors), 2 AST validators, CI on GitHub (ruff + pytest).
- Validators hardcode the 4-domain map in Python dicts.

### A.11 Versioning

- `pyproject.toml` = 1.2.0; git tags reach **v1.4.0** — **drift exists today.**

---

## B. PROBLEM MAP

| # | Problem | Evidence | Severity |
|---|---|---|---|
| 1 | **Contracts are prose, not machine-readable.** AI/agents can't discover what the system can do without reading source + docs | `module_contracts.md` + `__init__.py` exports only | HIGH |
| 2 | **Bootstrap is a growing choke point.** Every new capability = new imports, new services, new subscriptions hand-written in one file | 12 subscriptions / 8 services / 4 loaders already | HIGH |
| 3 | **Registry is name-based, not capability-based.** Consumers must know string names; no "find something that can fetch candles" | `Registry.get("symbol_repository")` | HIGH |
| 4 | **Adding a module requires editing many files** (validators, architecture.md, event_catalog.md, module_contracts.md, roadmap.md, README, pyproject, CI) | validator dicts hardcode domains | MEDIUM |
| 5 | **No capability/component graph** — no change-impact answer ("if I change Bar, what breaks?") | nothing to compute it from | HIGH |
| 6 | **No system model** — AI must re-read the tree every session | AGENTS.md is prose | HIGH |
| 7 | **No health/metrics/performance data** — no way to identify bottlenecks as architectural data | dev_log has one-off profiles only | MEDIUM |
| 8 | **No invariant enforcement** (OHLC relations, timestamp monotonicity, contract compatibility) | DB PK is the only guard | MEDIUM |
| 9 | **No workflow concept** — fixed chains hardcoded in bootstrap; "new feature = new engine" is the only path | event flow is linear | MEDIUM |
| 10 | **No versioning at contract level** — changing `Bar`/`DataLoaded` silently breaks consumers | no compatibility metadata | MEDIUM |
| 11 | **Knowledge duplication** — same fact lives in ai_memory + development_log + module_contracts + event_catalog; drift already visible | pyproject 1.2.0 vs git tag v1.4.0 | MEDIUM |
| 12 | **Archive is a graveyard without lesson extraction** — old registries/services patterns retired wholesale; engineering memory dirs are empty | `13_knowledge` empty `__init__.py` files | LOW |
| 13 | **Startup is fully synchronous** — any future slow provider (download, live feed) will block the UI chain | sync bus | MEDIUM |

### B.1 Important factual correction to the brief

> The brief assumes "a substantial historical data download system" already exists (auth, queue, retries, gap detection, checkpointing, parallel download, heartbeat…).
> **It does not exist in the active repository.** The only remnants are three minimal HTTP adapters in `99_archive` (IEX, Polygon, CSV import) with no retries, no queue, no gap detection, no checkpointing, no auth flow.
> Consequence: nothing to protect by wrapping — but the architecture must make room for it as a **future component** (`data.acquire`), so it slots in without touching existing code.

### B.2 Strong existing designs — KEEP (do not rewrite)

- EventBus — sync, exact-type, fail-safe. Extend with metadata only, never replace.
- Frozen dataclass models/events — keep as-is.
- Layered market (database → repository → loader) — keep.
- Stateless renderers + static pixmap cache — keep.
- Single wiring point principle — keep, evolve from hand-written to declarative.
- AST import validator — keep, make table-driven.
- Session-anchored timeframe aggregation — keep.
- `limit: int | None` full-history semantics — keep.
- Test discipline (234 tests) — keep.

---

## C. UNIVERSAL PRIMITIVES (minimum set)

Design rule: *minimal contracts, maximum composability.* Each primitive exists only because the system needs it for a real reason.

| # | Primitive | WHY it exists | WHAT problem it solves | WHAT it must NOT do | WHO uses it |
|---|---|---|---|---|---|
| 1 | **Component** | The unit of everything (downloader, chart, broker, AI agent). Replaces "module = chapter folder" as the architectural unit | Unifies all future capabilities into one model | Must not know kernel internals; must not contain UI/SQL mixing | Kernel, composition root, AI |
| 2 | **Capability** | "What can this component do?" — dot-id (`data.query.candles`) independent of names | Enables capability-oriented discovery; one capability → many implementations | Must not be a class/engine; no logic of its own | Runtime, workflows, AI planner |
| 3 | **Contract** | Typed interface + behavioral rules (CAN/MUST/MUST NOT/GUARANTEES/FAILURE MODES) + version | Replaces prose module_contracts with machine-checkable declarations | Must not contain implementation | Validator, change-impact, AI |
| 4 | **Command / Event** | Already exists (requests = commands, facts = events). Add registration + schema so they are discoverable | Keeps the working bus; adds metadata | Payload stays data-only (no connections/widgets) | Everyone, via bus |
| 5 | **Data model** | Already exists (`Bar`, `SymbolQuote` — frozen dataclasses). No new abstraction layer | Types stay concrete and typed | Must not become a generic `Dict` layer | All components |
| 6 | **State** | Declared state footprint of a component | Health, replay, checkpointing, restart safety later | Must not leak into contracts | Kernel, monitoring |
| 7 | **Workflow** | Ordered composition of existing capabilities (data→transform→decision→risk→action) | "New feature = new workflow", not "new giant engine" | Must not execute domain logic itself | Intent layer, AI planner |
| 8 | **Resource** | Declared external dependencies (SDK, network, files, threads) | Dependency isolation; provider SDKs never contaminate core | Must not be auto-provisioned | Kernel, policy |
| 9 | **Policy** | Declarative guardrails (limits, permissions) enforced by kernel, not by each component | Risk limits cannot be bypassed; AI plans checked against policy | Must not contain business logic | Kernel, AI boundary |
| 10 | **Health** | Self-reported health + lightweight metrics (latency, error rate, queue) | Bottleneck detection as architectural data | Must not add telemetry everywhere — opt-in per component | Kernel, monitoring, AI |
| 11 | **Manifest (Component DNA)** | Machine-readable self-description: identity, version, capabilities, contracts, deps, events, resources, health, failure modes, performance | The source of the system model; AI reads VAYREN through this | Must not over-engineer format (Python dataclass first, JSON export later) | Kernel, system model, AI |

**Kernel must stay domain-agnostic**: `core` never knows "symbol", "candle", "chart". Domain concepts live in chapters. Kernel knows only the 11 primitives.

---

## D. TARGET ARCHITECTURE

### D.1 Layers (numbered chapters stay — numbering = startup order, unchanged)

```
00_app   app    — entry + composition root (declarative, manifest-driven)
01_core  core   — KERNEL: bus, contracts, registries, system model, capability graph,
                  policy, health, lifecycle
02_market market — data capability components (storage, query, aggregation, quotes)
03_chart chart  — presentation capability components (render, model, window)
04_…14_ future  — future capability components (same contract, same entry point)
90_brain        — knowledge + regenerated system-model snapshot
scripts         — validators (table-driven) + describe_system.py
```

### D.2 Kernel structure (01_core additions — all additive)

```
core/
  event_bus/      (existing)
  events/         (existing)
  logger/         (existing)
  registry/       (existing Registry + NEW CapabilityRegistry, EventCatalog)
  contracts/      NEW — Component, Capability, Contract, Workflow, Policy, Health, Manifest
  system/         NEW — SystemModel builder, CapabilityGraph, DependencyGraph, change-impact
  config/         NEW — minimal ConfigSource (args + env + optional file, precedence fixed)
  runtime/        NEW — CompositionRoot: registers components, validates, wires, starts
```

### D.3 Component registration pattern (replaces hand-wiring)

Each chapter exposes one declarative entry (e.g. `market/components.py`) that builds its `ComponentManifest` list. `CompositionRoot`:

```
read all manifests (explicit list in app/ — single place, unchanged principle)
→ register capabilities into CapabilityRegistry
→ build SystemModel (components, capabilities, contracts, deps, events, workflows)
→ validate (contracts + invariants + policy)
→ wire events (existing bus.subscribe calls, but generated from manifest declarations)
→ start
```

### D.4 What stays identical

- EventBus semantics (sync, exact-type, fail-safe).
- All models, events, repositories, loaders, renderers, widgets.
- Layer rules (SQL only in database, painting only in renderer, subscriptions only in composition root).
- Public `__init__.py` contract exports.
- `make check` gate (extended, never weakened).

### D.5 What changes

| Jagah | Before | After |
|---|---|---|
| Wiring | hand-written imports + subscribe calls | manifest-driven; behavior identical, tests pin behavior |
| Registry | string names only | + capability lookup (`data.query.candles` → providers) |
| Validators | hardcoded dicts | table-driven (single source of truth for domain map) |
| New module | edit ~8 files | create chapter + manifest + one validator-table row |

---

## E. COMPONENT CONTRACT (what every component must follow)

1. **Declare a manifest.** `ComponentManifest` dataclass: `id`, `version`, `type`, `capabilities: list[CapabilityDecl]`, `contracts: list[ContractDecl]`, `dependencies` (hard/optional), `resources`, `events_consumed`, `events_produced`, `state`, `health: callable | None`, `performance_hints`.
2. **Provide capabilities** as typed callables/entry points registered by capability id. Pure capabilities → direct call; side-effecting capabilities → event.
3. **Communicate only via bus/contracts** — existing cross-module import rules unchanged (imports only public API, numbering direction).
4. **Declare inputs/outputs typed** (frozen dataclasses / primitives).
5. **Follow layer rules** — SQL in database/, painting in renderer/, business logic never in UI.
6. **Behavioral contract declaration** where invariants matter (e.g. data provider: "MUST NOT silently overwrite valid data").
7. **Version independently** (semver per component; contract compatibility declared).
8. **Health optional but encouraged** for long-running/service components.

---

## F. CAPABILITY MODEL

### F.1 Naming

```
<domain>.<verb>[.<object>]   e.g.  data.query.candles, chart.render, storage.read.batch
```

### F.2 Registry semantics

```
CapabilityRegistry:
  register(capability_id, component_id, implementation)
  providers(capability_id) -> list[Provider]      # one capability, many implementations
  matching(prefix)         -> list[CapabilityId]  # glob discovery "data.*"
```

### F.3 Discovery & composition

- `CompositionRoot` builds the graph at startup; lookups are dict-based (zero runtime overhead).
- **Workflow** = declarative DAG: `[step(capability, inputs_from, outputs_to)]`. Example:

```
data.query.candles → indicator.calculate → strategy.generate_signal
        → risk.evaluate → backtest.simulate → result
```

- Workflows are first-class registered objects (versioned), not code glued in bootstrap.
- Capability questions the graph answers: who provides X? what does component Y depend on? what breaks if contract Z changes? (change-impact = BFS over dependency+event+workflow edges, computed from SystemModel.)

---

## G. SYSTEM MODEL (how VAYREN describes itself)

1. **Source of truth:** manifests registered at startup.
2. **Output:** `SystemModel` object + serialized snapshot (`vayren --describe` / `scripts/describe_system.py` → `vayren_system.json` + regenerated `90_brain/system_model.md`).
3. **Contents:** components (identity/version/type), capabilities, contracts, dependency edges (hard/optional), event graph (who publishes, who consumes), workflows, health/status.
4. **Purpose:** AI reads the system model instead of the source tree; change-impact and capability questions are answered from it; it is the architectural map that never goes stale because it is generated, not hand-maintained.
5. **No hand-maintained duplication** — this replaces the prose "where is what" sections; prose docs remain for rationale, not for inventory.

---

## H. AI BOUNDARY

### H.1 What AI CAN do

- Read the system model and brain docs; answer capability/dependency/impact questions.
- Propose plans: new components, new workflows, changes — as **plans**, never as live edits.
- Run experiments in sandbox/benchmark harness; write to engineering memory.

### H.2 What AI CANNOT do (kernel-enforced)

| Guard | Rule |
|---|---|
| Plan → deploy | Plan must pass: contract validation → policy check → sandbox → tests → benchmark → approval |
| High-risk domains (execution, risk, capital, destructive DB, security) | Explicit human approval required; stricter policy class |
| Runtime authority | Deterministic runtime is authoritative; AI failure = no-op, VAYREN keeps running |
| Core dependency | AI is optional; `vayren-core` runs with zero AI dependencies (no new required deps) |

### H.3 Engineering memory (future)

JSONL/DB record: `problem → hypothesis → change → experiment → benchmark → result → decision → reason` — machine-readable so AI reuses knowledge instead of rediscovering it.

---

## I. MIGRATION PLAN (every step leaves VAYREN runnable)

| Step | Kaam | Risk | Churn |
|---|---|---|---|
| 1 | Kernel primitives as **pure dataclasses** in `core/contracts/` + tests. Zero behavior change | none | tiny (additive) |
| 2 | Table-driven validators (single source of truth for chapter/domain map) | none | small |
| 3 | Manifests for the 4 existing modules (declare what exists — factual only, no refactor) | none | small |
| 4 | SystemModel builder + `vayren --describe` + snapshot generator | none | small |
| 5 | CapabilityRegistry; register existing services as capabilities (wrappers over existing code) | low | small |
| 6 | CompositionRoot replaces hand-wiring (behavior pinned by existing 234 tests) | low | medium |
| 7 | Minimal ConfigSource (args → env → file precedence) | low | small |
| 8 | Lightweight health/metrics hooks (bus counters, opt-in `health()`) | low | small |
| 9 | Workflow engine (declarative); first workflow = existing "load symbol → chart" expressed declaratively | medium | medium |
| 10 | **PoC** (see J) — add one new capability end-to-end without touching existing modules | medium | small |
| 11 | Engineering memory store (experiments JSONL) | low | small |

Order rationale: steps 1–4 = low risk, high architectural value, minimal churn; 5–6 = the actual kernel adoption; 9–10 = proof that "new requirement → create only what is missing" works; 11 = AI-readiness.

---

## J. PROOF-OF-CONCEPT PLAN (smallest experiment)

**Components used:** `01_core` kernel (registry + system model + bus), `02_market` (data capability), `03_chart` (render capability).

**Steps:**

1. Declare manifests for market + chart (factual).
2. CompositionRoot registers: `data.query.candles` (market), `chart.render` (chart).
3. Assert kernel answers: "which component provides data.query.candles?" → market; "what does chart depend on?" → market, core.
4. Compose workflow `query → render` using the **existing** `LoadSymbol → DataLoaded → ChartReady` event path (workflow as declarative description of the existing chain).
5. `vayren --describe` produces correct system model.
6. **The decisive test:** add a tiny new capability `data.summary` (e.g. OHLC stats for a symbol) implemented **only** in market, registered by manifest — with **zero changes** to chart, core, or bootstrap wiring. This proves "create only what is missing."

**Success criteria:**

- All 234 existing tests still green (no behavior change).
- Startup overhead of discovery + graph build measured and documented (target: negligible, <5 ms — registration is dict insertion; runtime dispatch unchanged).
- Benchmark table for: startup, discovery, capability lookup, event dispatch, data access — compared before/after.

**Redesign trigger:** if PoC fails (e.g. manifest-driven wiring can't reproduce current behavior byte-for-byte), redesign kernel BEFORE migrating the whole system.

---

## K. SUCCESS CRITERIA CHECK (mapped to brief §29)

| Criterion | How the design meets it |
|---|---|
| New component → no unrelated changes | Manifest + registration; wiring generated |
| New provider → no core change | CapabilityRegistry multi-implementation |
| Replace storage → no business-logic change | Storage behind `storage.*` capability contract |
| Replace implementation → consumers unaffected | Contract compatibility + versioning |
| New workflow reuses capabilities | Workflow = DAG of capabilities |
| AI understands architecture without reading tree | Generated SystemModel + `--describe` |
| AI proposes without controlling production | Plan → validation → sandbox → approval (H) |
| Existing systems stable | Migration steps 1–4 are purely additive; 234 tests pin behavior |
| Runtime fast | No abstraction at dispatch: registries are dicts, bus unchanged, no reflection |
| System evolves without collapse | Domain-agnostic kernel primitives |

## L. OPEN QUESTIONS (review decisions needed)

1. Manifest format: Python dataclass only (initial) vs JSON file next to chapter? (Recommendation: dataclass first, JSON export later.)
2. Capability invocation: allow direct calls for pure functions, or strictly events-only? (Recommendation: events for side effects, direct for pure — documented in manifest.)
3. Where does the downloader land when built: `04_` chapter as `data.acquire` capability, or a new chapter with multiple data components? (Recommendation: dedicated chapter, capability id `data.acquire.*`.)
4. Validator table: Python module vs TOML/YAML file? (Recommendation: Python module — no new dependency.)
5. Is `vayren --describe` output location fixed to `90_brain/system_model.md`?

---

*End of design draft. Awaiting review and approval before any implementation.*