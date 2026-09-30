# Architecture — VAYREN Current System

**Owns:** Module map, layers, dependency graph, event flow, runtime. **Not owns:** Language ownership → `AI_ENTRY.md` §1 (machine: `ownership_policy.json`); detailed boundaries/events/contracts/state → `module_contracts.md`, `event_catalog.md`, `ai_memory.md`.
**When to read:** ALWAYS first, before any code change. **Related:** `AI_ENTRY.md` §1 (language map), `docs/` (contracts/state). **Status:** Authoritative. Current system only — not a diary.

## 1. What is VAYREN?

Native trading platform: Rust + Slint shell (`crates/vayren-shell`) served by a headless Python backend (`src/app/headless.py`) over newline-delimited JSON. Snapshot → Rust state → Slint projection. No business logic in UI, no UI in business logic.

## 2. Canonical Repository Tree

```text
src/         → Python product domains (app, core, data, market, strategy, backtest, risk, execution, broker)
crates/      → Rust workspace crates (vayren-core, vayren-domain, vayren-shell, view crates)
tools/       → Developer, build, test, and validation tooling
docs/        → Architecture, contracts, design knowledge, AI memory, governance documentation
design/      → UI design specs, UX flows, mocks
tests/       → Shared external test resources (if any)
config/      → Configuration files
.github/     → CI workflows & repository configuration
```

### Current Domain Modules

| Directory | Package | Owns | Depends on |
|---|---|---|---|
| `src/app` | `app` | Composition (headless backend + services) | all domains (partly unwired — see `ai_memory.md`) |
| `src/core` | `core` | Event marker, AI guardrails, native loader | stdlib only |
| `src/data` | `data` | Provider SDK boundary, settings, bridge | `core`, `broker` |
| `src/market` | `market` | Bar vocabulary + native bridges | `core` |
| `src/strategy` | `strategy` | Registry, runtime, research, lab | `core`, `market` |
| `src/backtest` | `backtest` | Native bridges only (Rust owns engine) | — (via FFI) |
| `src/risk` | `risk` | Native bridges only (Rust owns engine) | — (via FFI) |
| `src/execution` | `execution` | AI-adjacent logic + bridges (Rust owns core) | — (via FFI) |
| `src/broker` | `broker` | Registry, selection, vocab (UBL) | stdlib only |
| `crates/` | `vayren-core` / `vayren-shell` | Kernels (std-only) + Slint shell | std only / Slint |

## 3. How Modules Are Connected

Allowed graph (machine truth: `DOMAIN_DEPS` in `tools/validate_imports.py`; qualifiers: `module_contracts.md` §4):

```text
app → all domains (composition root) · core → none · broker → none
data → core, broker · market → core · strategy → core, market
backtest → core, market, strategy · risk → core
execution → core, market, strategy, risk, broker
```

| Dependency | Allowed? | Reason |
|---|---|---|
| `* → app` | ❌ | No backward import |
| `strategy/backtest → execution/risk` | ❌ | Research/strategies never reach live machinery |
| `execution → backtest/data` | ❌ | Live layer independent of research/download |
| `risk → non-core` | ❌ | Gates judge snapshots; read nothing, call nothing |
| `data → market/chart` | ❌ | Write path isolated from read/presentation |

**INVARIANT:** Cross-module imports use only `module/__init__.py` public surface. Internal paths, relative cross-module imports (`..market`), and `from x import *` are forbidden.

## 4. Layers & Runtime

### 4.1 Per-domain pipeline

```text
store (per-symbol SQLite, Rust-owned) → bridge (JSON snapshot over stdio)
  → state (Rust view-model: MarketState, watchlist, viewport)
  → project (pure state → Slint render view) → screen (chart, tools rail, status)
```

- One layer = one responsibility (skipping forbidden). The store never emits events; screens never touch bus/SQL.

### 4.2 Core internals

Rust owns bus/registry/contracts/system (no Python twins). Python retains `core/ai/` (pure observation, no LLM/mutation) under fail-closed `AiBoundary` (deploy = recorded decision only).

### 4.3 Runtime composition (single root)

```text
make dev (launcher) ─┐
desktop shortcut ─────┤→ vayren-shell → backend spawn (JSON) → snapshots once → Slint loop
```

- Single runtime: one backend process, one native window.
- **Fail-closed bridge:** snapshots validated before reaching Rust state; backend errors never touch the UI.
- `limit: int | None = None` = full history (no `LIMIT NULL` bind).

## 5. Boundaries & Invariants

| Boundary | Rule |
|---|---|
| Cross-module traffic | Public surface + bridge/snapshot only. Direct internal calls forbidden. |
| Single composition root | `src/app` (+ shell) owns wiring. UI never touches bus/SQL/loading. |
| One module one responsibility | New behavior → new module. Existing modules are not expanded. |
| Layers isolated | UI: no SQL; business logic: never in UI. Strategy never calls broker; risk never reads market. |
| Public contract | Consumers depend on `__init__.py` exports + events, never internals. |
| No fabrication | Bars/quotes derived only from real `ohlcv` rows. Aggregation never invents candles. |
| Secrets | Credentials/tokens never in events, settings, logs, or UI. |
| No placeholder | No mock/sample trading logic, no `TODO/FIXME`, no dead code. |
| Live safety | PAPER default; LIVE needs 5 explicit gates; kill switch persisted; UNKNOWN reconciled, never resubmitted. |

## 6. Enforcement

| Check | What it validates |
|---|---|
| `tools/validate_imports.py` (AST) | Dependency graph; forbids internal/relative/star imports (`if TYPE_CHECKING:` exempt) |
| `tools/validate_structure.py` | Required layout per domain (app/core/data/market/strategy/backtest/risk/execution/broker) |
| `tools/validate_language_ownership.py` (AST) | No new Python in Rust-owned domains, no Python UI, no reintroduced authorities, Rust hygiene |
| `tools/validate_authority.py` (AST) | Single registries/authorities/writers, `__all__` contracts, bridge purity, UI tokens, route drift |
| `tools/validate_routes.py` | Task routes: files/symbols/tests/validation exist, no duplicate authority |
| `make check` | `rust` + lint + format + typecheck + test + all validators — must pass before merge |

> Evolution (not a roadmap): new module → lower-numbered public APIs, bus/bridge only, own responsibility, contracts in `module_contracts.md`+`event_catalog.md`. Migration is **invisible feature-driven** per `AI_ENTRY.md` §1: new feature → target language + directly related slice (smallest useful) → validate; no unrelated migration, no big-bang.
