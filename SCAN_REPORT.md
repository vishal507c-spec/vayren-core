# VAYREN — Pura Repo Scan Report (line-by-line audit)

**Scan date:** 2026-09-11
**Scope:** 524 Python files (~5,366 prod LOC + ~30k test LOC), Rust workspace (2 crates), 90_brain docs, scripts, web/
**Method:** full file-tree walk → per-module source read → live validators + targeted test runs

---

## 0. Executive Summary

| Area | Verdict |
|---|---|
| Architecture (event-driven, layering) | **Solid.** Validators pass, forbidden imports absent |
| Language ownership (Rust/Python/Slint) | **Enforced.** 531 files checked, PASSED |
| Test suite | **1,632 tests, all pass in ONE process** (`1632 passed in 177s`) — full suite now completes after the R1 fix |
| Safety (risk/execution/live gates) | **Strong.** Fail-closed everywhere, PAPER default, 5 gates |
| Secrets | **Clean.** No credentials in git; redacted by construction |
| Ruff | `ruff format --check` ✅ · `ruff check` ✅ |
| Biggest risks | **R2 2851-line bootstrap**, **R3 `exec()` of user strategy code**, **R11 hardcoded `D:\` paths** — R1 now fixed |
| Uncommitted work | **361 files, +6,619/−9,397** on `main` — uncommitted |

---

## 1. Chapter-by-Chapter Walkthrough

### `01_core` — Foundation (3,200 LOC, 42 files, 284 tests)

**Owns:** EventBus, Event base, logger, Registry, contracts, SystemModel, AI boundary. **Depends on: nothing.**

| File | What it does | Used by | Notes |
|---|---|---|---|
| `event_bus/event_bus.py` | Synchronous publish/subscribe, `type(event)` exact-match dispatch | every module | ✅ **Fixed in working copy**: `handler.__name__` → `getattr(handler, "__name__", repr(handler))` — previously a `lambda` subscriber would raise `AttributeError` *inside* the except-block. Good catch |
| `events/event.py` | Marker base class. **Plain class, not a dataclass** | all events | Accepted: subclasses are frozen dataclasses |
| `events/app_started.py` | `AppStarted` frozen dataclass | bootstrap → lifecycle | clean |
| `logger/setup.py` | `configure_logging()` + `get_logger()` | app entry | clean |
| `registry/registry.py` | Name→object map. **Raises on duplicate register** | bootstrap | fail-closed |
| `registry/component_registry.py` | Manifest-validated registration + capability discovery | bootstrap, tests | Validates declared↔provided set equality both directions — excellent |
| `registry/capability_registry.py` | capability → many implementations; rejects duplicate component | component_registry | clean |
| `contracts/*.py` | ComponentId/Version/Metadata, CapabilityId/Contract, Manifest + `validate_manifest` | system/, ai/ | Validation is thorough (self-dep, overlap, dup capability all caught) |
| `native/loader.py` | ctypes bridge to Rust cdylib; **ABI + state-count handshake, no Python fallback** | market/native_aggregate, execution/native_order_state | Exemplary fail-closed interop |
| `system/*.py` | ComponentGraph, CapabilityGraph, EventGraph, DataFlow, Workflow, `analyze_change`, snapshot | AI layer | Pure model, no side effects |
| `ai/*.py` | Intent→Plan→Validator→Simulation→Sandbox. **No LLM call, no runtime mutation** | nothing at runtime | Correctly isolated; `Sandbox.deploy()` records only |

**Risks:** none material. `Event` not being a dataclass is intentional.

---

### `02_data` — Historical download (write path) (7,071 LOC, 51 files, 191 tests)

**Owns:** download engine, worker, storage, provider boundary. **Depends on:** `core`, `broker`.

| File | What it does | Notes |
|---|---|---|
| `downloader/engine.py` (431) | MODE 1 historical OHLCV download; scan→coverage→queue→fetch→DB | Provider **required** (raises `TypeError` if None) — no hidden default. Good |
| `downloader/queue.py`, `sweep.py` | download queue + forward sweep | modified in working copy |
| `storage/candle_db.py` (340) | `ohlcv`/`non_trading`/`history_boundaries`, WAL, `INSERT OR IGNORE`, V9/V9.1 migrations | clean |
| `provider/contract.py` | `Provider` Protocol, `CANONICAL_INTERVALS`, 7 error codes | Broker-agnostic boundary |
| `provider/zerodha/auth.py` (361) | `token.json` + Selenium TOTP login | ⚠️ **R6 external deps** — Selenium+chromedriver in an optional extra |
| `provider/zerodha/live_trading.py` (531) | Zerodha Kite **live trading adapter** | ⚠️ **R5** — real money path; see below |
| `provider/factory.py` | `register_provider`/`build_provider` | modified |
| `ui/*.py` (5 files, 1,800 LOC) | Qt panels: status_view(620), stock_checklist(480), download_panel(416), credentials_dialog, historical_panel | ⚠️ **R4** — Qt UI in `02_data` violates Constitution §3 |
| `settings.py` | `DownloadSettings` frozen; NSE holidays; **explicitly no credentials** | clean |
| `credentials_store.py` | File store + **Windows Credential Manager** (ctypes `advapi32`) | Secrets never in events/logs — verified |

---

### `03_market` — Read path (1,263 LOC, 29 files, 67 tests)

**Owns:** per-symbol SQLite → `Bar`/`SymbolQuote`. **Depends on:** `core`.

| File | What it does | Notes |
|---|---|---|
| `database/ohlcv.py` | Real per-stock schema (`candle_time` PK, no `symbol` column); **`LIMIT ?` never binds NULL** | Correctly handles `limit=None` as full ascending scan |
| `database/sqlite.py` | Legacy `candles` schema variant | ✅ **Note:** two near-identical DB classes — `OhlcvCandleDatabase` (real) vs `SqliteCandleDatabase` (legacy). Dead-ish duplication |
| `repository/candle_repository.py` | rows→`Bar`, **timeframe detection** (mode of deltas), aggregation | `detect()` caches `(base, session_start)` for pollers — good perf design |
| `repository/symbol_repository.py` | dir glob → symbols, opens file per call, **always closes** (`try/finally`) | clean |
| `timeframe/aggregate.py` (125) | Session-anchored bucketing; **delegates numeric loop to Rust** | Python keeps only parsing/formatting — constitution-correct |
| `timeframe/timeframe.py` (96) | ladder + `generate_label`; **never a hardcoded available list** | clean |
| `native_aggregate.py` (110) | ctypes marshal + `struct.iter_unpack` bulk decode | overflow + layout-drift guards present |
| `loader/*.py` (4 files) | MarketDataLoader, QuoteLoader, SymbolListLoader, TimeframeListLoader | All catch→log→return (bus survives). `QuoteLoader` no-ops on identical universe |

**Risk:** R4 (duplicate DB layer, low severity).

---

### `04_chart` — Presentation (6,971 LOC, 32 files, 209 tests)

**Owns:** model, engine, renderers, widgets, windows, theme. **Depends on:** `core`, `market`.

| File | LOC | Notes |
|---|---|---|
| `widgets/candle_chart_widget.py` | 1,486 | ⚠️ **R2-class size** — viewport + zoom/pan/crosshair + context menu + gestures in one widget |
| `renderer/plot_renderer.py` | 1,446 | ⚠️ **Very large renderer**; owns overlay drawing |
| `windows/chart_window.py` | 502 | Publishes `LoadSymbol`, hosts splitter |
| `widgets/tools_toolbar.py` | 438 | SVG icon rail |
| `renderer/*` (6 files) | — | candle/crosshair/label/overlay/plot/time_axis — stateless QPainter ✅ |
| `session/session_store.py` | 177 | chart session persistence |

**Observations:** Layer discipline is correct (paint only in renderer, no SQL in UI). `QNativeGestureEvent.localPos()` is **deprecated** (warning in test run) — cosmetic.

---

### `05_strategy` — Strategy + Research (8,236 LOC, 45 files, 197 tests)

**Owns:** registry, Python-native runtime, research, Lab UI. **Depends on:** `core`, `market`.

| File | LOC | Notes |
|---|---|---|
| `research/advanced_validation.py` | 1,601 | CPCV, OOS, PBO, DSR, multiple-testing, cost stress, leakage, temporal stability. **Statistically serious work** |
| `strategies/base.py` | 572 | `PythonStrategy` — plotting, bought/sold helpers, muted bars |
| `version.py` | 526 | Version control (source hash, IR version) |
| `language/compiler.py` | 95 | ⚠️ **R3 `exec(code, namespace)`** |
| `research/evolution.py` | 460 | strategy evolution |
| `ui/*.py` | — | code_editor, control_panel, strategy_dialog, strategy_list_panel |

**R3 detail:** `compile_strategy()` runs **user-supplied strategy source** via `exec(code, {"__builtins__": __builtins__})`. This is by design (a "Python-native strategy language, no DSL/VM" — docstring is explicit), **and it's coherent for a single-user desktop app**. But: full `__builtins__` is exposed, there is no sandbox, and strategies may be imported from disk (`language/storage.py`). **Not a defect against the stated design — a documented trust boundary.** Flag it so it's a conscious decision.

---

### `06_backtest` — Replay/simulation (3,929 LOC, 27 files, 54 tests)

| File | LOC | Notes |
|---|---|---|
| `runner.py` | 947 | ⚠️ large; orchestrates batch replay → results |
| `execution.py` | 437 | execution history, snapshots, lineage |
| `engine/{replay,positions,metrics,simulator,journal,directional}.py` | — | clean separation |
| `native_metrics.py` | 77 | Rust-backed max-drawdown/equity/sharpe |
| `optimizer.py`, `validation.py`, `worker.py` | — | BatchWorker runs its own loop (⚠️ leaks unless shut down — see R1) |

Correctly **never imports** `execution`/`risk` (verified).

---

### `07_risk` — Pre-order gates (500 LOC, 6 files, 17 tests)

**Smallest, cleanest module.** `RiskEngine.evaluate()` wraps everything in `try/except` → **any error denies** ("when uncertain, NO ORDER"). 17 named checks (kill switch, broker health, session, clock, instrument, duplicate, fresh data, spread, cooldown, order rate, sanity, qty, notional, position, exposure, daily/strategy loss, capital). `KillSwitch` persists to JSON so a restart can't clear it. **Exemplary.**

⚠️ Faint smell: `engine.py` calls `within_session(...)` and `clock_sane(...)` **twice each** (once for the check, once for the detail string) — O(2n) recompute, and inconsistent detail text if inputs mutate. Trivial.

---

### `08_execution` — Live/paper sessions (6,188 LOC, 45 files, 143 tests)

| File | LOC | Notes |
|---|---|---|
| `runtime/session.py` | 940 | `LiveSession` — the live brain. ⚠️ **R2-class** |
| `broker/sandbox.py` | 352 | sandbox venue |
| `market_data/{broker_feed,sqlite_tail,normalizer,replay,provider}.py` | — | intake + staleness before ingest ✅ |
| `broker/{paper,gates,factory,adapter,activation,readiness,resilience,credentials}.py` | — | mode-gated resolution |
| `engine.py` | 231 | Order lifecycle tracker; `reconcile()` is the **only** UNKNOWN exit |
| `portfolio/{ledger,reconcile}.py` | — | positions + reconciliation |
| `modes.py` | — | PAPER default; LIVE needs **5 gates**; env parse requires exact `"true"` |
| `adaptive/{attention,memory,confidence}.py` | — | advisory only, `size_multiplier ∈ (0,1]` (shrink-only) |

**Verified safety chain:** signal → adaptive posture (may HALT) → risk gate → plan → **risk re-validates final quantity** → submit → **LIVE blocks unless `LiveArm.ARMED`**. Clean and layered.

**R1 relevance:** `Bootstrap` starts `BacktestWorker` threads that run immediately and are only stopped by an autouse teardown hook.

---

### `09_broker` — Unified Broker Layer (1,956 LOC, 16 files, 201 tests)

**Docs refer to this as UBL.** Owns vocab, tri-state capabilities, three protocol faces, single registry, selection, funds, credentials, health, identity. **Depends on: nothing.**

- `registry.py` — the **only** name→plugin map; duplicate/unknown both fail closed
- `credentials.py` — `CredentialRef` has **no value field**; `__repr__` redacted by construction. Excellent design
- `selection.py` — one recorded `BrokerSelection` per session; `surface_resolution`/`surface_status` pure + fail-closed
- `adapters/zerodha/__init__.py`, `adapters/skeleton/__init__.py` — isolated adapter packages

**Test ratio is inverted here (1,956 prod vs 3,522 test)** — the boundary is heavily contract-tested. Good sign.

---

### `00_app` — Composition root (15,316 LOC, 30 files, 220 tests)

| File | LOC | Notes |
|---|---|---|
| `ui/strategy_lab_workspace.py` | **3,277** | ⚠️ **R2 — largest file in repo** |
| `bootstrap/bootstrap.py` | **2,851** | ⚠️ **R2 — the composition root** |
| `ui/research_workspace.py` | 1,110 | |
| `services/live_trading_service.py` | 1,101 | |
| `ui/live_workspace.py` | 979 | |
| `services/trade_chart_controller.py` | 824 | |
| `ui/stock_ranking.py` | 813 | |
| `services/{paper,broker_manager,research}_service.py` | — | |

**R2 detail:** `Bootstrap` is the single wiring point (correct per architecture) but has grown to 2,851 lines. It contains `build_execution_history()` (inline execution/lineage logic), several `_HistoryDomain` alias hacks, multiple nested closures subscribing via lambdas, and `# type: ignore` at scale. It is doing composition **and** business logic.

---

### `rust/` — Rust workspace (1,236 LOC)

| Crate | Files | Purpose |
|---|---|---|
| `vayren-core` | `lib.rs`(212), `order_state.rs`(192), `metrics.rs`(173), `aggregate.rs`(160), `stats.rs`(55) | cdylib; order-lifecycle table, backtest kernels, aggregation, mode |
| `vayren-shell` | `shell.rs`(207), `view_model.rs`(206), `ui/app.slint`, `ui/palette.slint` | **Slint native UI shell** (new, in working copy) |

ABI rules stated and followed: fixed-width ints, `f64`, caller-allocated buffers, **no panics across boundary**. `slice()`/`slice_mut()` are `unsafe` with documented safety contracts. This is the cleanest part of the repo.

---

## 2. Findings — Ranked

> **R1 — CRITICAL (process/CI): full `pytest` run HANGS. — ✅ FIXED (2026-09-11)**
> The suite never completed as a whole. Confirmed: `00_app` stalled at 37% and `test_runtime_integration.py` hung at test #9 (`test_download_panel_is_embedded_in_main_window`) — it printed PASSED then never returned. Alone it passed in 5.4s; after 8 prior `Bootstrap` constructions in the same process it deadlocked. **Cause:** every `Bootstrap` built a full Qt window + `BacktestWorker` QThread(s); root `conftest.py` deliberately disables GC (`gc.disable()`), so nothing was ever collected, and the autouse teardown walked `gc.get_objects()` calling `obj.wait(3000)` on each running thread — accumulating linearly until the process wedged.
>
> **Remediation shipped (3 edits):**
> 1. `00_app/app/bootstrap/bootstrap.py` — added **`Bootstrap.stop()`** (did not exist: Bootstrap could be started but never stopped) plus helpers `_release_widget_references()` / `_drop_gui_service_entries()`. It shuts down the worker threads it owns (`data_worker`, `backtest_worker`, `broker_manager.stop_worker()`), closes the chart window, clears the bus, **drops its own strong references** to the orphan panels (`StrategyControlPanel`, `StrategyListPanel`, `PerformancePanel` are constructed with **no parent** and held only by `self._*` + the services registry), then flushes `DeferredDelete`.
> 2. `04_chart/chart/windows/chart_window.py` — added a `closeEvent` override calling `deleteLater()`. Previously **no** `WA_DeleteOnClose` and **no** `closeEvent`: `close()` only *hid* the window, so the ~1,150-widget tree survived for the process lifetime.
> 3. `00_app/app/tests/conftest.py` — teardown now calls `Bootstrap.stop()` first; the `gc.get_objects()` sweep is retained only as a safety net for non-Bootstrap threads (it was the actual hang source).
>
> **Verified:** leak probes showed **monotonic** growth before (3 QThreads + ~1,150 widgets per `Bootstrap`) and **0 threads / 0 widgets** after, stable over 15 consecutive iterations. The **full suite now runs in a single process: `1632 passed in 177s`** (was: hung at 37% forever). Remaining suites and validators pass. **Impact resolved:** `pytest` can now be a merge gate.
>
> **Residual note:** the 3 orphan panels are a design smell even with the fix — they are built, registered as services, and never parented. Long-term they should either be parented into a layout or not constructed when unused (see R7).

> **R11 — MEDIUM (portability): hardcoded absolute Windows paths.**
> `00_app/app/bootstrap/bootstrap.py` contains **16 occurrences** of `r"D:\VAYREN_STRATEGIES"` and `app/__init__.py` sets `DEFAULT_DATA_DIR = r"D:\ZerodhaTradingData"`. On the very machine this was scanned, `D:\` does not exist. `settings.py`'s own docstring states data paths are configurable — these literals contradict it. **Impact:** first-run failure / silent creation of `D:\` on machines without that drive; untestable without the exact directory layout. **Fix direction:** route both through `settings.py` with an env var + user-home default, and delete the literals.


> **R2 — HIGH (maintainability): three files are 2,800–3,300 lines.**
> `bootstrap.py` (2,851), `strategy_lab_workspace.py` (3,277), `candle_chart_widget.py` (1,486), `plot_renderer.py` (1,446), `advanced_validation.py` (1,601), `runner.py` (947), `session.py` (940). AGENTS.md mandates "one responsibility per class, split if 2 verbs" — these violate it. Bootstrap mixes composition with `build_execution_history` business logic.

> **R3 — MEDIUM (security/trust): `exec()` of user strategy source.**
> `compile_strategy()` execs arbitrary Python with full builtins and no sandbox. Design-intentional ("performance: Python-native, no VM"), acceptable for a single-user desktop tool — **but it must be a conscious, documented trust boundary**, because strategies are loadable from disk.

> **R4 — MEDIUM (constitution drift): Python/Qt UI in Rust/Slint-owned domains.**
> Constitution §3 says all NEW native UI must be Rust+Slint. Yet `02_data/data/ui/` (5 files, ~1,800 LOC: `status_view`, `stock_checklist`, `download_panel`, `credentials_dialog`, `historical_panel`) and `06_backtest/backtest/ui/` are Python+Qt. The new `rust/vayren-shell` Slint UI exists in the working copy but has migrated **almost nothing** — `app.slint` renders "honest migration state" placeholder cards. Validator passes because these are tracked as `TEMPORARILY_RETAINED` in `language_retention.json`, not because they're compliant.

> **R5 — MEDIUM (operational): real-money Zerodha path in `02_data`.**
> `provider/zerodha/live_trading.py` (531 LOC) places real orders. Gated by 3 env vars with exact-string parsing, and redaction is implemented — **but** it lives in the *historical data* module, not `08_execution`. That's a layer-boundary violation and means the live-trading adapter is not covered by `08_execution`'s readiness/gate machinery.

> **R6 — LOW: two parallel SQLite DB classes.** `OhlcvCandleDatabase` (real schema) and `SqliteCandleDatabase` (legacy `candles`) are near-duplicates.

> **R7 — LOW: dead/degenerate code & commented-out blocks.** `bootstrap.py:148` `ir_version = 1  # noqa: F841` (assigned, unused); the `_HistoryDomain` namespace-alias class exists only to make bootstrap "read like the design table"; empty try/except-pass blocks in `language/storage.py`, `research/analysis.py`, `research/evidence.py`.

> **R8 — LOW: dependency risk.** Core `dependencies = ["PySide6>=6.7"]` is thin, but the `data` extra pulls `selenium` + `chromedriver-autoinstaller` + `kiteconnect` + `pyotp` — a browser automation stack in a desktop trading app.

> **R9 — LOW (process): 361 files uncommitted on `main`.** +6,619/−9,397 including deleted `vayren_ui.html`, a new Rust Slint shell, and 20 untracked files. The entire live-trading stack (`live_trading.py`, `brokers_workspace.py`, `09_broker/management.py`, `status.py`) is untracked. One `git clean` or bad rebase loses it.

> **R10 — LOW (env): `make check` is not runnable as-is here.** Bare `python` = managed 3.13 **without** pytest/PySide6; the working interpreter is system Python 3.11. `make check` calls bare `pytest`/`pyright` — it will fail without an activated venv. (Pyright also reports ~dozens of `reportMissingImports` because it can't resolve the per-chapter roots without a proper install.)

---

## 3. What Is Genuinely Good

1. **Validator-driven architecture.** `validate_imports` (AST dependency-graph), `validate_structure`, `validate_language_ownership` (per-file retention enforcement) — all pass. Boundaries are machine-checked, not just documented.
2. **Fail-closed everywhere.** Risk denies on internal error; broker registry raises on unknown; native loader has **no silent Python fallback**; corrupt checkpoint → `IllegalTransitionError`; LIVE without 5 gates → PAPER with reasons.
3. **Rust boundary is textbook.** ABI version + state-count handshake, `unsafe` blocks with safety docs, no panics across FFI, layout-drift guards.
4. **Secrets discipline.** `CredentialRef` has no value field; `repr` redacted; no tokens in git; Windows Credential Manager integration; explicit "no credentials in settings" docstring.
5. **Event catalog integrity.** 19 core + live events documented, all frozen dataclasses, payloads are pure data, requests vs facts distinguished.
6. **Research rigor.** 1,601 LOC of CPCV/PBO/DSR/leakage analysis is real quantitative work, not a stub.

---

## 4. Recommended Next Actions (priority order)

1. ~~**Fix R1**~~ — **DONE.** `Bootstrap.stop()` + `ChartWindow.closeEvent` + conftest teardown. Full `00_app` suite now completes (220 passed / 130s); leak verified at 0 threads + 0 widgets over 15 iterations.
2. **Commit or stash R9** — the entire live-trading stack is untracked on `main`.
3. **Fix R11** — replace the 16 hardcoded `D:\VAYREN_STRATEGIES` literals and `DEFAULT_DATA_DIR = r"D:\ZerodhaTradingData"` with `settings.py`-driven config (env var + user-home default).
4. **Split R2** — start with `bootstrap.py`: extract `build_execution_history` into `app/services/`, keep only wiring.
5. **Relocate R5** — move Zerodha live-trading adapter into `08_execution/broker/` so it's inside the gated boundary.
6. **Decide R3 explicitly** — either document the `exec()` trust boundary in `module_contracts.md`, or add an import allow-list.
7. **Close R4 backlog** — Slint shell exists but migrates nothing; either port one real screen (`BrokerPanel` is already modeled) or mark the gap in `ai_memory.md`.
