# AI_ENTRY — Start Here (only file you must read first)

> VAYREN = native trading platform: Rust+Slint UI (`rust/vayren-shell`) served by a
> headless Python backend (`00_app/app/headless.py`) over newline-delimited JSON.
> This file routes you to the right module in under a minute. Detail lives ONLY
> in the linked canonical source — never duplicated here.
>
> Machine routing (exact files, no search): `python scripts/route.py --task "<task>"`
> (index: `90_brain/task_routes.json`, self-validated by `scripts/validate_routes.py`).
> Execution handoff (packet + plan + safety): `python scripts/context_engine.py --task "<task>"`.

## 1. Language map (canonical ownership — machine mirror: `90_brain/ownership_policy.json`)

```text
Rust        → Core, Performance, Market/Data, Indicators, Numerical, Risk, Execution, Backtesting
Python      → Strategy, AI/ML, Research, Experimentation, Model workflows
Rust+Slint  → Native UI
Everything else → DENIED (arch gate FAILs new languages)
```

Fixed rules (no exceptions without an explicit architecture change):

```text
1. Language/ownership architecture is fixed.
2. Rust-owned kaam Python mein nahi, Python-owned kaam Rust mein nahi.
3. Native UI sirf Rust + Slint.
4. New language/ownership bina explicit architecture change ke allowed nahi.
```

## 2. Domain routing (TASK → MODULE → LANG → START HERE → CHECK)

```text
app composition   → 00_app/app               → Py → app/headless.py                     → (unwired; broker_selection_service live)
core bus/registry → rust event_bus/registry  → Rs → rust/vayren-core/src/event_bus.rs  → cargo test -p vayren-core --lib
AI guardrails     → 01_core/core/ai          → Py → core/ai/boundary.py                → (import-debt; advisory only)
native loader     → 01_core/core/native      → Py → core/native/loader.py              → via bridges
event vocab       → core.Event / market.Bar  → Py → market/models/bar.py               → importable shapes
provider SDKs     → 02_data/data/provider    → Py → data/provider/contract.py          → 02_data/data/tests
download config   → settings/throttle        → Py → data/settings.py                   → 02_data/data/tests
download flow     → rust download(_engine)   → Rs → data/native_download.py (bridge)   → cargo test (unwired driver)
market store      → rust market/aggregate    → Rs → market/native_aggregate.py         → cargo test (unwired driver)
backtest          → rust backtest*           → Rs → backtest/native_metrics.py         → cargo test (unwired driver)
risk              → rust risk_engine/kill_sw → Rs → risk/native_engine.py              → cargo test (unwired driver)
execution         → rust execution*          → Rs → execution/native_execution.py      → cargo test (unwired driver)
order lifecycle   → rust order_state           → Rs → rust/vayren-core/src/order_state.rs (+ execution/native_order_state.py bridge) → cargo test + parity
execution AI      → adaptive/regime/ml       → Py → execution/adaptive/                → advisory only, importable
strategy          → 05_strategy/strategy     → Py → strategy/runtime.py, registry.py   → contracts §5.6 (package-entry debt)
broker UBL        → 09_broker/broker         → Py → broker/registry.py                 → broker/tests (healthiest domain)
UI screens        → rust/vayren-shell+views  → Rs+Slint → shell/src/market.rs + ui/    → cargo test (tokens: ui/palette.slint)
tooling           → scripts/                 → Py → scripts/validate_*.py              → scripts/tests
```

Boundaries (machine-enforced): allowed deps `scripts/validate_imports.py`
(`DOMAIN_DEPS`); SDK/network only in `02_data/data/provider/`, `09_broker/broker/adapters/`;
bridges = marshal→delegate→return (`*/native_*.py` only may import `core.native`).

## 3. Binding rules (detail: `AGENTS.md`; ownership: AI_ENTRY.md §1; policy: `90_brain/*.json`)

```text
1. One responsibility = one owner = one language (duplicates forbidden).
2. Cross-module: public surface + bus/bridge only; no internals, no `import *`, no relative cross-module.
3. Rust-owned logic never reimplemented in Python (bridges delegate, no rule tables).
4. PYTHON-owned domains (strategy/AI/research/providers) never migrated for performance.
5. No mock/sample trading logic, no TODOs, no dead code, no secrets in code/logs/events.
6. New file in Rust-owned domain without retention entry → validator FAIL. New language → gate FAIL.
```

## 4. New code order (new behavior → new module, never expand old ones)

```text
MODEL (models/file.py) → EVENT (events/file.py) → SERVICE (services/file.py) → TEST (tests/test_file.py)
```
File/type/model/event standards live in `AGENTS.md` only (not repeated here).

## 5. Validate (`make check` = rust + lint + format + typecheck + test + validators)

```text
make check              # full gate (must pass before merge)
pytest <module>/tests   # fast impact scope while iterating
python scripts/forensics/__main__.py report --name "..."   # LAST command of a task
```

State: `90_brain/ai_memory.md` (current only).
Contracts: `90_brain/module_contracts.md`. Events: `90_brain/event_catalog.md`. Map/layers: `90_brain/architecture.md`.
