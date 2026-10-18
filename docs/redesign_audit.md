# REDESIGN AUDIT — Phase 1 (Current Architecture) & Phase 2 (Root Cause)

> Status: audit + documentation only. **No code, data or credentials were changed; nothing was pushed.**
> Method: read-only inspection (source, docs, git history, EC2 gateway state via SSH, production-code probes on the real data dir).
> Five read-only workstream audits (readiness, strategy, market data, risk/execution, UI) are integrated below.

---

## 1. Deliverable summary

- This document is the Phase 1 (audit) + Phase 2 (root-cause documentation) deliverable of the LIVE-engine redesign. It records the current architecture and dependency map, the proven root cause of the DREDGECORP `timeframe 3m not available` START block, the enabling defect classes, and the gap list against the redesign targets.
- Everything here is evidence-first: every claim carries a `file:line` reference, a byte-level diff, a production-code probe, or an SSH-verified fact. Five read-only workstream audits (readiness, strategy system, market data, risk/execution, UI) are integrated.
- **No code, data, config or credentials were changed. Nothing was pushed.**

---

## 2. Part A — Current architecture (dependency map)

### 2.1 Two runtimes, one backend

| Runtime | Entry | State owner |
|---|---|---|
| **Local** (dev) | `vayren-shell` spawns `python -m app.headless` (`crates/vayren-shell/src/main.rs:724`) | the local Python service |
| **Remote** (production desktop) | packaged-remote `vayren-shell` → WSS → EC2 gateway (`src/remote/server.py`); EC2 service: `remote.runner --data-dir /home/ubuntu/.vayren/data --strategy-dir /home/ubuntu/.vayren/strategies` | **the gateway's Python service** (UI queues intents only) |

The LIVE truth (status, blockers, config) is produced by `src/app/services/live_trading_service.py` on whichever host runs the headless backend, serialized by `src/remote/snapshot.py`, translated by `crates/vayren-shell/src/remote_live.rs`, projected by `crates/vayren-domain/src/live.rs`, rendered by `crates/vayren-shell/ui/live.slint` (shared with the embed host `crates/vayren-live-view`).

### 2.2 Module → dependency map

| Module | Path | Depends on | Notes (evidence) |
|---|---|---|---|
| UI shell | `crates/vayren-shell` (`ui/live.slint`, `src/{main,shell,remote_live}.rs`) | `vayren-domain` | Rendering + intent queue; `bridge_wired` gate for commands (`remote_live.rs`) |
| UI domain | `crates/vayren-domain/src/live.rs` | — | Session state machine, projection, action builders (`push_setup_action` :1188) |
| Remote | `src/remote/` (`server.py`, `gateway.py`, `snapshot.py`, `protocol.py`, `client.py`) | app/headless | Command allow-list (`server.py:57-81`), token auth, snapshot reshape (`snapshot.py:158+`) |
| App | `src/app/headless.py` + `services/` (`live_trading_service`, `broker_manager`, `candle_sync_service`, `market_data_service`, `backtest_service`) | market, strategy, execution, broker | Composition root; `live_trading_service` is the LIVE authority |
| Market | `src/market/repository/symbol_repository.py`, `native_timeframe.py`, `native_aggregate.py`, `src/data/candle_store.py` | Rust kernels | Per-symbol SQLite (`<SYMBOL>.db`), base-detection + timeframe ladder |
| Strategy | `src/strategy/` (`registry.py`, `runtime.py`, `language/`, `universe_store.py`) | core, market | Python-owned; registry singleton (`registry.py:519-538`); OBR C1C4 canonical def (:377-512) |
| Execution | `src/execution/runtime/session.py` + `market_data/` (providers) + `recovery/reconciliation/control_plane` | risk, broker | `LiveSession` per symbol; 11 readiness gates (`session.py:107-169`) |
| Risk | `src/risk/` → Rust `crates/vayren-core/src/risk_engine.rs` | — | 18-check kernel is the single decision authority; Python is a fail-closed bridge |
| Broker | `src/broker/registry.py`, `providers/fyers/` (`session_adapter.py`, `live_market_data.py`) | — | FYERS order WS + market WS; Zerodha is REST-polling only |
| Rust kernels | `crates/vayren-core` (`risk_engine`, `order_state`, `normalizer`, `aggregate`, `market`, `live_readiness`) | — | Order state table, stream normalizer, timeframe ladder, live gates |
| Slint views | `crates/vayren-shell/ui/*`, `crates/vayren-live-view/`, `crates/vayren-system-view/` | domain | LIVE surface has ONE global session card (no per-strategy cards) |

### 2.3 LIVE data path (current)

```
store sync:  candle_sync_service.sync_candles ──write──▶ <data_dir>/<SYMBOL>.db (only production writer)
                                                        │ (schema: ohlcv(candle_time PK, o,h,l,c,v))
LIVE start:  LiveTradingService.validate() ──▶ _validate_uncached()  (store readability, universe, timeframe ladder)
             _build_sessions()
               ├─ history = _load_history(sym, timeframe)        (full DB read; warmup = last 120 bars, :71/:2011)
               ├─ provider: BrokerFeedProvider (venue WS)  ORELSE  SqliteTailProvider (DB tail)
               ├─ LiveSession per symbol → 11 readiness gates → WARMING_UP → RUNNING
               └─ risk policy: RiskPolicy(max_order_qty=quantity, max_position_qty=quantity, allowed_symbols=(sym,))
tick 1s:     session.step() → provider.poll() → Rust StreamNormalizer (seq dedup, reorder 128)
             → closed CandleEvent → strategy on_bar → Signal → sizing (0.15% × 4× margin)
             → RiskEngine.evaluate ×2 (intent + ":final") → planner → pre-order checks → venue place_order
             → fills/orders stream → ledger → protective stop
snapshot:    service.snapshot() → remote/snapshot.py reshape → WSS → remote_live.rs translate → live.slint
```

### 2.4 LIVE lifecycle & gate chain (as-built)

- States: `STOPPED / STARTING / RUNNING / STOPPING / HALTED / ERROR` (`live.rs` `SessionStatus`).
- START chain: UI action (`confirmed` required remotely, `server.py:673-675`) → `headless._live_action` (`setup` auto-syncs only missing symbols, `headless.py:502-528`) → `service.start()` → consent → `validate()` → blockers → `_build_sessions()` (venue probe, sessions, warmup, reconcile, arm) → RUNNING.
- Backend blocker vocabulary: `live_trading_service.py` `_validate_uncached` :1032-1110 + `_live_blockers` :1117-1185 (broker selected/auth/venue, 5 env gates `live gate off`, consent, broker capital).
- Modes today: **PAPER (default) / SANDBOX / LIVE**; execution factory degrades LIVE→PAPER unless a registered adapter + all 5 env gates are present (`src/execution/broker/factory.py:104`, `modes.py:62-80`). Target redesign: exactly **BACKTEST + LIVE** (paper/sandbox/fake-order vocabulary removed).

---

## 3. Part B — DREDGECORP `timeframe 3m not available` — root cause

### 3.1 Symptom and producer

Banner: `✕ START BLOCKED — timeframe 3m not available for NSE:DREDGECORP`
Producer: `live_trading_service.py:1088-1091` (ladder branch of `_validate_uncached`, only reachable when `DREDGECORP.db` exists and its base duration cannot divide 180 s). Composed for display at `live.slint:1090/:1094/:1145`; surfaced raw via `snapshot()["start_blockers"]` (:1828) → `remote/snapshot.py:303` → `remote_live.rs:757-768`.

### 3.2 Evidence chain (first-hand)

1. **The store's config timeframe changed across the restart.** Gateway store `/home/ubuntu/.vayren/data/live/live_session.json`:
   - backup `live_session.json.pre-timer-deploy` (2026-10-18 05:31:50 UTC, 1,216,174 B) → `"timeframe": "30m"`;
   - current (05:33:27 UTC, 1,216,173 B) → `"timeframe": "3m"`;
   - **byte-level JSON diff of the entire 1.2 MB store: exactly one key changed — `/config/timeframe: "30m" → "3m"`.** Nothing else: no data loss, no other config change, no new activity entries.
2. **Timeline (all UTC):** old process last armed run at 05:12:31 logged `strategy OBR C1C4 ready — 30m`; service restart 05:31:50→05:31:52 (new process loads the store = 30m); two remote clients connect 05:32:43 / 05:32:48; store rewritten 05:33:27 with 3m; the operator's app opens later (05:36:38). **No activity entry exists after 05:12:31** — consistent with a config-only write (`configure()` path), not a run or setup lifecycle event.
3. **Only one writer can produce that change.** `_config.timeframe` has exactly three producers: the strategy-def branch (`:642-643`, only fills when a strategy select arrives without a timeframe), the explicit `configure(timeframe=...)` (`:682-683`), and the restore fallback (`:2966-2967`, only when the stored value is EMPTY). The loaded store held `"30m"`; the canonical OBR C1C4 definition resolves to **`timeframe='30m'`** (verified on EC2 through the production registry + `registry.py:404/:505`). Therefore the write was `configure(timeframe="3m")` — i.e. a remote **`setup` action carrying `timeframe:"3m"`** (`src/remote/server.py:676-678` allow-list; `headless.py:482-489` → `configure`).
4. **The client echoes its own selected timeframe.** The remote client's `push_setup_action` (`crates/vayren-domain/src/live.rs:1188-1206`) sends `{strategy_name, timeframe: selected_timeframe(), quantity, symbols}` on every accepted edit (strategy select :1296, timeframe pick :1363, etc.). The client's selection can diverge from the server's real ladder: it synthesizes a default 8-entry ladder **including "3m"** when the snapshot lacks a usable list (`remote_live.rs:742-747`) and appends an out-of-ladder `selected_timeframe` to its own list (`live.rs:1947-1953`).
5. **The data genuinely cannot serve 3m.** All 22 EC2 per-symbol stores hold 30-minute bars (260 bars, 2026-09-18 → 2026-10-16; KAYNES/ZEEL 560). Production-code probe on the real data: `detect_timeframes('DREDGECORP')` → `('30m','1h','2h','4h','1D','1W')`. Rust ladder rule (`crates/vayren-core/src/market.rs:244-258`): a timeframe is available iff `seconds % base == 0`; with base = 1800 s, `3m` (180 s) can never appear. **The pre-existing validator is correct and deterministic.**
6. **Why it worked at 10:42 and broke after:** the config was `30m` then (store backup + activity line `ready — 30m`); it became `3m` during the 05:32–05:33 client window, before the operator touched anything.

### 3.3 Root cause statement

The DREDGECORP START block is **not** missing data and **not** a faulty validator: it is a **LIVE config drift** — a remote client `setup` command wrote `timeframe:"3m"` into the authoritative live config during the post-restart verification window (the store's only changed field in 1.2 MB; config is written only via `configure()`). The server accepted the unservable value **without validation at write time**, persisted it, and the correct START-time ladder check now blocks every selected symbol (first = DREDGECORP).

### 3.4 Enabling defects (fix candidates, not part of the immediate unblock)

1. `configure()` accepts any timeframe string; validation happens only at START (`:682-683` vs `:1088-1091`) — fail-late instead of fail-fast.
2. `get_candles_timeframe()` returns base rows **relabeled** for unservable smaller timeframes (plain fetch plan, `symbol_repository.py:230-233`; `market.rs:333-342`; probe: `get_candles_timeframe('DREDGECORP','3m',5)` returned 5 rows) — the "no market data" branch is blind by construction and warmup could be fed mislabeled bars.
3. Setup-time auto-sync cannot repair this class: it syncs only symbols whose fetch is **empty** (`headless.py:502-528`) — the mislabel makes coarse-base symbols look synced.
4. Validation cache key excludes store state (`:1010-1026`) — fixing the store alone would not re-evaluate until a config/start action.
5. Remote client can offer/forward timeframes the backend cannot serve (fallback ladder `remote_live.rs:742-747`; append `live.rs:1947-1953`) and mutating commands are not verbatim-logged — the exact emitting gesture at 05:33 is not recoverable (both verification clients were closed without logs).
6. Message wording (`timeframe 3m not available`) imputes missing candles; the actual dependency is the base-duration divisibility ladder — no remediation guidance.

### 3.5 Immediate remediation options (pick one, operator decision)

| Option | Action | Restart needed? |
|---|---|---|
| A (operator UI) | In the app: ORDER & SESSION → TIMEFRAME cycler → click until it reads **30m** (each click sends a `setup`; the backend then re-validates) | no |
| B (scripted) | Send one `setup {timeframe:"30m"}` through the gateway with the operator token | no |
| C (deterministic) | Stop gateway → edit store `config.timeframe` → `"30m"` → start gateway | yes (needs approval) |

30m is the correct grid: OBR C1C4 declares it (`registry.py:404`, `:505`) and every store serves it. Remaining LIVE gates (broker auth, consent, capital, env gates) are ordinary operator steps and unaffected by this blocker.

---

## 4. Part C — Gap list vs redesign targets (condensed)

| Redesign target | Current state | Key gaps (evidence) |
|---|---|---|
| Exactly two modes (BACKTEST/LIVE) | PAPER default + SANDBOX + paper broker; LIVE→PAPER silent degradation | `factory.py:104`, `modes.py:62-80`, `live.rs` ExecMode; remove paper/sandbox vocabulary + fake-order paths |
| Universal strategy contract | Only `warmup()`/`on_bar()`; optional unenforced declaration block | no init/shutdown, no per-strategy health/metrics; `SUPPORTS_LIVE` force-set at composition (`live_trading_service.py:1941-43`); contract reads ad-hoc attrs, ignores definition (`inspector.py`) |
| Pluggable registry + per-strategy validation + isolation | Registry singleton (first `data_dir` wins); params validated; isolation partial | no lifecycle/registry-level failure isolation (compile failure aborts backtest; `sync_storage` warns silently); version story split across 3 schemes |
| Common market-data contract (backtest vs live adapters) | Three incompatible interfaces (venue faces / execution providers / historical pull); `MarketDataRouter` designed but unwired | no live-gap detection/backfill; staleness thresholds duplicated (120/300/60/30 s); sqlite tail has bounded catch-up only |
| Event pipeline (dedup, ordering, bounded queues, risk-before-order) | Rust normalizer (seq dedup, reorder 128, overflow policy) in `session.step`; two risk evals per order (strong) | strategy events declared but dead (registry/manifest only); no bus; no upstream provider-level ordering guarantee |
| Shared risk layer (existing limits preserved) | Rust 18-check kernel is single authority (strong); sizing 0.15% formula | policy built per-session from `config.quantity` only; most limits disabled by default; `data_age_seconds=0` hardcoded in risk requests; kill switch per-session file; **no SAFE_STOP anywhere (0 repo matches)** |
| Robust execution (ids, idempotency, reconcile, timeouts) | Strong primitives: intent ids, client-order-id tag, venue tag-resolve on timeout, UNKNOWN→explicit reconcile, checkpoints | `PeriodicReconciler`/pipeline **unwired from LiveSession**; UNKNOWN can persist without operator reconcile; restart recovery defers SL re-arm to reconcile; no retry/cancel-on-timeout policy (intentional) |
| Per-strategy lifecycle + dashboard | One global session status; dashboard surfaces exist | no per-strategy states/health; data freshness not surfaced; reconnect counters parsed but unrendered; `system.execution_ready` unconsumed |
| Automated tests (13 categories) | Rust kernel/state tests strong; remote UI regression tests; live runtime timer tests | no live-feed gap/recovery tests, no reconciliation-in-tick-loop tests, no per-strategy isolation tests at scale |

---

## 5. Part D — Constraints, unknowns, next phase

**Constraints honored:** read-only audit; no pushes; no DB/credential changes; operator session files untouched.

**Unknowns (explicit):**
1. The exact client gesture/binary that emitted `timeframe:"3m"` at ~05:33:27 UTC — mutating commands are not verbatim-logged; both verification clients had closed. The write path (client `setup` → `configure` → persist) is proven; the emitting UI state is inferred from code, not logged.
2. `crates/vayren-core/src/live_session.rs` (Rust twin) invocation status — unwired as far as this audit traced.
3. `control_plane.py`/`ControlPlaneBridge` production wiring — tests/embedded only.
4. EC2-side scripts/history outside the repo were not audited.

**Next phase (on operator go-ahead): Phase 3 — interface definition** (strategy contract, market-data contract, risk layer, execution pipeline, two-mode split) followed by the incremental migration plan. Phase 4 (backtest/live dependency separation) must also remove the class of defect §3.4: validate operator config at write time, never let a client-origin value persist unvalidated, and make the readiness output actionable.

---

## 6. Execution log — restore + readiness verification (2026-10-18, post-audit)

**6.1 Phase 2 restore — done through the supported command path (no DB edit, no restart).**
- Sent the exact path a real client uses: remote `setup {"timeframe":"30m"}` (`remote/server.py:676-678` allow-list → gateway → `headless._live_action` → `configure()` → `_persist_config()`), from an EC2-local protocol client (`src/remote/client.py`) authenticated with the trade token from `/etc/vayren/remote.env` (token read in-process, never printed). Service stayed up throughout.
- Backend accepted: `{"ok": true, "action": "setup"}` with `start_blockers` reduced to `["live confirmation required (explicit operator consent)"]` — the 22 per-symbol timeframe lines are gone.
- Store re-read + key-by-key diff: `config.timeframe` `"3m" → "30m"`; **only that key changed**; no candle writes (setup auto-sync skipped because 30m rows exist for all 22 symbols); activity list unchanged (configure writes no activity entry — consistent with the original forensics).
- Fresh client connect then confirmed the running service: `selected_timeframe="30m"`, `strategy.timeframe="30m"`, `available_timeframes=['30m','1h','2h','4h','1D','1W']`.

**6.2 Phase 3 readiness — first-hand evidence (2026-10-18 ~06:23 UTC / 11:53 IST).**
- Broker: FYERS session valid (read-only `profile`); account `XV29244` (VISHAL SINGH). Funds ₹100.00 available, ₹0 used — confirmed twice independently (service `refresh_capital` @06:18:54 + direct adapter `funds()` read). Positions: 0. Open orders: 0 (no pending AMO). `live/order_path_test.json` ledger absent → one-BUY allowance unused.
- Quotes: live LTPs fetched for all 22 universe symbols (Sunday = last traded): JINDWORLD 41.92, DCW 44.88, SAGILITY 45.30, VIKRAN 59.92, IFCI 66.99, ZEEL 71.85, … DREDGECORP 896.45.
- Gates (snapshot 06:22:58 UTC): Broker Connected READY · Credentials Valid READY · Account Confirmed READY · Market Data Streaming READY ("candle store 22/22; no live feed") · Strategy Ready READY ("OBR C1C4 ready — 30m") · Risk Engine Ready READY ("max 1 per order") · Reconciliation NOT CONFIGURED (no session; `blocks_live=false`) · Environment (LIVE) READY · **Arming NOT READY ("DISARMED — arm before START")**.
- Data: per-symbol stores hold 30m bars through Fri 2026-10-16 15:15 IST (22/22); the venue live feed is expected to engage only while a session is RUNNING ("no live feed" is the correct STOPPED reading).
- Calendar: today Sunday (closed); Monday 2026-10-19 is a normal session; Tuesday 2026-10-20 is a holiday (Dussehra).
- **AMO policy (decisive):** FYERS, under SEBI's algo-trading rules — "AMO orders are not permitted in algo trading… ensure the `offlineOrder` parameter is always set to false when placing orders through the API." The adapter already complies (`offlineOrder:false`, `session_adapter.py:881`); the operator-authorized `order_test` places MARKET/DAY only (`order_path_test_service.py:351-358`). **A genuine AMO cannot be submitted through the FYERS API**; the broker's client-facing AMO surface is the FYERS app's Schedule Order (outside VAYREN).
- **Outcome: no order was submitted.** (No ticket was authorized; AMO is unavailable through the API; consent is disarmed.) The one-BUY allowance remains untouched and nothing needs reconciliation.

**6.3 Remaining defect classes (planned, not yet changed):** unvalidated `configure()` write (fail-late at START); `get_candles_timeframe` relabeling base rows for unservable timeframes (probe: `"3m"` still returns rows); client-side ladder synthesis/append (`remote_live.rs:742-747`, `live.rs:1947-1953`); validation-cache key excludes store state; stale persisted `session_timing.status="RUNNING"` text in the store while the service is STOPPED (cosmetic — the worker card reads service state, not this block).

**6.4 Phase 5 increment 1 — timeframe authority implemented and verified (local code only; nothing deployed, nothing armed, no order placed).**

- **Write-time gate**: `LiveTradingService._require_servable_timeframe` refuses a client-origin timeframe BEFORE assignment or persist unless every configured symbol's detected ladder (Rust `available_timeframes`) can serve it (compared by seconds, so label case never lies); unknown labels refuse outright; symbols with no detectable ladder yet are skipped — START's per-symbol blockers remain the fail-late gate. Refusal surfaces through the existing headless path as `setup refused: …` and voids no state.
- **Relabel removal**: `SymbolRepository.get_candles_timeframe` returns no candles for finer-than-base or non-aligned requests (the old `"3m"`-returns-rows probe behavior is now pinned as returning none by test); `MarketDataService.get_bars` raises `Unsupported timeframe … (available: …)` for the same inputs instead of mislabeled rows.
- **Cache freshness**: `validate()`'s key now includes `SymbolRepository.store_fingerprint` — `(mtime_ns, size)` per configured symbol — so a cached "no market data" verdict retires as soon as rows land, whichever writer wrote them (the explicit sync-path invalidation remains as a second belt).
- **Still open from 6.3**: client-side ladder synthesis/append (Rust UI/domain — increment 2); stale cosmetic `session_timing.status` store text.
- **Verification**: new tests `src/market/tests/test_symbol_repository_timeframe.py` (7) and `src/app/tests/test_live_timeframe_authority.py` (4); full pytest suite PASSED (1622 collected, 0 failures/errors); `ruff check` + `ruff format --check` PASSED; `pyright --threads` 0 errors; validators (imports, structure, language ownership, architecture, authority, routes, repo-graph, scope, context) PASSED. Order path, risk controls, consent/arming untouched.

**6.5 Phase 5 increment 2 — client ladder authority implemented and verified (local code only; nothing deployed, nothing armed, no order placed).**

- **Shell fallback removed** (`crates/vayren-shell/src/remote_live.rs`): the fabricated default ladder is gone; `available_timeframes` is forwarded only when the gateway states it — an absent key now leaves the previous server-stated ladder in place.
- **Domain synthesis + append removed** (`crates/vayren-domain/src/live.rs`): no default list; an explicit empty list is honored as "nothing supported"; the server's established selection (`selected_timeframe`) selects only when the ladder can serve it — a server-unknown value is never appended and never selected; ladder replacement re-resolves the selection by value, not by stale index.
- **Submission safety (server-side hardening)**: the setup payload can only carry a server-supported timeframe or `""`; `configure()` (`src/app/services/live_trading_service.py`) now treats empty/whitespace as "no change intent" so an empty value can never wipe the stored config (headless already folds `""` to `None`).
- **Verification**: 4 new domain tests + 1 new shell test + 1 new Python test (names in `docs/universal_strategy_engine.md` §11); `cargo test -p vayren-domain` 297 passed; `cargo test -p vayren-shell --lib` 64 passed; `cargo test --workspace` green; `cargo fmt --all -- --check` clean; full pytest **1618 passed, 5 skipped**; ruff + pyright clean on touched files; all validators PASSED.
- **Gate environment note**: `make check`'s `rust` step cannot replace `target/release/vayren_core.dll` while the running desktop app holds it (PID 12388, started 11:24) — the equivalent decomposed steps (release kernel build to a scratch `--target-dir`, debug shell build, workspace tests, fmt check) all pass. `make lint`'s repo-wide format check flags the unrelated parallel-session file `src/broker/registry.py`; the increment's own files are clean and untouched elsewhere.
- **Prepared, not implemented**: increment 3 contract fields + registry isolation (`docs/universal_strategy_engine.md` §2.1, §3.1) and the two-mode cleanup inventory/sequence (§1.1, held for operator sign-off; `~100` PAPER/SANDBOX reference sites across `src/` + `crates/`).
- **Still open from 6.3**: stale cosmetic `session_timing.status="RUNNING"` store text; reconciler not wired into `LiveSession` (increment 6).

**6.6 Phases 3–6 — universal strategy contract, registry isolation, strategy-list removal, two-mode cleanup (implemented & verified, local code only; nothing deployed, nothing armed, no order placed).**

- **Universal strategy contract (Phase 1, §12.1)**: `StrategyDefinition` gains five additive defaulted fields — `supported_timeframes`, `required_warmup_bars`, `required_capabilities`, `supported_modes=("BACKTEST","LIVE")`, `last_error`. `__post_init__` validates warmup ≥ 0, modes ⊆ `("BACKTEST","LIVE")`, sanitized tuples; `with_*` copies use `dataclasses.replace`; `inspect_strategy()` precedence: declaration > legacy `LIVE_*` attribute, empty falls back. `supports_live` = capability, not readiness.

- **Registry failure isolation (Phase 2, §12.2)**: `sync_storage` records per-strategy load errors via `load_errors()` (no payloads); one broken strategy never blocks others. `LiveSession.register_strategy` captures `logic_factory()` exceptions into a named, fail-closed contract with `init_blockers()`; `degraded` flag on session; START blocks only when zero strategies initialize.

- **Fabricated strategy list removed (Phase 3, §12.3)**: shell `init_remote_state` + `apply_remote_sections` no longer seeds five names; domain `apply_snapshot` no longer synthesizes default list; explicit empty list clears picker. Tests: absent, empty, populated.

- **Two-mode cleanup (Phase 4, §12.4)**: **PAPER/SANDBOX fully removed**:
  - Rust kernel: `ExecutionMode::Backtest|Live`; `resolve_mode` refuses ungated LIVE (no downgrade); `MODE_DOWNGRADE` → `MODE_REFUSED`.
  - Python: `ExecutionMode.BACKTEST|LIVE`; `parse_mode` refuses legacy labels; `resolve_mode` raises `LiveModeError` with missing gates; `resolve_broker` rejects BACKTEST (no venue) and LIVE without adapter; simulated venues `PaperBroker`/`SandboxBroker` + `native_settle_decision` **deleted**.
  - Domain/UI: `ExecMode` (domain, view_model, shell, system-view), broker `Environment` (vocab, auth, selection) → `LIVE` only; legacy values fail closed.
  - Remote: server/snapshot/server_ops accept `BACKTEST|LIVE` only.
  - Legacy config: default `BACKTEST`; stored `PAPER`/`SANDBOX` refused at parse (never reinterpreted as LIVE).

- **Isolation & lifecycle (Phases 5–6, §12.5)**: per-strategy `init_blockers()`, session `degraded` flag; LIVE refusal named (`VENUE_REFUSED` + `LiveModeError`); no simulated fallbacks.

- **Verification**: `cargo test --workspace` **749 passed, 0 failed**; full pytest **0 failures, 0 errors**; ruff clean on all touched files; `pyright --threads` 0 errors; all validators PASSED (structure, imports, language ownership, architecture, authority, routes, repo-graph auto-fixed, scope, context). `make check` canonical `rust` step blocked by running desktop app holding `vayren_core.dll` (PID 12388) — scratch-target kernel build, debug shell build, workspace tests, fmt all pass as supplementary evidence.

- **Prepared for increment 7**: reconciler wiring, dedup/ordering/bounded queues, live adapter validation/recovery.

- **Still open**: stale cosmetic `session_timing.status="RUNNING"` store text; reconciler not wired into `LiveSession`.
