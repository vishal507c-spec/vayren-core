# Development Log — Kya Kab Hua

**Nya entry hamesha upar likho.**

## 2026-08-14 — Universal Foundation Part 4 (Runtime Integration)

### Goal
Naya architecture (manifests, registries, SystemModel) ko **asli runtime se jodna** — bina kuch replace kiye. Do real components integrated: **market + chart**. Ek hi runtime rehta hai (existing Bootstrap + EventBus). Koi naya runtime, koi naya event system, koi naya registry nahi. **"Do less, but make it correct."**

### Audit (pehle, code change se pehle)
- Bootstrap = composition root (services create + old `Registry` names + sab subscriptions) — ✅ reuse kiya, naya composition root nahi banaya
- EventBus — ✅ chhua nahi (0 changes)
- Old `Registry` — ✅ untouched, names same (`symbol_repository`, `chart_engine`, ...)
- POC (`core/tests/test_component_poc.py`) ke pas already **factual** market/chart manifests the — production mein move karna tha, kuch naya design nahi
- `SystemSnapshot` already serializable tha (names only, koi live object nahi) — section 12 verify hua, koi fix nahi lagi
- Registry compatibility: dono lookup side-by-side — old names + new capability lookup

### Changes
| File | Kaam |
|---|---|
| `02_market/market/manifest.py` (NEW) | `market_manifest()` — asli market component ka production manifest (storage type, 4 capabilities: data.query.candles/timeframes/quotes + data.transform.aggregate, events consumed/produced, dependency core) — POC se move, sirf imports badle |
| `03_chart/chart/manifest.py` (NEW) | `chart_manifest()` — asli chart component ka production manifest (presentation type, chart.render capability, consumes data.query.candles, deps core+market) |
| `02_market/market/__init__.py` | `market_manifest` export |
| `03_chart/chart/__init__.py` | `chart_manifest` export |
| `00_app/app/bootstrap/bootstrap.py` | `_build_architecture()` — startup par 2 real components register karta hai (`ComponentRegistry`) with **live service instances** as implementations (SymbolRepository ×4 capabilities, ChartEngine), phir `SystemModel` build. Naye properties: `components`, `system_model`. Existing wiring/service registration bilkul unchanged |
| `01_core/core/tests/test_component_poc.py` | Local manifest defs hata ke production manifests import kiye — **ek hi source of truth**; implementations mapping same (classes) |
| `scripts/validate_structure.py` | `market/manifest.py` + `chart/manifest.py` required |
| `00_app/app/tests/test_runtime_integration.py` (NEW) | 10 required integration tests |

### Real components integrated (sirf 2 — intentional)
1. **market** — `data.query.candles`, `data.query.timeframes`, `data.query.quotes`, `data.transform.aggregate` → implementations = live `SymbolRepository` instance
2. **chart** — `chart.render` → implementation = live `ChartEngine` instance

### How it connects
```
Bootstrap.__init__
  → existing services + wiring (unchanged)
  → _build_architecture(repository, engine)
      → ComponentRegistry.register(market_manifest, {capabilities → live instances})
      → ComponentRegistry.register(chart_manifest, {chart.render → engine})
      → SystemModel(registry)          # startup par ek baar, hot path par kabhi nahi
bootstrap.components   → capability lookup (naya)
bootstrap.services     → old name lookup (wahi)
bootstrap.system_model → architecture model (AI-readable)
```

### Tests (10 naye → 522 total, sab green)
1. Real components have valid manifests (register validate karta hai) 2. Capabilities discoverable (`find("data.")` = 4) 3. Old registry still works (names + instances) 4. Capability lookup works (chart.render → live engine instance) 5. SystemModel sees real components (chart, market + deps) 6. SystemModel sees capabilities (find_capability, consumers("data.query.candles") == ("chart",)) 7. EventBus behavior unchanged (publish/start flow) 8. Startup flow unchanged (title, visibility, full chain to WindowRendered) 9. Snapshot serializable (JSON, no "object at"/"at 0x") 10. No unnecessary overhead (bounds)

### Performance (measured, offscreen, seed 2 symbols)
| Cheez | Value |
|---|---|
| Full startup (Bootstrap ctor) | 6.54 ms |
| Architecture build (register×2 + model + snapshot) | **0.134 ms = 2.05% of startup** |
| Capability lookup (`providers`) | 2.3 µs |
| Event dispatch (no-op) | 0.3 µs — EventBus code 0 changes, hot path untouched |
| Snapshot build | 0.024 ms |

Overhead negligible → koi optimization zaroori nahi. SystemModel construction sirf startup par (har event/candle/tick par kabhi nahi).

### Deliberately NOT built (over-engineering audit)
- Koi naya runtime / event system / registry / DI framework / service locator / adapter layer nahi
- Koi plugin scanner, koi reflection, koi dynamic import nahi — explicit registration (`market_manifest()` / `chart_manifest()`)
- Koi naya config system, koi database nahi
- Koi manager/wrapper/interface nahi — manifests plain functions, Bootstrap wahi composition root
- Migrate nahi kiya: Historical Download, Strategy, Risk, Execution, etc. — sirf 2 components (intentional)

### Verification
- `pytest` = **522 passed** (512 → 522, 10 naye integration tests)
- `ruff check` ✓ + `ruff format --check` ✓, `pyright` **0 errors**
- `validate_structure.py` PASSED (4 domains), `validate_imports.py` PASSED
- NOT committed (task rule).

### Remaining (Part 5+)
Workflow **execution engine** (model declarative hai), `vayren --describe` CLI (SystemModel se AI-readable description print), baaki components ka incremental migration (same pattern: manifest.py + Bootstrap registration), health polling, config source.

---

## 2026-08-14 — Universal Foundation Part 3 (AI Engineering + Evolution Layer)

### Goal
Part 2 ke system model ke upar **deterministic, AI-optional intelligence layer**: AI propose kar sakta hai, VAYREN deterministic policies se validate karta hai, runtime kabhi AI ke haath mein nahi. **"AI proposes. VAYREN validates. Deterministic runtime executes."** Koi LLM call nahi, koi self-modification nahi, koi production behavior change nahi. Part 3 uses Part 2's SystemModel; Part 1/2 ki tarah pure stdlib.

### Created (01_core/core/ai/)
| File | Kaam |
|---|---|
| `intent.py` | `IntentKind` (CREATE_WORKFLOW/ADD_COMPONENT/ADD_DATA_SOURCE/CREATE_STRATEGY/IMPROVE_PERFORMANCE/OTHER) + `Intent` (goal/constraints/requested_capabilities/inputs/expected_outputs/risk_level default LOW) + `classify()` keyword rules + `parse_intent`/`validate_intent` + `IntentValidationError` |
| `plan.py` | `PlanChangeKind` (ADD/MODIFY/REMOVE) + `PlanChange` + `PlanRisk` (level/reason) + `Rollback` (steps) + `Plan` (id regex `^[a-z][a-z0-9_]*$`, summary, requirements, reused/new capabilities — **overlap rejected**, components, changes — target component list mein hona zaroori, tests, benchmarks, risks, rollback) + `risk_rank()` (LOW 0 < MEDIUM 1 < HIGH 2) + `plan_risk()` (max declared, default LOW) + `validate_plan` + `PlanValidationError` |
| `plan_validator.py` | `Policy` (protected_components, forbidden_capabilities, max_risk, allowed_change_kinds, requires_rollback_above) + `PlanValidator.validate(plan, system)` — reused capability system mein hona chahiye, nayi capability pehle se provided nahi honi chahiye, MODIFY/REMOVE unknown component = reject, phir har policy check. Deterministic errors, order fixed |
| `change_simulation.py` | `simulate_plan(system, plan)` — Part 2 `analyze_change` per component: affected components (sorted union + self), capabilities (provided + affected), workflows, `required_tests` (`regression:<component>` labels + plan.tests), `estimated_risk` = max, sorted `reasons` |
| `sandbox.py` | `SandboxStage` strict lifecycle: PLAN → SANDBOX (simulate) → TEST → BENCHMARK → VALIDATE → APPROVE → DEPLOY — **stage skip = `SandboxError`**, history records every transition. `deploy()` = **recorded decision only** (`SandboxDeployment`, note: "deterministic runtime remains authoritative"), kuch execute nahi hota. Invalid plan → approve `SandboxError("cannot approve an invalid plan")`. Lazy simulation/validation caches |
| `boundary.py` | `ActionKind` — 9 allowed (understand, plan, propose, simulate, benchmark, validate, compare, suggest, report) + 6 **forbidden** (execute_trade, bypass_risk, delete_production_data, modify_protected_system, deploy_unvalidated, override_contract). `AiBoundary` fail-closed: `classify()` keyword verbs (banned verb wins over allowed), `request`/`require` (`BoundaryViolation`), `request_text` unknown → denied "unrecognized action request" |
| `providers.py` | `AiProvider` ABC (name/available/describe) + `OfflineProvider` (default, hamesha unavailable) + `AiProviderRegistry` (register dupes rejected, `select(preferred)` → None on unavailable = **graceful AI optionality**, `status()`) |
| `memory/engineering.py` | `Decision` (ACCEPTED/REJECTED/DEFERRED) + `EngineeringEntry` (problem/hypothesis/experiment/change/benchmark/result/decision/reason/evidence — `matches()` case-insensitive across ALL text fields) + `EngineeringMemory` (add auto-id, get (KeyError), search deterministic, decisions() = accepted+rejected only, problems() unique ordered) |
| `memory/performance.py` | `METRICS` fixed: latency_ms/throughput/cpu_percent/memory_mb/io_ops/error_rate; `LOWER_IS_BETTER` = sab minus throughput. `PerformanceRecord` + `PerformanceMemory` — `record()` **requires ≥1 metric** ("performance claims require measurements"), `for_subject`/`latest`/`best` (min for lower-is-better, max for throughput)/`average`/`summary`/`subjects` |
| `optimization.py` | `Candidate` (label/description) + `OptimizationStudy` (current + add_candidate dupes rejected) — `benchmark` (unknown metric → `OptimizationError`), `compare`/`recommend` (measured candidates only, lower-better ascending / throughput descending), **`adopt()` hamesha `OptimizationError`** — "adoption requires a validated plan and sandbox approval" (guarded self-optimization) |
| `context.py` | `SECTIONS` canonical order + `ContextRequest` (unknown section → ValueError) + `ContextBuilder.build()` (bina request = sab sections) — components/capabilities (providers mapping)/contracts/dependencies/workflows/events/system_state (live counts + snapshot gaps)/architecture_history/engineering_memory (search) — sab deterministic text + `AiContext.render()`/`to_json()` |
| `__init__.py` | Package exports (sab public API `core.ai` se) |

### Modified
- `core/__init__.py` — Part 3 exports + `__all__` (Intent*, Plan*, Policy/PlanValidator, ChangeSimulation, Sandbox*, AiBoundary/ActionKind, EngineeringMemory, PerformanceMemory, OptimizationStudy, AiProvider/Registry/OfflineProvider, ContextBuilder/AiContext)
- `scripts/validate_structure.py` — `core/ai/` + `core/ai/memory/` required files
- `core/tests/test_dependency_isolation.py` — `FOUNDATION_MODULES` mein 13 ai module paths (isolation check ab ai layer ko bhi cover karta hai: app/market/chart/PySide6/httpx/requests/numpy/pandas banned)
- `core/ai/context.py` — `build()` request optional (bina request = full context)
- `core/ai/optimization.py` — `benchmark()` unknown metric validation + explicit metric mapping (pyright-safe)

### Tests (85 naye → core total 512)
`test_intent.py` (8: spec examples Add broker/Create workflow/Improve performance/Add data source/Create strategy), `test_plan.py` (7: id regex, summary, reused∩new, changes require components, target listed, empty component, plan_risk max), `test_plan_validator.py` (11: protected component reject — **spec example**, reused-not-provided, new-already-provided, unknown component, forbidden capability, change kind not allowed, risk over limit, rollback required, multi-policy), `test_change_simulation.py` (7: market change reaches chart via capability graph, workflow impact, risk aggregation, deterministic), `test_sandbox.py` (10: happy path to deploy, deployment = record, no skips, invalid plan can't approve, lazy caches, history), `test_engineering_memory.py` (8: spec batch-size example, auto-id, search cross-field case-insensitive, decisions filter), `test_performance_memory.py` (10: ≥1 metric required, best min/max, unknown metric, average/latest/subjects/summary), `test_optimization.py` (7: candidates, dupe reject, benchmark validation, compare rank, recommend, adopt never automatic), `test_ai_boundary.py` (6: allowed all pass, forbidden all denied, spec coverage, require raises, classify verbs, unknown fails closed, end-to-end protected plan → validator → sandbox), `test_ai_providers.py` (7: offline default, graceful None, preferred selection, dupe reject, ordered), `test_ai_context.py` (15: canonical order, subset, unknown section, ground truth content, deterministic).

### Verification
- `pytest` = **512 passed** (core 172 → 512)
- `ruff check` ✓ + `ruff format --check` ✓, `pyright` **0 errors/0 warnings**
- `validate_structure.py` PASSED, `validate_imports.py` PASSED
- NOT committed (task rule).

### Remaining (Part 4+)
Workflow **execution engine** (model declarative hai), `vayren --describe` CLI, per-chapter manifests (`market/components.py` style), declarative composition root, health polling, config source. Part 3 ka ai layer abhi pure model/observation hai — provider adapter (LLM) kabhi add ho to `AiProviderRegistry` se, runtime boundary kabhi nahi badalta.

---

## 2026-08-14 — Universal Foundation Part 2 (System Intelligence Layer)

### Goal
Part 1 ke foundation ke upar **machine-readable architecture model**: poora registered system queryable, deterministic, AI/human-readable — bina kisi production behavior change. Koi engine, koi EventBus, koi market/chart code touch nahi hua.

### Created (01_core/core/system/)
| File | Kaam |
|---|---|
| `component_graph.py` | `ComponentGraph` — manifests se deps/dependents (hard + optional dono reverse-edges banate hain), transitive closure dono direction, `find_cycle()` (Kahn's + leftover walk, deterministic sorted). Referenced-but-unregistered deps bhi nodes ban jaate hain |
| `capability_graph.py` | `CapabilityGraph` — capability → providers/consumers (`capabilities_consumed` se), `capabilities_of`, `consumed_by`, `unresolved_consumers()` |
| `event_graph.py` | `EventGraph` — event → producers/consumers, `unproduced()`, `unconsumed()`, `events_of` |
| `data_flow.py` | `DataFlowModel` — component inputs/outputs, workflow `data_path` (step, capability, output) triples |
| `workflow.py` | `WorkflowStep`/`Workflow` (id regex `^[a-z][a-z0-9_]*$`, duplicate step ids rejected), `WorkflowRegistry` (register/get/list sorted/find_by_capability/`in`/len) |
| `change_impact.py` | `RiskLevel` (LOW/MEDIUM/HIGH) + `ChangeImpact` + `analyze_change` (auto-dispatch: `.` → capability, registered id → workflow, warna component). Risk rules: HIGH = indirect dependents ya workflows; MEDIUM = direct dependents/consumers; LOW = kuch nahi. `SystemModel` sirf `TYPE_CHECKING` import (circular import fix) |
| `snapshot.py` | `SystemSnapshot` (components/capabilities/events/workflows/gaps) + `build_snapshot` — deterministic, `to_dict`/`to_json`/`render`. Gaps: unresolved_consumers, unproduced_events, unconsumed_events, dependency_cycles. `SystemModel` sirf `TYPE_CHECKING` |
| `system_model.py` | `SystemModel` — queryable facade: `registry` + optional `WorkflowRegistry`; `components()`/`find_component`/`capabilities()`/`find_capability(prefix)`/`find_implementations` (asli provider objects registry se)/`find_dependents`/`find_dependencies`/`find_consumers`/`find_workflows`/`analyze_change`/`snapshot()` |
| `__init__.py` | Public exports |

### Modified
- `core/contracts/manifest.py` — `ComponentManifest.capabilities_consumed: tuple[CapabilityId, ...]` + validation: duplicate capabilities, duplicate consumed, **own capability consume rejected**, duplicate events_consumed
- `core/__init__.py` — system exports (SystemModel, ChangeImpact, RiskLevel, SystemSnapshot, Workflow, WorkflowRegistry, WorkflowStep, analyze_*, build_snapshot)
- `scripts/validate_structure.py` — `system/__init__.py` required
- `core/tests/test_component_poc.py` — chart manifest ab `data.query.candles` consume karta hai

### Tests (66 naye → core total 172)
`test_component_graph.py` (9: sorted deps, reverse edges incl. optional, transitive, cycle detection), `test_capability_graph.py` (6), `test_event_graph.py` (6), `test_data_flow.py` (5), `test_workflow.py` (8), `test_change_impact.py` (10: HIGH/MEDIUM/LOW + dispatch), `test_system_model.py` (8), `test_snapshot.py` (6), `test_manifest.py` (+2: duplicate consumed, self-consumption).

### Bugs found & fixed during verification
1. `manifest.py` mein `CapabilityId` import missing (NameError) — fixed
2. `component_graph.py` dependents loop mein stale `name` variable (edit regression) — proper `for name, edges` loops
3. `change_impact.py` — `elif direct` ne indirect se set HIGH ko MEDIUM par overwrite kar diya — risk ab last condition se compute hota hai (reasons independent)
4. `data_flow.py` — `components()` mein bina data wale components bhi aate the — ab sirf declared inputs/outputs
5. Pyright: `implementation.__name__` object type par — test `is CandleRepository` identity check karta hai

### Verification
- `pytest 01_core/core/tests` = **172 passed** (sab green)
- `ruff check` ✓ + `ruff format --check` ✓, `pyright` **0 errors**
- `validate_structure.py` PASSED (4 domains)
- NOT committed (task rule).

### Remaining (Part 3+)
`vayren --describe` CLI, per-chapter manifests (`market/components.py` style), declarative composition root, workflow **execution engine** (model abhi declarative hai), health polling, config source, engineering memory.

---

## 2026-08-14 — Universal Foundation Part 1 (Component + Capability + Manifest + Registry)

### Goal
Approved universal architecture ka Part 1: ek chhota, clean, stdlib-only foundation jo VAYREN ko component-oriented, capability-oriented, contract-driven aur self-describing banata hai. Sirf foundation — koi migration nahi, koi engine rewrite nahi, AI/Part 2/3 nahi.

### Safety list (approved)
- **KEEP**: EventBus, Registry, logger, events, market/chart/app production code, pyproject, Makefile — sab untouched
- **MODIFY**: `01_core/core/__init__.py` (exports), `01_core/core/registry/__init__.py` (exports), `scripts/validate_structure.py` (core ke liye naye required files)
- **CREATE**: `01_core/core/contracts/` package + 2 naye registries + 9 test files
- **DO NOT TOUCH**: 02_market, 03_chart, 00_app code, EventBus, existing Registry

### Created (01_core/core/contracts/)
| File | Kaam |
|---|---|
| `component.py` | `ComponentId` (lowercase snake validation), `ComponentVersion` (semver parse), `ComponentStatus` enum, `ComponentMetadata` |
| `capability.py` | `CapabilityId` (dot-id `data.query.candles`, ≥2 segments, prefix matching), `BehavioralRules` (can/must/must_not/guarantees/failure_modes), `CapabilityContract`, `CapabilityDecl`, `CapabilityProvider` |
| `contract.py` | `ComponentContract` (invariants + capability contracts) |
| `health.py` | `HealthStatus`, `Health`, `HealthCheck` callable type |
| `manifest.py` | `ComponentManifest` (Component DNA: identity, version, type, capabilities, inputs, outputs, dependencies, optional deps, events consumed/produced, health, resources, side effects, metadata, contract) + `validate_manifest` + `ManifestError` + `ManifestValidationResult` |

### Created (registries)
- `01_core/core/registry/capability_registry.py` — **`CapabilityRegistry`**: capability → implementations (multiple allowed, same-component duplicate rejected), `providers()`, `capabilities()` (sorted), `find(prefix)` glob discovery, `has`/`__contains__`/`__len__`. Backward compatible — existing `Registry` untouched.
- `01_core/core/registry/component_registry.py` — **`ComponentRegistry`** + `RegisteredComponent` + `ComponentRegistryError`: `register(manifest, implementations)` (har declared capability ka implementation zaroori, har implementation declared, duplicate component rejected, invalid manifest → `ManifestError`), `component()`/`components()`/`capabilities()`/`providers()`/`find()`/`summary()` (deterministic AI-readable text).

### Proof of concept (Part 10)
`01_core/core/tests/test_component_poc.py` — asli `market` + `chart` components ko factual manifests ke saath register karta hai (koi production change nahi):
- `data.query.candles`, `data.query.timeframes`, `data.query.quotes`, `data.transform.aggregate` → market; `chart.render` → chart
- Discovery answers: "kaun data.query.candles deta hai?" → market; "chart kya depend karta hai?" → core + market
- Chain proven: Component → Manifest → Capabilities → Registry → Discovery

### Tests (106 naye, deterministic, Qt-free)
1. identity, 2. capability definition, 3. manifest validation, 4. capability registration, 5. discovery, 6. multiple implementations, 7. invalid manifest rejection, 8. **dependency isolation** (AST scan: foundation modules sirf stdlib + core import karte hain — no app/market/chart/PySide6/httpx), 9. Registry backward compatibility.

### Verification
- `pytest` = **340 passed** (234 baseline + 106 naye; existing 228 app+market+chart+core-old tests **zero changes**, sab green)
- `ruff check` ✓, `ruff format --check` ✓, `pyright` **0 errors**, validators PASSED (structure ab naye core packages enforce karta hai)
- **Perf sanity**: register 1000 capabilities = 8.8 ms (8.8 µs each); lookup = **2.74 µs**; 200 component registrations (validation sahit) = 6 ms; `summary()` = pure text. Pure dict-based — koi reflection/serialization/factory nahi.
- NOT committed (task rule).

### Remaining for Part 2 (not implemented)
SystemModel builder + `vayren --describe`, manifests per chapter (market/components.py etc.), declarative composition root, workflows, health polling, config source, engineering memory.

## 2026-08-13 — Watchlist Rows: Complete Market Information (Phase 5N)

### Goal
Watchlist rows ko institutional 2-line format: line 1 = SYMBOL (left) + PRICE (right), line 2 = change % (right, bull/bear colored). **Sirf real data** — company name/icon/marker app mein kahin exist nahi karta (real DB schema sirf OHLCV hai) → gracefully omitted. No fake data, watchlist functionality untouched, fast, do NOT commit/push.

### Data decision (user-approved)
Watchlist model mein sirf symbols the. User ne **real batch quotes** approve kiya: `SymbolRepository.get_quotes` — har symbol ke DB ka **sirf latest candle** read karta hai (`fetch_candles(symbol, 1)` → `ORDER BY candle_time DESC LIMIT 1`, kabhi full history nahi), `SymbolQuote(symbol, price=close, change_pct=return_pct, timestamp)`. Change % = wahi `(close−open)/open` semantics jo chart header use karta hai. Missing file → gracefully skip. Measured: **~1 ms/symbol, 527 stocks ≈ 0.4–0.55 s total, ek baar at startup, phir cached** — kabhi re-query nahi.

### Changes
| File | Change |
|---|---|
| `02_market/market/models/symbol_quote.py` | **Naya** `SymbolQuote` — frozen dataclass (symbol, price, change_pct, timestamp) |
| `02_market/market/events/quotes_loaded.py` | **Naya** `QuotesLoaded(quotes: tuple[SymbolQuote, ...])` |
| `02_market/market/repository/symbol_repository.py` | `get_quotes(symbols)` — per-symbol latest-candle read; missing file skip |
| `02_market/market/loader/quote_loader.py` | **Naya** `QuoteLoader.on_symbols_listed` → `get_quotes` → `QuotesLoaded`; **identical universe = no-op** (`_last_symbols` guard, `None` initial — pehla listing hamesha publish hota hai) |
| `00_app/app/bootstrap/bootstrap.py` | `quote_loader` service register; `SymbolsListed → [window.on_symbols_listed, quote_loader.on_symbols_listed]` (order = watchlist pehle); `QuotesLoaded → window.on_quotes_loaded` |
| `03_chart/chart/windows/chart_window.py` | `on_quotes_loaded` → `watchlist.set_quotes` |
| `03_chart/chart/widgets/watchlist_widget.py` | `set_quotes` — `dict[symbol → SymbolQuote]` presentation state; `_refresh_list` (sort/watchlist switch) par re-attach |
| `03_chart/chart/widgets/symbol_list_widget.py` | **`_SymbolRowDelegate`** (QStyledItemDelegate): 2-line rows — line 1 symbol (DemiBold, left) + price (`,.2f`, right-aligned), line 2 change % (11px, bull `#26a69a`/bear `#ef5350`, right). Bina quote wali rows symbol-only vertically centered. **Selected = midlight bg + 2px teal accent edge + Text color** (HighlightedText #0b0f13 midlight par invisible tha — contrast fix) — change % colors selected par bhi readable. Hover = alternate-base, hairline separators sab rows. Quote item ke `UserRole` data mein (row-scoped). Uniform height = 2×line + padding |

### Verification
- **Real data (`D:\ZerodhaTradingData`, 527 stocks)**: startup total **414–556 ms** (quote batch included), **527/527 rows quoted** — e.g. `360ONE 1,073.30 +0.05% @ 2026-06-05 15:15:00`, `AARTIIND 430.60 -0.59%`.
- **Pixel checks 8/8**: selected midlight bg + teal edge, unselected window bg, hairline, price/symbol Text pixels, bull `#26a69a` pixel (360ONE), bear `#ef5350` pixel (AARTIIND). Screenshots: `Temp\opencode\shots_watchlist\`.
- **Perf**: `set_quotes(527)` = **0.72 ms**; sort rebuild + re-attach = **4.84 ms**; quotes survive sort (527/527). Per-repaint/per-mousemove DB queries: none. No per-symbol re-query after the one-time batch.
- `pytest` = **234 passed** (11 naye: 3 repo, 4 loader, 3 watchlist, 1 window), ruff ✓, pyright 0 errors, validators PASSED.
- NOT committed (task rule).

---

## 2026-08-13 — Institutional UI Redesign (Phase 5M)

### Goal
Default-looking Qt UI → serious trading terminal feel. **UI-only task**: functionality, data flow, EventBus, layout (watchlist | options | chart) sab untouched. Koi fake data nahi (sirf symbol + real OHLC).

### Problem (root causes)
1. Theme sirf palette-role QSS tha — OS theme ke hisaab se light/blue default Qt look (generic).
2. Chart header floating pills the (LabelRenderer framed labels), terminal strip jaisa nahi.
3. Rows/padding/scrollbar bulkier the; options panel plain.

### Changes (presentation only)
| File | Change |
|---|---|
| `03_chart/chart/theme.py` | **`APP_PALETTE`** — fixed dark terminal palette (cool near-black `#101418` family, single teal accent `#26a69a`, muted grays). **Refined `APP_STYLE`**: 3px radius, hairline structural separators, `QMenu::item:checked` accent+600, `QMenu::separator`, `QSplitter::handle:hover`, `QToolTip` styled, `TimeframeToolbar QPushButton` uniform segments (min-width 44, min-height 24) |
| `03_chart/chart/windows/chart_window.py` | **QSS `palette()` Qt ke behavior se application palette resolve hota hai** — `QApplication.setPalette(APP_PALETTE)` (window par bhi `setPalette`). Explicit `Segoe UI 9pt`. **Race fix**: `on_timeframes_listed` ab `_current_timeframe` re-apply karta hai — symbol switch par active button hamesha sahi checked (TimeframesListed ChartReady ke baad aata hai) |
| `03_chart/chart/widgets/symbol_list_widget.py` | Dense rows: padding 3px 8px, radius 2, hairline separators, selection = **teal accent + bold + dark text** (default blue OS box nahi). Scrollbar 6px, slim handle, hover dark |
| `03_chart/chart/widgets/watchlist_widget.py` | Header: compact margins, 24px normalized icon buttons (+ ↩ ⋯), selector fixed height 24 + weight 600. Sort row: uppercase `SYMBOL` muted table-header (11px, weight 600), aligned 12px baseline. Separators = explicit 1px hairline (`_SEPARATOR_STYLE`, midlight) |
| `03_chart/chart/widgets/options_panel.py` | Rail tint (`alternate-base`) — **`WA_StyledBackground`** (plain QWidget QSS bg ke liye zaroori), margins (8,10,8,8) |
| `03_chart/chart/widgets/timeframe_toolbar.py` | Compact margins (8,4,8,4), spacing 2, **`WA_StyledBackground`** — iske bina `TimeframeToolbar { border-bottom }` kabhi render nahi hota tha (latent bug) |
| `03_chart/chart/renderer/overlay_renderer.py` | **Header strip redesign**: `HEADER_BAND` (translucent pill) → solid `STRIP_BG #141922` + `STRIP_BORDER` hairline. `paint_symbol_info` = **two-tone unframed** (symbol bright bold `#e8eef5`, • meta muted `#8a93a6`). `paint_ohlc` = **institutional two-tone fields** (O/H/L/C letters muted `#5d6778`, values light `#b7c0cc`) + bull/bear change suffix. Signatures/contracts unchanged (tests pin call counts + args) |
| `03_chart/chart/widgets/candle_chart_widget.py` | `_paint_header`: solid strip + 3px radius + bottom hairline (grid-color) |

### Verification
- **Pixel-level rendering checks** (offscreen grab sampling): options rail tint, toolbar hairline border, active timeframe teal pill, header strip + hairline + text pixels, selected row teal fill, separators, chart bg — **15/15 pass**.
- `pytest` = **222 passed** (1 new: institutional palette applied), ruff ✓, pyright 0 errors, validators PASSED.
- Koi functional path nahi chhua: symbol/timeframe switching, header data (latest bar OHLC), crosshair pills, pan/zoom, reset — sab tests green.
- Screenshots: `C:\Users\visha\AppData\Local\Temp\opencode\shots\` (window, watchlist, chart, toolbar, options, selected row, crosshair).

### Design system (VAYREN identity)
Dense + precise + calm: cool near-black `#101418` surfaces, hairline `#2a3342` structure, teal `#26a69a` = only accent (active/selected + bull), muted `#8a93a6`/`#5d6778` secondary. No gradients, no shadows, no animation. Typography: 9pt Segoe UI, 12px controls, bold selected/active, muted labels.

---

## 2026-08-13 — Extreme UI Responsiveness Pass (Phase 5L)

### Approach: MEASURE FIRST (task rule — kabhi guess nahi)

Real dataset (`D:\ZerodhaTradingData`, 527 stocks, ~60k rows/stock) par profiler script se har interaction measure kiya BEFORE kuch chhua. Profile points: data layer, engine, full event pipeline, per-frame paint, watchlist.

### Bottlenecks found (BEFORE)

| Path | Latency |
|---|---|
| Timeframe click 1D → chart ready | **1874 ms** |
| Timeframe click 1W (full history) → ready | **2296 ms** |
| paintEvent (full frame) | **10.0 ms** (~100 FPS max) |
| Crosshair move + repaint | **11.0 ms/frame** |
| Pan step + repaint | **12.1 ms/frame** |
| Symbol click → chart ready | **43 ms** |
| `ChartEngine.on_data_loaded` (60k bars) | **85 ms** |
| `detect_timeframes` (per symbol click) | **56 ms** |
| Watchlist `set_symbols` (527 rows, har click par) | **7 ms** |

cProfile root cause: `aggregate_bars` = 2.2s, usme **`strptime` 58k calls = 2.1s** (har call locale lookup `_getlang` → `getlocale`!). `infer_timeframe` har 60k timestamp parse karta tha.

### Optimizations (sab measured hotspots, behavior untouched)

| File | Change | Win |
|---|---|---|
| `02_market/market/timeframe/aggregate.py` | `strptime` (2-format try/except, locale lookup) → `datetime.fromisoformat` (~100x faster parse); bucket aggregation single-pass running max/min/sum (pehle per-bucket generator re-scans + row/dt tuple lists) | 1D agg 1849 → 542 ms; 1W 2316 → 633 ms |
| `03_chart/chart/widgets/candle_chart_widget.py` | **Static-layer pixmap cache**: grid + candles + time axis ek widget-sized pixmap mein baked (key = model id, window, price range, volume_max, size); paintEvent = blit + header + crosshair. Crosshair-move frames kabhi bars nahi chhoote. Visible-window stats (low/high/volume_max) single-pass + cached (`_window_stats`, key par kabhi stale nahi) — `_price_range` auto-fill bhi cache se. `_price_range` per-frame slice+3 generator scans gaye | paint 10.0 → **0.93 ms** (10.7x); crosshair frame ~1.8 ms; pan frame 12.1 → **1.65 ms**; zoom 3.6 → **1.6 ms** |
| `03_chart/chart/widgets/watchlist_widget.py` + `symbol_list_widget.py` | `set_symbols`: identical symbol set = **no-op** (symbol clicks wahi universe re-publish karte hain — rows kabhi nahi recreate hote); rebuild ab `setUpdatesEnabled(False)` + `addItems` batch (ek layout+repaint) | 7 ms → **0.002 ms** per symbol click |
| `03_chart/chart/engine/chart_engine.py` | `_ascending` check — already-sorted bars par sort skip (contract "ascending bars" same, sirf O(n) verify) | 85 → 14 ms (60k bars) |
| `03_chart/chart/models/timeframe.py` | `infer_timeframe` ab pehle `_INFERENCE_SAMPLE = 2048` bars ka sampled median gap (regular series par bilkul same; 60k-bar load par 102 ms parse → ~1 ms) | engine pipeline ka bada hissa |

### Verification (AFTER)

| Path | BEFORE | AFTER | Speedup |
|---|---|---|---|
| Symbol click → chart ready | 43.5 ms | **16.1 ms** | 2.7x |
| Timeframe click 1D → chart ready | 1874 ms | **551 ms** | 3.4x |
| Timeframe click 1W → ready | 2296 ms | **632 ms** | 3.6x |
| paintEvent (full frame) | 10.03 ms | **0.93 ms** | 10.7x |
| Crosshair move + paint | 11.0 ms/frame | **1.8 ms/frame** | 6.1x |
| Pan step + paint | 12.1 ms/frame | **1.65 ms/frame** | 7.3x |
| Zoom step + paint | 3.6 ms/frame | **1.6 ms/frame** | 2.2x |
| `on_data_loaded` (60k) | 85 ms | **14.2 ms** | 6x |
| `detect_timeframes` | 56 ms | **21 ms** | 2.7x |
| Watchlist `set_symbols` | 7 ms | **0.002 ms** | ~3500x |
| Crosshair handler | — | **43 µs** | — |
| Watchlist scroll step | — | **4 µs** | — |

### Remaining bottleneck (documented)

Timeframe click 1D = **551 ms** — ab SQLite fetch (48k rows, ~170 ms) + pure-Python aggregation loop (~160 ms) + Bar construction ka honest cost hai. Aage jaane ke liye SQL-side aggregation (GROUP BY) chahiye — database architecture change = out of scope (correctness + architecture rules). Default click flow (base 15m) 16 ms hai — 550 ms sirf explicit higher-timeframe click par.

### Tests

`pytest` = **221 passed** (sab green, koi regression nahi). `ruff` ✓, `pyright` **0 errors**, validators PASSED. Static-layer cache grid-cache tests ke contracts se match karta hai (paint_grid sirf cache rebuild par, header har paint par live).

---

## 2026-08-13 — UI Polish Pass (Phase 5K)

### Kya hua tha?

Functionality theek thi — sirf visual polish chahiye tha. Task: behavior bilkul mat badlo, sirf UI ko smoother/cleaner/professional banao. No fake data, no new controls, no new framework.

### Decisions

- **Central theme** (`chart/theme.py`): ek hi palette-based QSS string, `ChartWindow` par apply — sab controls ek hi visual language share karte hain: 4px radius, transparent resting buttons, soft hover/pressed, highlight accent active state, framed menus, hairline separators. Koi hardcoded color nahi — sirf `palette(...)` roles (system palette ke saath auto-adjust).
- **Watchlist rows** (`symbol_list_widget.py`): padding 4/8, separators ab `palette(midlight)` (subtle), hover state `palette(alternate-base)`, selection = smooth rounded highlight (bulky box gaya), `outline: 0` (focus rect hataya), slim 8px rounded scrollbar (transparent track, hidden arrows).
- **Top controls / sort header** (`watchlist_widget.py`): buttons theme se consistent height/padding; "Symbol" label muted (`palette(placeholder-text)`) — typography hierarchy clear.
- **Timeframe bar**: `QPushButton` theme — borderless rounded buttons, hover midlight, **checked = highlight pill** (obvious par subtle). Functionality untouched.
- **Menus**: QMenu framed + rounded, selected item highlight, disabled item muted.
- **Panel separators**: `QSplitter::handleWidth(1)` — 1px hairline panel boundaries.
- **Chart header**: `_paint_header` mein subtle translucent rounded backdrop band (`OverlayRenderer.HEADER_BAND`, alpha 110) — header integrated feel, candles dominate. OHLC readout split: prefix muted + change segment bull/bear color (`CandleRenderer.BULL/BEAR`) — direction at a glance. Math/format bilkul waisa hi (close−open, sign, pct). `LabelRenderer.paint_left` ko optional `text_color` param mila (additive, default = waisa hi).

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/theme.py` | **Naya** — `APP_STYLE` centralized QSS |
| `03_chart/chart/widgets/symbol_list_widget.py` | Row/hover/selection/scrollbar QSS polish |
| `03_chart/chart/widgets/watchlist_widget.py` | Muted "Symbol" label |
| `03_chart/chart/windows/chart_window.py` | Theme apply + `setHandleWidth(1)` |
| `03_chart/chart/renderer/label_renderer.py` | `paint_left`/`_draw_label` mein optional `text_color` |
| `03_chart/chart/renderer/overlay_renderer.py` | OHLC change segment colored (bull/bear), `HEADER_BAND` constant |
| `03_chart/chart/widgets/candle_chart_widget.py` | Header backdrop band (cached brush) |
| `03_chart/chart/tests/test_chart_window.py` | 2 naye pinning tests (theme applied, hairline splitter) |

### Verification

- `pytest` = **221 passed** (219 + 2 naye) — 152 chart tests bhi green
- `ruff check` ✓ + `ruff format --check` ✓
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED
- Stylesheet parse warnings: none (Qt silent)

---

## 2026-08-13 — Timeframe Persistence on Symbol Switch (Phase 5J)

### Kya hua tha?

Bug: symbol switch par timeframe default par reset ho jaata tha — `Stock A (30m) → Stock B` par Stock B base timeframe (15m) par khulta tha.

### Root cause

`ChartWindow._on_symbol_selected` hamesha `LoadSymbol` publish karta tha — aur `MarketDataLoader.on_load_symbol` sirf **base candles** load karta hai (koi timeframe nahi). Selected timeframe state chart session ka hai, stock ka nahi — par symbol click usse kabhi use hi nahi karta tha.

### Decision

- `ChartWindow` ab `_current_timeframe: str | None` track karta hai — single source of truth:
  - `on_chart_ready` → `model.timeframe` se sync (jo chart abhi dikha raha hai)
  - `_on_timeframe_selected` → explicit user selection par set
  - `__init__` → `None` (default abhi tak select nahi hua)
- `_on_symbol_selected` ab:
  - `_current_timeframe is None` (pehli baar load) → `LoadSymbol` (base bars, waisa hi)
  - timeframe selected hai → `TimeframeChanged(symbol, timeframe, limit)` — existing market aggregation path reuse, koi naya event/path nahi
  - dono case mein `ListTimeframes` (waisa hi)
- `ChartEngine`/`MarketDataLoader`/repository — **koi change nahi**. Unavailable/at-or-below-base timeframe → repository ka existing fallback (plain fetch) handle karta hai, label wahi dikhta hai jo chart dikhata hai.
- Refresh feature app mein exist nahi karta (grep confirmed — sirf watchlist internal `_refresh_*` helpers) — koi refresh-reset path tha hi nahi.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/windows/chart_window.py` | `_current_timeframe` state; `_on_symbol_selected` ab selected timeframe par `TimeframeChanged` bhejta hai (LoadSymbol sirf default case); `on_chart_ready`/`_on_timeframe_selected` state sync; docstring |
| `03_chart/chart/tests/test_chart_window.py` | `_model` helper mein `timeframe` param; 2 naye tests: chart-loaded symbol switch `30m` rehta hai (LoadSymbol nahi), explicit `1D` selection 3 stock switches mein persistent |

### Verification

- `pytest` = **219 passed** (217 + 2 naye)
- `ruff check` ✓ + `ruff format --check` ✓
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — Permanent Chart Header (Crosshair se Independent)

### Kya hua tha?

Problem: chart header (`SYMBOL · TIMEFRAME · EXCHANGE` + OHLC top bar) sirf crosshair active hone par dikhta tha — mouse chart se bahar jaate hi gayab. Requirement: header **hamesha visible**, crosshair se bilkul independent, latest bar ka OHLC + Change/Change% (sirf real model data), symbol/timeframe badalne par turant update.

### Root cause

`_paint_overlays` (candle_chart_widget.py) top bar (symbol info + OHLC) ko crosshair ke saath paint karta tha — `paintEvent` mein `if crosshair is not None and chart_rect.contains(crosshair)` ke andar. Crosshair clear → header bhi clear.

### Decision

- **`_paint_header(painter, chart_rect)`** — naya method: har `paintEvent` mein model loaded hote hi paint hota hai (crosshair condition ke BAHAR). Content: `paint_symbol_info` (model.symbol/timeframe/exchange) + `paint_ohlc` (model.bars[-1] = **latest bar**, left_margin = symbol rect). Existing `OverlayRenderer` rendering reuse — koi duplicate header nahi, koi fake data nahi (sab model se).
- **`_paint_overlays`** ab sirf crosshair labels: right price + bottom time. Top bar crosshair se nikal kar permanent header mein — "Separate the header rendering from the crosshair visibility/update logic" waisa hi.
- **`_crosshair_dirty_rect`** se top-bar rect hata diya (ab crosshair-move par top bar repaint ki zaroorat nahi).
- Change/Change% pehle jaisa hi: `paint_ohlc` me `close - open` + `bar.return_pct` — real data se computed.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/candle_chart_widget.py` | `_paint_header` (permanent, latest bar), `_paint_overlays` ab sirf price+time, dirty-rect cleanup, docstring |
| `03_chart/chart/renderer/overlay_renderer.py` | Module docstring: permanent header vs crosshair-following labels |
| `03_chart/chart/tests/test_overlay_integration.py` | `test_no_overlays_without_crosshair` → `test_header_painted_without_crosshair` (header ab bina crosshair bhi paint hota hai); naye tests: immediate paint after load, symbol/timeframe/exchange from model, latest bar OHLC, model change → header update (TCS), crosshair visibility independence (3 paints) |

### Verification

- `pytest` = **217 passed** (213 + 4 naye)
- `ruff check` ✓ + `ruff format --check` ✓
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — Watchlist Layout Rebuild (Target-Layout-Conformant, Real Data Only)

### Kya hua tha?

Task: watchlist panel ko TradingView-style target layout se match karna — 2-line rows (price/company/change), filter row [S][G][T], bottom bar [Grid][Edit][⋯], vertical toolbar watchlist ke right. User clarification (2 questions) ne scope fix kiya:

- **No fake data**: rows sirf real symbol dikhate hain (NETWEB, TCS...). Price/company/change/logo ka data app mein hai hi nahi — invent nahi kiya. "No data → no field." Row layout future fields ke liye design-ready, par ab sirf symbol.
- **No fake controls**: [S][G][T] filter row, [Grid][Edit][⋯] bottom bar, extra more button — inme se koi existing nahi → **banaye nahi**. Existing functionality only.
- **Vertical toolbar**: app mein sirf `OptionsPanel` column hai (◉ ◇ placeholders) — task ke §11 (`WATCHLIST | VERTICAL TOOLBAR | CHART`) ke hisaab se splitter order badala: ab **watchlist | options | chart** (pehle options far-left tha).

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/windows/chart_window.py` | Splitter order: watchlist (0) \| options (1) \| container (2); sizes `[220, 56, 1004]`; docstring |
| `03_chart/chart/widgets/symbol_list_widget.py` | Stock row styling (palette-based QSS — koi hardcoded color nahi): `border: none`, item padding 3px 6px, subtle row separators (`palette(mid)` bottom border), selected row = rounded (4px) border + highlight background. Consistent compact rows. API/behavior unchanged |
| `03_chart/chart/widgets/watchlist_widget.py` | Header ke neeche + Sort row ke neeche `QFrame.HLine` separators (clear visual separation); fixed top (header/sort) vs sirf list scroll — pehle se aisa tha, ab documented + tested |
| `03_chart/chart/tests/test_options_panel.py` | Order test: watchlist → options → chart |
| `03_chart/chart/tests/test_watchlist_widget.py` | 3 naye tests: separator ordering, row styling (QSS content), sirf list scrolls (header/sort fixed) |

### Verification

- `pytest` = **213 passed** (210 + 3 naye)
- `ruff check` ✓ + `ruff format --check` ✓
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — Panel Order Fix (OPTIONS | WATCHLIST | CHART) + Placeholder Buttons

### Kya hua tha?

Task: sirf layout order badalna tha — Options panel far-left, watchlist uske turant right, chart sabse right. Options panel mein exactly 2 chhote icon buttons vertically (◉, ◇) — UI placeholders, koi functionality nahi: no click, no popup, no menu, no data.

### Decision

- **Splitter order** (`ChartWindow`): ab `options (0) | watchlist (1) | container/chart (2)` — pehle `watchlist | options` tha. Stretch 0,0,1; `setSizes([56, 220, 1004])`. Kuch aur nahi chhua — watchlist/chart/data/interactions untouched.
- **`OptionsPanel`** ab empty nahi: 2 placeholder `QToolButton` (glyphs `◉`/`◇`), `BUTTON_SIZE = 28`, vertically stacked (QVBoxLayout, top-aligned, `AlignHCenter`), panel `OPTIONS_WIDTH = 56` fixed. Buttons **disabled** (`setEnabled(False)`) — click physically impossible, koi connection/menu/popup nahi. Sirf visuals, functionality zero.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/options_panel.py` | 2 placeholder buttons (◉, ◇) vertically — disabled, no connections |
| `03_chart/chart/windows/chart_window.py` | Splitter order: options | watchlist | chart; sizes `[56, 220, 1004]`; docstring order fix |
| `03_chart/chart/tests/test_options_panel.py` | Order test (options→watchlist→chart), 2 placeholder buttons (exact glyphs, size, vertical stack, disabled/inert, no menu), baaki geometry tests wahi |

### Verification

- `pytest` = **210 passed** (208 + 2 naye)
- `ruff check` ✓ + `ruff format --check` ✓
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — Left Options Section (Empty Container Beside Chart)

### Kya hua tha?

Task: sirf layout badalna tha — chart ke LEFT side par ek narrow options column: `WATCHLIST | OPTIONS | CHART`. Sirf options section add karna tha — chart, candles, watchlist, timeframe, data, interactions — sab untouched. Empty container, koi buttons/functionality nahi.

### Decision

- **Naya `OptionsPanel`** (`03_chart/chart/widgets/options_panel.py`) — pure UI empty container, `OPTIONS_WIDTH = 56`, `setFixedWidth` (narrow + fixed; splitter user-resize nahi ho sakta). Koi layout, koi child widget nahi — options tooling (indicators/drawing) aage ke phases mein yahan aayega.
- **Layout**: `ChartWindow` splitter ab 3 items: watchlist (0) | options (1) | container(chart+toolbar) (2). Options stretch 0, container stretch 1; `setSizes([220, 56, 1004])`. Splitter handles hi clean separator hain (app mein koi QSS nahi — existing styling = plain widgets). Options column poori height tak jaata hai (toolbar row ke saath), chart se kabhi overlap nahi — pan/zoom chart ke andar hota hai, options bahar hai.
- Chart/window ctor signature: `ChartWindow(widget, watchlist, options, toolbar, bus, limit=None)` — bootstrap ab `OptionsPanel()` construct karke pass karta hai. Chart, watchlist, toolbar, events — koi change nahi.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/options_panel.py` | Naya `OptionsPanel` — empty fixed-width (56px) container |
| `03_chart/chart/windows/chart_window.py` | Splitter 3 items (watchlist \| options \| chart), `options` param + property, sizes `[220, 56, 1004]` |
| `00_app/app/bootstrap/bootstrap.py` | `OptionsPanel()` construct + ChartWindow ko pass |
| `03_chart/chart/__init__.py` | `OptionsPanel` export |
| `03_chart/chart/tests/test_options_panel.py` | Naya — 6 tests: order (watchlist→options→chart), fixed width, vertical span, no overlap, splitter sibling (pan/zoom se hila nahi sakta), empty container |
| `03_chart/chart/tests/test_chart_window.py` | Helper mein options param; splitter container ab index 2 (pehle 1) |

### Verification

- `pytest` = **208 passed** (202 + 6 naye)
- `ruff check` ✓ + `ruff format --check` ✓
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — Watchlist Panel (Header Row + Sort Row + Stock List)

### Kya hua tha?

Task: watchlist header layout `[Watchlist ▼] [+  Tool  ⋯]` ke saath, neeche `Symbol / Sort by ▼` row aur stock list. Repository mein watchlist koi nahi thi (sirf plain `SymbolListWidget` sidebar) — user ne naya feature banane ka approve kiya (no fake data).

### Decision

- **Naya `WatchlistWidget`** (`03_chart/chart/widgets/watchlist_widget.py`) — pure UI, `SymbolListWidget` ko compose karta hai (existing list + `symbol_selected` signal reused, duplicate nahi). Row 1: watchlist selector (name + dropdown arrow, InstantPopup), `+` (new watchlist), `↩` (reset chart view tool), `⋯` (more menu). Row 2: `Symbol` label + `Sort by ▼`. Row 3: stock list (stretch 1). Sab horizontally aligned, compact (QToolButton autoRaise, margins 4/2).
- **Real behaviors, koi fake data nahi**: watchlists session-local in-memory (`dict name → symbols`), seeded `"All Stocks"` = asli symbol universe (`SymbolsListed` se). `+` naya khali watchlist banata hai, `⋯` menu = New/Remove watchlist (All Stocks kabhi remove nahi). `Sort by ▼` asli sorting (A→Z / Z→A). `↩` emits `reset_requested` → `ChartWindow` existing `reset_view()` ko call karta hai (reuse, duplicate nahi).
- **Wiring**: `ChartWindow` ab `WatchlistWidget` leta hai (`sidebar` property → `watchlist`); `on_symbols_listed` → `set_symbols`; symbol click → `LoadSymbol`/`ListTimeframes` (waisa hi); `on_chart_ready` → `select_symbol`. `Bootstrap` ab `WatchlistWidget` banata hai. `SymbolListWidget` ab watchlist ke andar list hai (still exported, still used). Chart/market/data — kuch nahi chhua.
- `SymbolListWidget.__init__` parent type `QListWidget | None` → `QWidget | None` (pyright, composition ke liye).

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/watchlist_widget.py` | Naya `WatchlistWidget` — header/sort/list rows, watchlists in-memory, sort, `symbol_selected`/`reset_requested` signals, `symbols`/`watchlists`/`active_watchlist`/`current_symbol` properties |
| `03_chart/chart/windows/chart_window.py` | `sidebar` → `watchlist` param/property; `on_symbols_listed`/`on_chart_ready` watchlist par; `reset_requested → widget.reset_view` |
| `00_app/app/bootstrap/bootstrap.py` | `SymbolListWidget` → `WatchlistWidget` construct |
| `03_chart/chart/widgets/symbol_list_widget.py` | Parent type fix (`QWidget \| None`), docstring update |
| `03_chart/chart/__init__.py` | `WatchlistWidget` export |
| `03_chart/chart/tests/test_watchlist_widget.py` | Naya — 16 tests: header order/same-row, dropdown mode, sort row below, list population, click → signal, sort A→Z/Z→A + menu, add/remove/switch watchlists, more-menu enable state, reset tool signal |
| `03_chart/chart/tests/test_chart_window.py` | `sidebar` → `watchlist`; naya test: watchlist reset tool reuses chart reset_view |
| `00_app/app/tests/test_smoke.py` | `window.sidebar` → `window.watchlist` (symbols/current_symbol public API) |

### Verification

- `pytest` = **202 passed** (185 + 17 naye)
- `ruff check` ✓ + `ruff format --check` ✓
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — Initial Viewport Fix: Latest INITIAL_BARS (TradingView-Style)

### Kya hua tha?

Fresh chart lifecycle (naya symbol / first open) par viewport **poori history fit** karta tha (`_fit_all_count` — `ceil((total-0.5)/(1-RIGHT_MARGIN_FRACTION))`), isliye 10 saal ke candles ek screen pe squeeze ho jaate the. Timeframe change par previous viewport inherit hota tha. TradingView-style behavior chahiye: data poori load ho, par initial viewport sirf **latest INITIAL_BARS** dikhaye.

### Decision

- **Fresh lifecycle → latest `INITIAL_BARS` (150).** `set_model` mein naya `_initial_count(total)` = `max(MIN_VISIBLE_BARS, min(INITIAL_BARS, total))` — poori history loaded rehti hai, sirf visible window limited hai. `_fit_all_count` delete (dead code — ab kahin use nahi hota).
- **Fresh lifecycle = naya symbol YA timeframe change** (`same_series = same symbol AND same timeframe`). Timeframe change ab previous viewport inherit nahi karta — wahi latest `INITIAL_BARS` window. Pehla open bhi wahi (previous=None).
- **Same series reload (data growth / follow-latest) waisa hi** — follow-latest re-anchor / manual-pan shift preserved (existing tests intact). App mein koi refresh feature nahi hai; user-triggered loads = symbol click (naya symbol) ya timeframe click (timeframe change), dono ab fresh `INITIAL_BARS` se khulte hain.
- **`reset_view` (right-click menu / Alt+R) bhi latest `INITIAL_BARS`** — pehle fit-all karta tha. Pan/zoom/crosshair, fresh-chart isolation, context menu — sab untouched. Data kabhi delete nahi hota: `_first` > 0 par shift hota hai, pan/zoom-out se purane candles accessible.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/candle_chart_widget.py` | `set_model`: `same_series` check (symbol+timeframe); fresh → `_initial_count`; `_fit_all_count` → `_initial_count` (delete); `reset_view` → `_initial_count`; `ceil` import hata |
| `03_chart/chart/tests/test_chart_viewport.py` | `test_new_symbol_viewport_spans_entire_history` → `test_new_symbol_shows_latest_initial_bars`; `test_new_symbol_large_history_shows_first_candle` → `test_new_symbol_large_history_skips_first_candle`; `test_switch_symbol_resets_viewport` → `test_switch_symbol_resets_to_latest_initial_bars`; naye: `test_timeframe_change_resets_to_latest_initial_bars`, `test_same_series_reload_keeps_zoom_window`, `test_initial_view_reaches_older_candles_by_pan_and_zoom`; `test_drag_vertical_pans_price_keeps_span_and_zoom` ka `_first == 0` assumption fix (ab `first_before`) |
| `03_chart/chart/tests/test_chart_context_menu.py` | `test_action_trigger_resets_viewport`: reset ab latest `INITIAL_BARS` (`_anchor_first(500, 150)`), fit-all nahi |

### Verification

- `pytest` = **185 passed** (182 + 3 naye / 2 updated); chart 116, app+core+market 69
- `ruff check` ✓ + `ruff format --check` ✓ (1 file formatted)
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — Right-Click Context Menu (Reset Chart View)

### Kya hua tha?

Chart par right-click kuch nahi karta tha (default OS menu ke bajaye kuch nahi), aur viewport ko fresh-chart state (fit-all) par wapas lane ka koi ek-jagah command nahi tha — `_reset_price_scale` sirf price strip ka manual scale reset karta tha.

### Decision

- **Ek hi reset command — do trigger.** `CandleChartWidget.reset_view()` naya public method (viewport-only: `_first`/`_last` → `_initial_count` + `_anchor_first`, `_price_manual = None`, `_follow_latest = True`, crosshair clear, grid cache invalidate). Dono paths — menu click aur `Alt+R` — ek hi `QAction` (`↩ Reset chart view`, shortcut `QKeySequence(Alt+R)`) se `triggered → reset_view` chalte hain. Koi duplicate reset logic nahi.
- **QAction widget par add** (`addAction`) — default `WindowShortcut` context, isliye `Alt+R` poore main window mein active hai (focus kisi bhi child par ho). No new dependency, no focus-policy change.
- **Right-click press par menu** (`mousePressEvent` RightButton branch) — `QMenu` ek hi action ke saath, cursor position par `exec()` (Qt khud click-outside/Escape/selection par band karta hai, screen bounds clamp bhi Qt ka). `contextMenuEvent` override = default OS/Qt menu suppress.
- **Scope pakka: sirf widget UI.** Reset sirf viewport (pan + zoom + visible/logical range + price auto-fit) — data reload, symbol/timeframe/candles/indicators sab untouched. `set_model` fresh-chart lifecycle, pan/zoom/crosshair/touch — sab waisa hi.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/candle_chart_widget.py` | `QAction` `↩ Reset chart view` (Alt+R) + `addAction`; `reset_view()`; `mousePressEvent` right-button → `_show_context_menu`; `_context_menu`/`_show_context_menu`/`contextMenuEvent` (suppress default); class docstring interactions update |
| `03_chart/chart/tests/test_chart_context_menu.py` | Naya — 10 tests: action text/shortcut, single-action menu, contextMenuEvent suppressed, right-click wiring (menu stub ke saath — offscreen `QMenu.exec` auto-trigger quirk), crosshair/drag state untouched, action trigger → viewport reset, price auto-fit restore, model identity untouched, no-model noop |

### Verification

- `pytest` = **182 passed** (172 + 10 naye); chart 113, app+core+market 69
- `ruff check` ✓ + `ruff format --check` ✓ (1 file formatted)
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-13 — TradingView-Style Free Chart Panning

### Kya hua tha?

Chart drag sirf **horizontal** pan karta tha (`_pan_from_drag` sirf x use karta tha); vertical (price) pan nahi tha aur pointer capture nahi tha. User chahta tha TradingView jaisa free pan: press+hold karke chart ko left/right **aur** up/down le jaao, zoom change kiye bina.

### Decision

- **Ek hi existing system mein integrate kiya** — naya panning system nahi banaya. Existing `mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent` drag flow ko extend kiya.
- **Vertical pan = price range shift.** Chart ki vertical dimension price hai. Vertical drag `_price_range()` ko same span (zoom) rakh ke shift karta hai, existing `_price_manual` override ke through — span badalta nahi → zoom unchanged. Sirf jab user vertically drag karta hai tab `_price_manual` set hota hai; horizontal-only drag pe auto-fit wahi rehta hai (existing behaviour preserved).
- **Pointer capture.** `grabMouse()`/`releaseMouse()` — mouse press ke baad drag chart se bahar nikalne par bhi active rehta hai (web `setPointerCapture` ka Qt equivalent). Touch `TouchEnd`/`TouchCancel` pehle se hi state reset karta hai.
- **Touch:** 2-finger pan ab vertical bhi karta hai (`_pan_price_delta_px`) — horizontal + vertical dono. 1-finger crosshair aur pinch zoom wahi. Dedicated interactions (price-strip drag scaling, double-click reset) untouched.
- **Zoom unchanged:** time zoom `_first`/`_last` ke beech `count` fixed rehta hai; price zoom `span` fixed rehta hai. Siraf viewport position badalti hai. Candle data/OHLC/timeframe/indicator — kuch nahi chhua.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/widgets/candle_chart_widget.py` | `_drag_origin_y`/`_drag_price_low`/`_drag_price_high` state; `mousePressEvent` me `grabMouse()`; `mouseMoveEvent` me x+y dono; `_pan_from_drag(x, y)` vertical price pan; naya `_pan_price_delta_px(y)`; `mouseReleaseEvent` me `releaseMouse()`; 2-finger touch vertical pan |
| `03_chart/chart/tests/test_chart_viewport.py` | 4 naye tests: vertical pan (span/zoom unchanged), horizontal-only keeps auto price, diagonal pans both axes, touch 2-finger vertical pan |

### Verification

- `pytest` = **172 passed** (168 + 4 naye)
- `ruff check` + `ruff format --check` ✓ (2 files auto-formatted)
- `pyright` = **0 errors**
- `validate_structure.py` + `validate_imports.py` = PASSED

---

## 2026-08-10 — Release v1.1.0: Version Control Setup

### Kya hua?

Version control system setup hua: single source of truth + pehla proper semantic release tag.

### Decisions

- **Single source of truth**: `pyproject.toml` `[project] version` — version ab wahi ek jagah hai (0.1.0 stale tha aur git tags se mismatch karta tha). Runtime code koi version read nahi karta (koi `__version__` nahi), isliye koi duplication add nahi ki.
- **Version**: Phase 5A–5C ka kaam (timeframe system + UI, symbol list, crosshair + overlay renderers, load-all fix) foundation ke upar **naya feature** hai, breaking nahi → MINOR bump → **v1.1.0**. Previous: `v1.0-architecture` tag (1.0.0).
- **Editable install fix**: environment ab `Downloads\vayren-core` (purana path) ki jagah Desktop wale repo ko point karta hai — `pip install -e ".[dev]"` se.

### Verification

- Formatting: 4 files auto-formatted (test_chart_viewport.py, test_probe_qt.py, candle_chart_widget.py, chart_window.py).
- Full gate: ruff ✓, pyright 0 errors ✓, pytest **168 passed** ✓, structure + import validators ✓.
- Git: commit `v1.1.0` tag `v1.1.0`, origin/main pe push.

## 2026-08-07 — Phase 5B Fix: Toolbar Layout Bug (Blank Space Above Chart)

### Kya hua tha?

Timeframe toolbar add karne ke baad chart neeche dhakal gaya tha — toolbar aur chart ke beech ek bada blank white area aata tha. Root cause: `ChartWindow` ke `QVBoxLayout` mein toolbar aur chart widget dono ka vertical policy `Preferred` tha aur **koi stretch factor nahi tha** — Qt ne extra vertical space dono mein barabar baant diya (toolbar 28px hint ke bajaye 380px ho gaya), chart ko neeche push karke.

### Decision

- **`ChartWindow`**: `layout.setStretchFactor(toolbar, 0)` + `layout.setStretchFactor(widget, 1)` — toolbar apni minimum height pe rehta hai, chart baaki sab vertical space leta hai.
- Koi EventBus, Market, SQLite, architecture change nahi — sirf `03_chart/chart/windows/chart_window.py`.
- Koi hardcoded height/margin/spacer nahi — pure Qt stretch.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/windows/chart_window.py` | `setStretchFactor(toolbar, 0)` + `setStretchFactor(widget, 1)` |
| `03_chart/chart/tests/test_chart_window.py` | 2 naye regression tests: toolbar min-height + chart fills remaining space; resize (1200x760 / 1200x1500 / 900x500) pe koi gap nahi |

### Verification

- Geometry probe: 1280x760 → toolbar h=28, chart y=28 h=732, gap=0; 2000x3000 → gap=0. Har size pe chart pure vertical space bharata hai.
- pytest 00_app + 02_market + 03_chart = **162 passed** (2 naye).
- ruff ✓, pyright 0 errors ✓.

## 2026-08-07 — Phase 5C: Load-All Fix (Poori History Dikhana)

### Kya hua tha?

Naya symbol click karne par chart sirf latest **5000 rows** dikhata tha — TCS jaisi stock ki asli SQLite history (61,676 rows, 2016-06-09 → 2026-06-10) truncate ho jaati thi. User ko poora history chahiye: `limit` default ab `None` = "poori available history", explicit `--limit N` hi cap karta hai. Chain event → DB fetch → repository → loader → window → widget ke har level pe `limit: int | None` pass hua.

### Decision

- **Events**: `LoadSymbol.limit` + `TimeframeChanged.limit` ab default `None` (`int | None`) — docstring "None = entire available history".
- **DB layers**: `SqliteCandleDatabase.fetch_candles(symbol, limit)` aur `OhlcvCandleDatabase.fetch_candles(symbol, limit)` — `limit is None` pe **ascending full table scan** (koi DESC + LIMIT subquery nahi). `LIMIT ?` pe `None` bind nahi kiya — SQLite `datatype mismatch` error deta hai (empirically verified).
- **Repositories**: `CandleRepository.get_candles`/`get_candles_timeframe` + `SymbolRepository` same signatures — `None` = full history; aggregation (`get_candles_timeframe`) fir full rows aggregate karke full list return karta hai (koi window-drop nahi).
- **App/window**: `Bootstrap.__init__(data_dir, limit=None)`, `ChartWindow.__init__(..., limit=None)` (pehle `5000` default tha). App `--limit` default `None`.
- **Widget initial viewport**: naya symbol → `_fit_all_count(total)` — poore dataset pe fit (`ceil((total-0.5)/(1-RIGHT_MARGIN_FRACTION))`, min `MIN_VISIBLE_BARS`); first bar index 0 par, latest bar right margin pe. Same-symbol reload → follow-latest `INITIAL_BARS` trailing window. Naya `_log_data_range(model)` log karta hai first/last ts + total + visible window.

### Kya kiya?

| File | Change |
|---|---|
| `02_market/market/events/load_symbol.py`, `timeframe_changed.py` | `limit: int \| None = None` default |
| `02_market/market/database/sqlite.py`, `ohlcv.py` | `fetch_candles(symbol, limit)` — `None` = ascending full scan, koi `LIMIT ?` NULL bind nahi |
| `02_market/market/repository/candle_repository.py`, `symbol_repository.py` | `get_candles`/`get_candles_timeframe` `int \| None` — `None` pe full history / full aggregation |
| `00_app/app/__init__.py`, `app/bootstrap/bootstrap.py`, `03_chart/chart/windows/chart_window.py` | `limit=None` plumbing (default cap `5000` hata diya agar tha) |
| `03_chart/chart/widgets/candle_chart_widget.py` | `_fit_all_count(total)` helper + initial fit-whole-dataset view (first index 0) + `_log_data_range` |
| `02_market/market/tests/{test_loader,test_repository,test_ohlcv}.py` | Naye tests: `limit=None` → saari history |
| `00_app/app/tests/test_main.py` | `--limit` default `None` |
| `03_chart/chart/tests/test_chart_viewport.py` | `test_new_symbol_viewport_spans_entire_history` (500) + `test_new_symbol_large_history_shows_first_candle` (10000) + `_trailing` helper |
| `03_chart/chart/tests/test_overlay_renderer.py` | Un-ended `QPainter` ko `painter.end()` deke shutdown abort fix |
| `03_chart/chart/tests/test_probe_qt.py` | `_paint` return annotation fix (pyright 0 errors) |

### Consequence

- Naya symbol poora 10 saal ka chart kholta hai — pehli bar index 0, latest bar right margin pe.
- Timeframe change `limit=None` pe full aggregation (saare raw rows se).
- Test count: **160 pass** (ruff ✓, pyright 0 errors ✓; test_suite 160 pass).

### Verification

- `pytest 00_app 02_market 03_chart` = **160 passed** (offescreen).
- ruff ✓, format ✓, pyright 0 errors ✓.

## 2026-08-06 — Phase 5A Bug Fix: Bottom Time Label Missing Time

### Kya hua tha?

Bottom crosshair label sirf date dikhata tha — time (HH:MM) missing tha. Root cause: `LabelRenderer.paint_centered` ne multi-line text ko single-line height ke saath size kiya, isliye doosri line (time) crop ho jaati thi. Additionally, format timeframe-aware nahi tha (daily/weekly/monthly ke liye alag format chahiye).

### Decision

- **`LabelRenderer`**: multi-line text (newline-separated) ko properly support karo — `text_h = fm.height() * line_count` + `drawText` with `AlignLeft | AlignTop` so both lines render inside the label rect.
- **`OverlayRenderer._format_timestamp`**: timeframe parameter add kiya — intraday `m`/`h` → `Tue 04 Aug '26\n13:00` (date + time), daily `d` → `Tue 04 Aug '26` (date only), weekly `w` → `Week 32\n2026`, monthly `mo` → `Aug 2026`. Exact timestamp SQLite se read hota hai, koi estimation nahi.
- **`paint_time`**: now takes `timeframe` arg; widget passes `model.timeframe`.

### Kya kiya?

| File | Change |
|---|---|
| `chart/renderer/label_renderer.py` | Multi-line support in all paint methods (`paint`, `paint_left`, `paint_centered`); `drawText` with `AlignLeft | AlignTop` |
| `chart/renderer/overlay_renderer.py` | `paint_time` takes `timeframe`; `_format_timestamp` timeframe-aware (intraday/daily/weekly/monthly) |
| `chart/widgets/candle_chart_widget.py` | Pass `model.timeframe` to `paint_time` |
| `chart/tests/test_overlay_renderer.py` | Updated format tests for timeframe param; added daily/weekly/monthly/intraday tests |
| `chart/tests/test_overlay_integration.py` | Updated `paint_time` mock signature |

### Verification

- ruff ✅, format ✅, pyright 0 errors ✅, structure ✅, imports ✅, **117 tests pass** ✅

### Kya hua tha?

Phase 5A ke labels thhe lekin alignment loose thi — bottom time label right-edge pe fixa tha, right price label fixed Y pe, OHLC neeche ki strip mein tha, symbol info double-line ho sakta tha. UI polish pass: **sirf visual alignment** — EventBus/Market/SQLite/Bootstrap architecture koi bhi change nahi.

### Decision

- **Bottom time label** → `LabelRenderer.paint_centered`: horizontally centered at crosshair X, clamped to axis strip center. Perfectly follows the vertical crosshair line.
- **Right price label** → vertically centered at crosshair Y (clamped to chart rect top/bottom). Exactly aligns with the horizontal crosshair line.
- **OHLC readout** → moved into the top info bar (TradingView style): single line `O H L C +change (+pct%)` placed immediately right of the symbol info label, sharing the same top bar row.
- **Symbol info** → single clean line `SYMBOL • timeframe • exchange` at top-left; OHLC starts at `symbol_rect.right() + 6px` so no overlap.
- **All labels** constrained within `chart_rect` or `axis_rect`; `LabelRenderer` clamps Y positions to prevent clipping/overflow.
- Pens/brushes still class-level cached; overlays only paint when crosshair active (no extra repaint overhead).

### Kya kiya?

| File | Change |
|---|---|
| `chart/renderer/label_renderer.py` | Added `paint_centered` (horizontally centered label); refactored `_draw_label` shared helper; removed unused `text_width` |
| `chart/renderer/overlay_renderer.py` | `paint_symbol_info` returns QRect (for OHLC placement); `paint_ohlc` now single-line in top bar with `left_margin` param; `paint_price` takes crosshair_y + chart_rect (vertical centering); `paint_time` takes crosshair_x (horizontal centering) |
| `chart/widgets/candle_chart_widget.py` | `_paint_overlays` passes crosshair_pos to price/time; OHLC placed at `symbol_rect.right()`; uses top_bar for both symbol + OHLC |
| `chart/tests/test_overlay_integration.py` | Updated all mocks for new signatures; added `test_price_label_y_aligns_with_crosshair`, `test_time_label_x_centers_on_crosshair`, `test_ohlc_positioned_in_top_bar` |
| `chart/tests/test_overlay_renderer.py` | Updated smoke tests for new signatures |

### Verification

- ruff ✅, format ✅, pyright 0 errors ✅, structure ✅, imports ✅, **113 tests pass** ✅
# Development Log — Kya Kab Hua

**Naya entry hamesha upar likho.**

## 2026-08-06 — Phase 5B: Market Timeframes (Detection + Aggregation)

### Kya hua tha?

Chart pe sirf base data (15m) dikhta tha. Ab market engine asli SQLite se **available timeframes detect** karta hai (kabhi hardcode nahi) aur user ke timeframe select par real aggregation karke `DataLoaded` publish karta hai. Scope pakka: **sirf Market + SQLite** — Chart Renderer/Widget/Crosshair/Labels/OHLC sab untouched; koi app reload nahi, sirf data reload.

### Decision

- **Detection (`market/timeframe/`)** — naya pure domain: canonical `TIMEFRAME_LADDER` (candidates, hardcode nahi) + `available_timeframes(base)` = sirf wo entries jo DB ki base-duration ke whole multiple hain ("Only show timeframes that exist"). Hamesha SQLite se detect — koi cached list nahi.
- **Base duration** = histogram (mode) of bar-to-bar deltas from actual rows (`detect_bar_duration`).
- **Aggregation (`aggregate.py`)** — `Rows` ko bucket: intraday = **session-anchored** (session start = mode of first-bar-per-day, partial windows se robust), daily = local midnight, weekly = Monday midnight. OHLCV sab raw rows se: open = first, high = max, low = min, close = last, volume = sum — zero fabrication.
- **Sharding bug + fix**: `_detect_session_start` pehle mode-of-all-rows tha — chhote (partial) windows pe galat session (10:00) nikal aata. Fix: full sample (5000) se `detect_session_start` (first-bar-per-day mode) + windowed fetch (limit+1)×ratio → aggregate → last `limit`. Sirf 2 queries per timeframe change.
- Loading window = `(limit+1) × ratio` raw rows; oldest partial bucket naturally drop hoke `bars[-limit:]` milta hai.
- Naye events: `TIMEFRAME_CHANGED` (request, class `TimeframeChanged`), `ListTimeframes` (request), `TimeframesListed` (result) — exact `ListSymbols`/`SymbolsListed` pattern.
- Subscriptions sirf bootstrap mein (architecture same): `TimeframeChanged` → `MarketDataLoader.on_timeframe_changed`; `ListTimeframes` → naya `TimeframeListLoader.on_list_timeframes`.

### Kya kiya?

| File | Change |
|---|---|
| `02_market/market/timeframe/__init__.py` | Naya package |
| `02_market/market/timeframe/timeframe.py` | Ladder + conversion (`1m..1W` + generated `90m`/`2D`) + `available_timeframes` |
| `02_market/market/timeframe/aggregate.py` | `detect_bar_duration` + `detect_session_start` + `aggregate_bars` |
| `02_market/market/events/timeframe_changed.py` | Naya `TimeframeChanged(symbol, timeframe, limit=5000)` |
| `02_market/market/events/list_timeframes.py` | Naya `ListTimeframes(symbol)` |
| `02_market/market/events/timeframes_listed.py` | Naya `TimeframesListed(symbol, timeframes)` |
| `02_market/market/repository/candle_repository.py` | `detect_bar_duration`, `detect_timeframes`, `get_candles_timeframe` (sample + window fetch) |
| `02_market/market/repository/symbol_repository.py` | `detect_timeframes`, `get_candles_timeframe` (per-stock file open) |
| `02_market/market/loader/market_data_loader.py` | `on_timeframe_changed` → aggregation → `DataLoaded` |
| `02_market/market/loader/timeframe_list_loader.py` | Naya `TimeframeListLoader` → `TimeframesListed` |
| `02_market/market/events/__init__.py`, `loader/__init__.py`, `market/__init__.py` | Exports |
| `00_app/app/bootstrap/bootstrap.py` | 2 naye subscriptions + `timeframe_list_loader` service (sirf wiring) |
| `00_app/app/tests/test_smoke.py` | Service list mein `timeframe_list_loader` |
| `02_market/market/tests/conftest.py` | `seed_ohlcv_intraday_database` helper + fixture |
| `02_market/market/tests/test_timeframe.py` | 12 tests (ladder + availability) |
| `02_market/market/tests/test_timeframe_repository.py` | 12 tests (detection + aggregation OHLCV exactness) |
| `02_market/market/tests/test_timeframe_loader.py` | 4 tests (event flows + failure → nothing) |

### Consequence

- `TIMEFRAME_CHANGED → Market loader → SQLite → DATA_LOADED → Chart updated` — chart pe koi line nahi badli; `DataLoaded` wahi old flow se chart ko update karta hai.
- Real `AMBUJACEM.db` (15m base): detect hota hai `('15m','30m','45m','1h','2h','4h','1D','1W')` — `1m/3m/5m` sahi excluded.
- Aggregated OHLCV raw rows ke SQL merge ke **counter-exact** (14:45 bar: 417.7/418.55/416.6/417.3/607720).

### Verification

- ruff ✓ format ✓ pyright 0 errors ✓ **110 tests pass** (28 naye) ✓ structure + import validators ✓
- Live boot (offscreen): `ListTimeframes` → `TimeframesListed` (8 timeframes); `TimeframeChanged "1D"` → **2475 daily bars** (2016-06-09 → 2026-06-05); `TimeframeChanged "30m"` → 5000 bars, chart widget model ab `('AMBUJACEM', 5000 bars, '30m', ...)` — chart bina touch update ✅

## 2026-08-06 — Phase 5A: Chart UI Labels + Snapping Crosshair

### Kya hua tha?

Crosshair sirf lines dikhata tha — koi candle info, price, timestamp, ya symbol details nahi. Ab crosshair snaps to nearest candle and four floating UI labels follow it: bottom time label (exact timestamp), right price label (current crosshair price), symbol info bar (SYMBOL • timeframe • exchange), and OHLC bar (Open/High/Low/Close + change + %). Scope: **sirf Chart UI** — EventBus, Market Engine, SQLite, Repository, Database, Loader, Bootstrap sab untouched.

### Decision

- **`CrosshairValue`** (model) — frozen dataclass: bar_index, price, timestamp, open, high, low, close. Widget computes it; renderers only consume.
- **`infer_timeframe`** (models/timeframe.py) — pure function: median bar-to-bar gap → "30m", "1h", "1d" etc. ChartEngine populates ChartModel.timeframe from this.
- **`ChartModel`** extended with `timeframe: str` and `exchange: str` — engine populates from bar spacing + default "NSE".
- **`LabelRenderer`** — stateless framed label painter (semi-transparent bg, cached pen/brush). Shared by all overlay labels. Zero paint-time allocation.
- **`OverlayRenderer`** — stateless painter of 4 labels: `paint_symbol_info`, `paint_ohlc`, `paint_price`, `paint_time`. Timestamp formatted as two-line `Tue 04 Aug '26\n10:15`.
- **Crosshair snap** — mouse X → nearest candle slot; vertical crosshair line snaps to that candle's center; horizontal line shows interpolated price at mouse Y; bottom time label + OHLC bar always follow the selected candle.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/models/chart_model.py` | Added `timeframe` + `exchange` fields |
| `03_chart/chart/models/crosshair_value.py` | Naya — frozen CrosshairValue dataclass |
| `03_chart/chart/models/timeframe.py` | Naya — `infer_timeframe()` from median gap |
| `03_chart/chart/models/__init__.py` | Export CrosshairValue + infer_timeframe |
| `03_chart/chart/engine/chart_engine.py` | Populate timeframe (infer) + exchange (default NSE) |
| `03_chart/chart/renderer/label_renderer.py` | Naya — stateless framed label painter |
| `03_chart/chart/renderer/overlay_renderer.py` | Naya — 4 overlay label painters + timestamp formatter |
| `03_chart/chart/renderer/__init__.py` + `chart/__init__.py` | Export LabelRenderer, OverlayRenderer, CrosshairValue, infer_timeframe |
| `03_chart/chart/widgets/candle_chart_widget.py` | Snap crosshair to nearest candle, compute CrosshairValue, paint overlays after candles |
| `03_chart/chart/tests/test_crosshair_renderer.py` | Updated _model() for new ChartModel fields; plot_rect → chart_rect |
| `03_chart/chart/tests/test_crosshair_value.py` | Naya — 2 tests |
| `03_chart/chart/tests/test_timeframe.py` | Naya — 7 tests |
| `03_chart/chart/tests/test_overlay_renderer.py` | Naya — 9 tests |
| `03_chart/chart/tests/test_overlay_integration.py` | Naya — 7 tests |

### Performance

- All label pens/brushes cached at class level — zero allocation per paint call.
- Crosshair snap is O(1) arithmetic (no search).
- Overlay labels only paint when crosshair is active (no crosshair → no overlay paint).
- `_clear_crosshair` only calls `update()` when state actually changed.
- 60 FPS target: paint path unchanged for candles/time-axis; overlays add <1ms.

### Verification

- ruff ok, format ok, pyright 0 errors, **110 tests pass** (25 naye), structure + import validators ✅

### Kya hua tha?

Chart par mouse le jaane se kuch nahi dikhta tha. Ab TradingView-style crosshair hai — mouse position par ek vertical (poori chart height) + ek horizontal (poori width) line, jo smooth follow karti hai aur mouse nikalte hi chhup jaati hai. Scope pakka tha: **sirf crosshair** — EventBus, Market engine, SQLite, candle rendering sab untouched.

### Decision

- Naya **`CrosshairRenderer`** — alag class, `CandleRenderer` mein kuch nahi ghused. Pen class-level cached hai — paintEvent mein **zero allocation**.
- Color: `RGBA(180,180,180,120)` semi-transparent gray, 1px, crisp (AA off — lines, TextAntialiasing waale text ko nahi chhute).
- Widget: `setMouseTracking(True)` + `_crosshair_pos` state. `mouseMoveEvent` pe update (pan logic waise hi chalta hai), `leaveEvent` pe hide.
- Render order: candles → time axis → crosshair (always last, requirement 4).
- Lines plot area tak limit hain — axis strip (labels) mein mouse jaye to crosshair hidden (TradingView jaisa).
- Redraw sirf `update()` — koi reload nahi, 60 FPS safe.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/renderer/crosshair_renderer.py` | Naya! Stateless `CrosshairRenderer` — cached pen, `paint(painter, position, plot_rect)` |
| `03_chart/chart/widgets/candle_chart_widget.py` | Mouse tracking, `_crosshair_pos` state, move/leave handlers, paintEvent mein render (candles ke baad) |
| `03_chart/chart/renderer/__init__.py` + `chart/__init__.py` | `CrosshairRenderer` export |
| `03_chart/chart/tests/test_crosshair_renderer.py` | Naya! 7 tests |

### Testing battles (worth remembering)

- Widget pe `grab()` offscreen platform mein **unreliable** — hidden widget pe crash (0xC0000005), shown widget pe flaky (partial transparent image), `render()` pe bhi transparent. Sirf event-loop + top-level window grab stable. Isliye widget tests **pixel-free**: state transitions + `CrosshairRenderer.paint` monkeypatch (recording args). Renderer pixel-exact tests QImage pe hain (449 → 448 = intersection double-blend wala 1 pixel).
- Pan test pehle fail: 200 bars mein right edge par pan space nahi tha (clamp), aur `_mouse_event` press mein button arg galat tha (NoButton) — dono fixed.
- `QWidget` bina `QApplication` create karo to hard crash — widget tests mein `_app()` helper zaroori.

### Verification

- ruff ok, format ok, pyright 0 errors, **59 tests pass** (7 naye), structure + import validators ✅
- Standalone window-grab pixel check: 795 diffs = 296 (plot height) + 500 (width) − 1, **sab exactly crosshair lines par**, leave ke baad pixel-perfect baseline
- Live boot `D:\ZerodhaTradingData` (AMBUJACEM): crosshair (400,150) → (100,260) follow, leave → hidden, pan intact ✅

## 2026-08-06 — Phase 3: TradingView-style Time Axis

### Kya hua tha?

Chart ka X-axis (time axis) sirf khali strip thi. Ab TradingView style adaptive date/time labels aaye — zoom ke hisaab se format aur tick spacing automatically badalti hai. Scope pehle se pakka tha: **sirf X-axis**, EventBus/Market/Chart engine/database kuch nahi chhuna.

### Decision

- Naya **`TimeAxisRenderer`** class — stateless QPainter, `CandleRenderer` jaisa style. `CandleRenderer` ke andar kabhi nahi ghuse (single responsibility).
- Widget ne 24px strip reserve ki (chart + volume ke neeche) — `_chart_rects()` ab 3 rects deta hai.
- Tick step = "sabse chhota step jiske spacing >= 96px" — 1/2/5×10^k calendar-friendly ladder (15min, 1h, 1d, 1w, 1M, 1Y...).
- Ticks **calendar-aligned**: intraday = local clock grid, daily = midnight, weekly = Monday(simplified: 7d), monthly = month start, yearly = Jan 1.
- Format ladder zoom se: `10:15` → `10 Apr` → `Apr` → `Apr 2024` → `2024`.
- Labels kabhi overlap nahi hote: pixel-gap guard (min 96px ya label width) + gridline/label at tick center.
- Perf: paint mein sirf ~2 timestamp parses + binary search per tick (O(log n)), bahar ke lakhon candles touch nahi hote — 60 FPS safe.

### Kya kiya?

| File | Change |
|---|---|
| `03_chart/chart/renderer/time_axis_renderer.py` | Naya! Stateless `TimeAxisRenderer` — `select_step()`, `format_for_step()`, `tick_times()`, `paint()` (+ `_nearest_bar` binary search) |
| `03_chart/chart/widgets/candle_chart_widget.py` | `TIME_AXIS_HEIGHT=24`; `_chart_rects()` → 3 rects; paintEvent mein axis render; wheel/drag unpack fix |
| `03_chart/chart/renderer/__init__.py` | `TimeAxisRenderer` export |
| `03_chart/chart/__init__.py` | `TimeAxisRenderer` export |
| `03_chart/chart/tests/test_time_axis_renderer.py` | Naya! 11 tests |

### Details worth remembering

- First bug: sub-day ticks epoch (UTC) aligned the gaye — IST (+5:30) pe ugly times (08:30, 11:30...) ban gaye aur gaps 90px tak gir gaye. Fix: **local-clock alignment** (`seconds_since_midnight % step`).
- Second bug: antialiased 1px lines half-pixel pe — gridlines blend hoke mil gaye. Fix: shapes ke liye `Antialiasing=False` (crisp 1px), text ke liye `TextAntialiasing=True` — 0 blended pixels.
- Test expectation: 3-day step weekly tier mein hain (`%b`), 2-day tak daily (`%d %b`).

### Verification

- ruff ok, format ok, pyright 0 errors, **52 tests pass** (11 naye), structure + import validators ✅
- Live boot `D:\ZerodhaTradingData` (offscreen pixel census): labels render (792 text px), 6 crisp gridlines at 169-176px spacing (>= 96 floor), 0 blended pixels

## 2026-08-06 — Phase 2: Real Zerodha Databases + Stock Sidebar

### Kya hua tha?

Phase 1 sample DB (`data/vayren.db`, SPY) se chalta tha — ek file, ek symbol. Ab asli data aaya:

| Asli data | Detail |
|---|---|
| Location | `D:\ZerodhaTradingData` — 527 SQLite files, har stock ka apna DB |
| Schema | Table `ohlcv` (`candle_time` PK, open, high, low, close, volume) — `candles` table nahi, `symbol` column nahi |
| Requirement | Startup pe folder scan → sidebar mein stocks → click → load → chart. Koi preload nahi |

### Decision

Market engine (`MarketDataLoader`) aur Chart engine (`ChartEngine`) **rewrite nahi kiye** — contract vahi rakha, nayi cheezein add ki:

- Ek naya DB adapter jo asli schema padhta hai — `CandleRepository` vahi mapping karta hai (row → Bar)
- Ek naya repository jo per-stock file discover/load karta hai
- Ek naya event pair: `ListSymbols` (request) / `SymbolsListed` (result)
- Sidebar = naya widget (`SymbolListWidget`), window mein splitter layout

### Kya kiya?

| File | Change |
|---|---|
| `02_market/market/database/ohlcv.py` | Naya `OhlcvCandleDatabase` — asli Zerodha schema (`ohlcv`/`candle_time`) |
| `02_market/market/repository/symbol_repository.py` | Naya `SymbolRepository` — `list_symbols()`, `get_candles()` (per-stock file) |
| `02_market/market/events/list_symbols.py` | Naya `ListSymbols` request event |
| `02_market/market/events/symbols_listed.py` | Naya `SymbolsListed` result event |
| `02_market/market/loader/symbol_list_loader.py` | Naya `SymbolListLoader` — scan → publish |
| `02_market/market/repository/candle_repository.py` | Type widening: `SqliteCandleDatabase \| OhlcvCandleDatabase` |
| `02_market/market/loader/market_data_loader.py` | Type widening: `CandleRepository \| SymbolRepository` |
| `03_chart/chart/widgets/symbol_list_widget.py` | Naya sidebar widget (Qt signal `symbol_selected`) |
| `03_chart/chart/windows/chart_window.py` | Splitter layout (sidebar + chart), title `VAYREN — SYMBOL`, click → `LoadSymbol` |
| `00_app/app/bootstrap/bootstrap.py` | `data_dir` based; 7 subscriptions (3 naye); window show at start |
| `00_app/app/lifecycle/lifecycle.py` | `AppStarted` → `ListSymbols` (preload hata diya) |
| `00_app/app/__init__.py` | `--symbol`/`--db` hata; `--data-dir` (default `D:\ZerodhaTradingData`) |

### Naya event flow

```
AppStarted → ListSymbols → SymbolsListed → (sidebar filled)
click AMBUJACEM → LoadSymbol → DataLoaded → ChartReady → VAYREN — AMBUJACEM
click BPCL      → LoadSymbol → DataLoaded → ChartReady → VAYREN — BPCL
```

### Consequence

- Har click pe sirf usi stock ka DB khulta hai — koi preload nahi, koi memory waste nahi
- Chart engine, EventBus, folder structure — sab untouched
- Purana `SqliteCandleDatabase` (sample schema) apni jagah hai — tests usi pe chalte hain

### Verification

- `make check` equivalent: ruff ✓, pyright 0 errors ✓, **41 tests pass** ✓, structure + import validators ✓
- Live boot `D:\ZerodhaTradingData`: 527 symbols listed; click AMBUJACEM → `VAYREN — AMBUJACEM`; click BPCL → `VAYREN — BPCL`; wapas AMBUJACEM ✓

---

## 2026-08-06 — Repository Refactor: Charting Platform Ki Neev

### Kya hua tha?

Repository ek 13-chapter trading scaffold thi — signals, strategies, risk, execution, portfolio... sab kuch tha, par:

| Problem | Detail |
|---|---|
| GUI nahi tha | Na Qt, na tkinter — kuch nahi |
| Chart nahi tha | Koi rendering code nahi |
| SQLite nahi tha | Na file, na schema, na code |
| Scope bahut bada | 13 modules, koi bhi complete nahi |

### Decision

Naya architecture — **numbering = startup flow**:

```
00_app → 01_core → 02_market → 03_chart
```

Future modules fixed: `04_indicator … 14_plugin`.

Permanent rules bani (`90_brain/project_rules.md`): event-driven only, one module one responsibility, no circular dependencies, no placeholder code.

### Kya kiya?

| Kaam | Detail |
|---|---|
| Purane chapters archive | `git mv` → `99_archive/` (history safe) |
| `EventBus` nikala | `02_platform` → `01_core/core/event_bus/` (verbatim) |
| Logger nikala | `lib/logging` → `01_core/core/logger/` |
| `Registry` nikala | → `01_core/core/registry/` (instances ke liye generalize kiya) |
| `Bar` nikala | → `02_market/market/models/bar.py` (verbatim) |
| `02_market` banaya | `SqliteCandleDatabase` → `CandleRepository` → `MarketDataLoader`; events `LoadSymbol`/`DataLoaded` |
| `03_chart` banaya | `ChartModel`, `ChartEngine`, `CandleRenderer`, `CandleChartWidget` (zoom/pan/resize), `ChartWindow`; events `ChartReady`/`WindowRendered` |
| `00_app` banaya | `App` (args, QApplication, Qt loop), `Bootstrap` (sirf subscription site), `AppLifecycle` |
| Tooling | `pyproject.toml` trim → sirf PySide6 + dev tools; `Makefile`; validators; AGENTS.md/README |

### Consequence

- Synchronous bus → poora chain `Bootstrap.start()` mein — deterministic, test easy
- Wiring ek jagah → event topology ek file mein dikhti hai
- Archive packaging/pyright/pytest/validators se bahar

### Verification

Live boot `python -m app --symbol SPY --db data/vayren.db --limit 1200` (sample DB, 1200 bars):

```
Application started → Loaded 1200 candles for SPY
→ Chart ready for SPY → Chart window shown → Window rendered
```

Qt loop running raha. Ek fix laggi: `__main__.py` ko `sys.argv[1:]` dena padta hai (`python -m app` argv[0] = module path).

**Compliance:** `make check` green — ruff, pyright (0 errors), 26 tests pass, structure + import validators pass.

---

## Template — Naye Entry Ke Liye

```markdown
## YYYY-MM-DD — <Kya hua>

### Kya hua tha?        → context
### Decision            → kya decide hua
### Kya kiya?           → changes ki list
### Consequence         → isse kya asar hua
### Verification        → kaise check kiya
```

> Har session ka record. 6 mahine baad koi padhe — sab samajh aaye.
