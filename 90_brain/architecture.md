# Architecture — VAYREN Current System

**Owns:** Module map, layers, dependency graph, event flow, runtime. **Not owns:** Language ownership → `AI_ENTRY.md` §1 (machine: `ownership_policy.json`); detailed boundaries/events/contracts/state → `module_contracts.md`, `event_catalog.md`, `ai_memory.md`.
**When to read:** ALWAYS first, before any code change. **Related:** `AI_ENTRY.md` §1 (language map), `90_brain/` (contracts/state). **Status:** Authoritative. Current system only — not a diary.

## 1. What is VAYREN?

Native trading platform: Rust + Slint shell (`rust/vayren-shell`) served by a headless Python backend (`00_app/app/headless.py`) over newline-delimited JSON. Snapshot → Rust state → Slint projection. No business logic in UI, no UI in business logic.

## 2. Current Modules

| Chapter | Package | Owns | Depends on |
|---|---|---|---|
| `00_app` | `app` | Composition (headless backend + services) | all chapters (partly unwired — see `ai_memory.md`) |
| `01_core` | `core` | Event marker, AI guardrails, native loader | stdlib only |
| `02_data` | `data` | Provider SDK boundary, settings, bridge | `core`, `broker` |
| `03_market` | `market` | Bar vocabulary + native bridges | `core` |
| `05_strategy` | `strategy` | Registry, runtime, research, lab | `core`, `market` |
| `06_backtest` | `backtest` | Native bridges only (Rust owns engine) | — (via FFI) |
| `07_risk` | `risk` | Native bridges only (Rust owns engine) | — (via FFI) |
| `08_execution` | `execution` | AI-adjacent logic + bridges (Rust owns core) | — (via FFI) |
| `09_broker` | `broker` | Registry, selection, vocab (UBL) | stdlib only |
| `rust` | `vayren-core` / `vayren-shell` | Kernels (std-only) + Slint shell | std only / Slint |

## 3. How Modules Are Connected

Allowed graph (machine truth: `DOMAIN_DEPS` in `scripts/validate_imports.py`; qualifiers: `module_contracts.md` §4):

```text
00_app → all chapters (composition root) · 01_core → none · 09_broker → none
02_data → core, broker · 03_market → core · 05_strategy → core, market
06_backtest → core, market, strategy · 07_risk → core
08_execution → core, market, strategy, risk, broker
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
| Single composition root | `00_app` (+ shell) owns wiring. UI never touches bus/SQL/loading. |
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
| `scripts/validate_imports.py` (AST) | Dependency graph; forbids internal/relative/star imports (`if TYPE_CHECKING:` exempt) |
| `scripts/validate_structure.py` | Required layout per domain (app/core/data/market/strategy/backtest/risk/execution/broker) |
| `scripts/validate_language_ownership.py` (AST) | No new Python in Rust-owned domains, no Python UI, no reintroduced authorities, Rust hygiene |
| `scripts/validate_authority.py` (AST) | Single registries/authorities/writers, `__all__` contracts, bridge purity, UI tokens, route drift |
| `scripts/validate_routes.py` | Task routes: files/symbols/tests/validation exist, no duplicate authority |
| `make check` | `rust` + lint + format + typecheck + test + all validators — must pass before merge |

> Evolution (not a roadmap): new module → lower-numbered public APIs, bus/bridge only, own responsibility, contracts in `module_contracts.md`+`event_catalog.md`. Migration is **invisible feature-driven** per `AI_ENTRY.md` §1: new feature → target language + directly related slice (smallest useful) → validate; no unrelated migration, no big-bang.
