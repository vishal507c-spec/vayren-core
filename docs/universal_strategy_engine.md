# Universal Strategy Engine — Target Architecture & Incremental Plan

Phase 5 definition (2026-10-18). This document defines the target shape of the
strategy engine and the order in which it is reached. `docs/architecture.md`
remains the current-state brain; `docs/redesign_audit.md` holds the gap
evidence this plan retires. Nothing in this plan is deploy automation: no
orders, no arming, no pushes happen as part of it.

Status at definition time:

| # | Increment | Status |
|---|---|---|
| 1 | Timeframe authority (write-time gate, no relabeling, cache freshness) | **DONE — verified** |
| 2 | Client-side ladder authority (Rust UI/domain) | **DONE — verified (§4.2, §11)** |
| 3 | Strategy contract fields + registry isolation | **DONE — verified (§2.1, §3.1, §11)** |
| 4 | Exactly two modes (PAPER/SANDBOX removal) | **DONE — verified (§1.1, §11)** |
| 5 | Lifecycle states + truthful dashboard | **DONE (incl. in 3+4): per-strategy init isolation, `init_blockers()`, `degrade` surfacing, truthful VENUE_REFUSED journal** |
| 6 | Pipeline hardening (reconciler wiring, dedup/ordering/bounded queues) | Planned |
| 7 | Live adapter validation/recovery | Planned |

## 1. Operating modes — exactly two

- **BACKTEST** — historical replay against stored candles only. Deterministic.
  Never touches broker execution (Lab / backtest engine flow).
- **LIVE** — real broker, real money. Explicit operator consent, mandatory
  risk checks, broker reconciliation. Never depends on backtest-only data;
  missing backtest data can never block LIVE.

Current vocabulary to retire (verified locations):

- `LiveTradingService.configure()` accepts `mode in ("PAPER", "LIVE")`, and
  PAPER is the default (`src/app/services/live_trading_service.py:684`).
- `headless._live_action` mode branch speaks PAPER/LIVE
  (`src/app/headless.py:535-540`).
- Paper/sandbox surfaces listed in the audit (`src/execution/modes.py`,
  broker factory degradation path, UI chips) — see `docs/redesign_audit.md`.

**Rule:** no simulated fills anywhere in the runtime. Any inability to trade
live must surface as an explainable blocker (component, reason, remediation) —
never as a simulated venue quietly taking the order's place.

**Tradeoff (hold for decision):** removing PAPER removes the historical
fallback where an unavailable LIVE venue could still "run" against a
simulated venue. After removal, LIVE with no venue is START-blocked — no
fills at all. That is the intended posture (silence over simulation), but it
changes runtime behavior, so implementation waits for explicit operator
sign-off.

### 1.1 Two-mode cleanup — inventory & removal (DONE, §12.4)

Machine-counted reference sites (PAPER/SANDBOX tokens across `src/` +
`crates/`): **~100 files**. Removal order (each step gated by the full check):

1. **Kernel (Rust — owns the mode decision)** —
   `crates/vayren-core/src/execution_engine.rs`: `enum ExecutionMode { Paper,
   Sandbox, Live }`, `resolve_mode`'s degrade-to-PAPER branch,
   `resolve_mode_mask`, and their tests; `live_session.rs` paper-fill /
   paper-capital paths; `execution_events.rs`, `lib.rs` guards.
2. **Python vocabulary** — `src/execution/modes.py` (`ExecutionMode`),
   `src/execution/broker/factory.py` + `paper.py` + `sandbox.py` (simulated
   venues — whole modules), `src/execution/runtime/session.py`
   (`SessionConfig.mode` default PAPER, paper capital/slippage fields),
   `src/execution/safety.py`, `control_plane.py`,
   `src/execution/runtime/inspector.py` (`SUPPORTS_PAPER`),
   `src/strategy/execution_router.py`, `src/strategy/models/execution.py`,
   `src/strategy/eligibility_engine.py` (`PAPER`/`SANDBOX` routing),
   `src/app/services/live_trading_service.py`, `src/app/headless.py`,
   `src/remote/{server,snapshot,server_ops}.py`.
3. **Rust domain/UI** — `crates/vayren-domain/src/live.rs`
   (`ExecMode::Paper/Sandbox`, unknown→Paper mapping),
   `broker_connection.rs`, `view_model.rs`, `portfolio.rs`, the six view
   crates and `shell.rs` strings/chips.
4. **Test sweep** — mode-named tests across `src/**/tests/` and crate test
   modules updated to the two-mode vocabulary (behavior assertions, not just
   renames).

Behaviour after removal: LIVE with unmet gates is START-blocked with the
explicit blocker list (never a PAPER run); combined with the existing
consent/arming gates, no code path remains that can produce a fill without a
real venue order.

## 2. Strategy contract (universal interface)

Target capabilities, all **additive and defaulted** so existing compiled
strategies (same `StrategyRecord.code` driven through
`LiveStrategyDriver.on_candle → logic.on_bar`) keep working unchanged:

| Capability | Today (OBR C1C4) | Increment |
|---|---|---|
| Identity + version | name, version on record | 3 (surface in snapshots) |
| Lifecycle init/shutdown | implicit (compile + on_bar) | 3 (explicit hooks, defaulted no-ops) |
| Config/parameter schema | parameters dict | 3 (declared schema, validated) |
| Symbols + timeframes | universe store + declared `timeframe` | 1 (servability gate) + 3 (declaration surfaced) |
| Indicators + warmup | fixed live warmup feed (120 bars) | 3 (declared warmup) |
| Readiness/health | compile + runtime status | 3 (health reasons per strategy) |
| Candle processing | `on_bar(bar)` | exists |
| Signals (with reason) | engine emits intents | exists (reason strings preserved) |
| Position awareness | session state per symbol | 3 (query surface) |
| Risk requirements | central risk engine | exists (single authority, unchanged) |

### 2.1 Prepared contract fields (Increment 3 — additive, backward-compatible)

`StrategyDefinition` (`src/strategy/models/definition.py`, frozen dataclass)
gains ONLY defaulted fields, so every existing constructor call site (5 in
`src/` incl. `registry.py`, `execution/runtime/session.py`,
`live_trading_service.py`) and every stored record keeps its exact meaning:

| Field | Type | Default | Semantics |
|---|---|---|---|
| `supported_timeframes` | `tuple[str, ...]` | `()` | Declared servable set; `()` = derive from logic (`LIVE_TIMEFRAMES`) / any. Config validation refuses a client timeframe outside a non-empty set BEFORE anything is persisted. |
| `required_warmup_bars` | `int` | `0` | Declarative history requirement; engines use `max(declared, logic.warmup())`. `0` = no declaration (today's behaviour). |
| `required_capabilities` | `tuple[str, ...]` | `()` | Broker/data capability names validated against live venue facts (mirrors `StrategyRuntimeContract.required_broker_capabilities`). |
| `supported_modes` | `tuple[str, ...]` | `("BACKTEST", "LIVE")` | A strategy may declare BACKTEST-only; configuring it for LIVE is refused with an exact reason. Default = both → no behaviour change. |
| `last_error` | `str` | `""` | Registry-isolation surface: set with `runtime_state="ERROR"` when registration/logic creation failed; cleared on success. |

Bridging rule (keeps both worlds working): `inspect_strategy()`
(`src/execution/runtime/inspector.py`) prefers a non-empty declared field
over the corresponding `LIVE_*` logic attribute; an empty declaration falls
back to today's attribute probing — no strategy must change to keep running.
Implementation must extend the six `with_*` copies plus
`registry.duplicate`/`create_or_update` (mechanical); prefer a
`dataclasses.replace`-style single copy helper so future fields cannot drift
out of the copies.

## 3. Registry and failure isolation

- Current: `strategy.registry` — registration, `contains`/`get`, compile
  check through `_compiled(record)` inside `validate()`.
- Target: per-strategy validation at registration; **independent failure
  isolation** — one strategy's init/compile failure degrades THAT strategy
  only (`DEGRADED`/`SAFE_STOP`), never a global halt and never a silent skip.

### 3.1 Registry failure isolation (prepared, Increment 3)

- **Registration (explicit API)** keeps failing fast (`StrategyRegistryError`)
  — an explicit `register`/`create_or_update` call still raises.
- **Bulk/load paths** (`StrategyRegistry.sync_storage`) keep their
  per-record try/except and now SURFACE the failure instead of log-and-skip
  only: a quarantined map (`load_errors()` → `{record: reason}`) plus
  `last_error` on the affected definition; one broken strategy (compile
  error, unknown kind, bad params) never blocks the others and never
  vanishes silently.
- **Session registration** (`LiveSession.register_strategy`,
  `src/execution/runtime/session.py:245`): a `logic_factory()` exception is
  captured into a per-strategy contract with
  `missing=("logic_creation_failed: ...",)`; other strategies still register
  and START reports it as a per-strategy blocker. Fail-closed: a failed
  strategy can never tick — it is a named blocker, never a silent skip.

## 4. Data plane — strict separation

- **Backtest data**: stored candle files (downloader-owned layout), historical
  only. Replay is deterministic (`execution/market_data/replay.py`).
- **LIVE data**: broker adapters + the live store tail
  (`execution/market_data/sqlite_tail.py`) with the Rust normalizer
  (`broker_feed`: bucket/fold_tick).
- Invariant: LIVE blockers are computed from live facts only; missing
  backtest-only data never appears as a LIVE blocker (the per-symbol
  "no market data" blocker is about the live store's own coverage, with an
  explicit sync path as remediation).

### 4.1 Timeframe authority (Increment 1 — implemented & verified)

- **Server-authoritative ladder**: Rust `available_timeframes` — a symbol's
  base duration and its whole multiples; nothing invented.
- **Write-time gate**: `LiveTradingService._require_servable_timeframe`
  refuses a client-origin timeframe BEFORE it is assigned or persisted;
  unknown labels are refused outright; a symbol with no detectable ladder
  (no store yet) is skipped here and still gated by START's per-symbol
  blockers. Refusals surface as `setup refused: <reason>` notes through the
  existing headless action path.
- **No relabeling anywhere**: `SymbolRepository.get_candles_timeframe`
  returns no candles when the requested value cannot honestly be produced
  from the base rows; `MarketDataService.get_bars` raises the actionable
  `Unsupported timeframe ... (available: ...)` error instead of returning
  base rows under a finer label.
- **Cache freshness**: `validate()`'s cache key now rides per-symbol store
  fingerprints (`SymbolRepository.store_fingerprint` — `(mtime_ns, size)`),
  so a verdict can never outlive the rows it was computed from, regardless
  of which writer changed them.
- **Client side (Increment 2 — DONE, §4.2)**: both synthesis sites are gone;
  the picker offers only server-stated values.

### 4.2 Client ladder authority (Increment 2 — implemented & verified)

- **Shell translation** (`crates/vayren-shell/src/remote_live.rs`): the
  fallback ladder insertion is REMOVED — `available_timeframes` is forwarded
  only when the gateway states it; an absent key leaves the previous
  server-stated ladder in place downstream.
- **Domain ingest** (`crates/vayren-domain/src/live.rs`): the synthesized
  default list is gone; an explicit empty list is honored as "nothing
  supported" and a key-absent payload keeps the previous ladder. The server's
  `selected_timeframe` selects only when the ladder can serve it — a
  server-unknown value is never appended and never selected; the picker
  stays unselected instead. Ladder replacement re-resolves the previous
  selection BY VALUE (indices are meaningless across lists).
- **Submission safety**: with selection restricted to server values (or
  none), the setup payload can only carry a server-supported timeframe or
  `""`; `""` is a proven no-op server-side (`headless._opt_str` folds it to
  `None`, and `configure()` now treats empty/whitespace as no-change intent —
  it can never wipe the stored config).
- **Observed adjacent fabrication (Increment 3 candidate)**: the shell still
  injects a hardcoded five-name strategy list when the gateway states none
  (`remote_live.rs::apply_remote_sections`, `if state.strategies.is_empty()`).
  Same fabrication class, different surface — the registry-backed list should
  become the only source; tracked here so it is not lost.

## 5. Event pipeline

Target flow: data → validation → candles/indicators → signal → risk → order
intent → venue → reconciliation, with dedup, ordering guarantees, and bounded
queues; **no signal path may bypass risk**.

Current mapping: Rust normalizer produces closed buckets; `LiveSession`
drives strategies per symbol; the Rust risk engine is the single authority;
venue execution is broker-gated; a persisted engine snapshot tracks submitted
orders. Known gaps (audit): the reconciler is not wired into `LiveSession`
lifecycle; queue bounds and dedup guarantees need an explicit audit pass.

## 6. Risk & execution invariants (preserved, untouched by this plan)

Existing limits only (no invented monetary limits); one operator-approved
order at a time; unique persisted order-intent id; no blind retries; no
fallback orders; reconcile before any further action; only broker-confirmed
state is reported; explicit consent/arming stays mandatory. Increment 1 did
not touch any of these.

## 7. Lifecycle states and dashboard truth

Target state machine: `STOPPED → INITIALIZING → READY → RUNNING → DEGRADED →
SAFE_STOP`.

- STOPPED — configured, validated, not armed.
- INITIALIZING — start accepted; sessions/venues building.
- READY — validated + armed, not ticking yet.
- RUNNING — ticking; strategies producing signals through risk.
- DEGRADED — per-symbol/session fault while the rest continues; exact reason
  surfaced (never a silent partial run).
- SAFE_STOP — operator halt / kill switch.

Dashboard must show: uptime, per-strategy state, data freshness, blockers,
order lifecycle — and must never display RUNNING for mere
connected/initialized truth.

## 8. Remaining increments

- **2–4 — DONE** (§4.2, §11, §12).
- **5. Lifecycle + dashboard — DONE** with 3+4 (init isolation, truthful
  blockers, VENUE_REFUSED journal).
- **6. Pipeline hardening** — reconciler wired into session lifecycle; dedup,
  ordering, bounded queues verified per component.
- **7. Live adapter validation/recovery** — timestamp/duplicate/gap/staleness
  checks with explicit recovery in the live adapters (Rust-owned where the
  normalizer already lives).

## 9. Decisions and tradeoffs (explicit)

1. **PAPER removal** — removes simulated-fill fallback; LIVE degradation
   becomes blockers only. Held for sign-off.
2. **Write-time gate scope** — only client-supplied values are validated at
   write time; a strategy's declared timeframe is not re-verified at
   selection (START's validator still gates per symbol). This keeps
   "configure first, sync data after" flows possible.
3. **Relabel removal** — reads that previously returned base rows under a
   wrong label now return no data (engine reader) or an actionable error
   (app reader). Charts show honest absence; analysis over mismatched pairs
   refuses instead of computing on wrong bars.
4. **Fingerprint cost** — one `stat` per configured symbol per
   `validate()` poll (bounded by symbol count, microseconds each) buys
   guaranteed cache freshness.
5. **Client selection when the stored value is unservable** (increment 2) —
   the picker shows no selection instead of displaying the drifted value; the
   operator picks a supported value (the server gate then accepts it). The
   server keeps its stored config untouched meanwhile — the client performs
   no repair.
6. **Empty timeframe payload** (increment 2) — the Rust side keeps sending
   `timeframe: ""` (legacy full-payload shape); headless folds `""` to `None`
   and `configure()` now ignores empty/whitespace explicitly. The no-op is
   enforced server-side, twice, so an empty value can never wipe config.
7. **"Not stated" vs "stated none"** (increment 2) — a payload without the
   ladder key keeps the LAST server-stated ladder; only an explicit empty
   list clears it. The client must not confuse silence with an empty set.

## 10. Increment 1 evidence (this session)

- New regression tests: `src/market/tests/test_symbol_repository_timeframe.py`
  (7 tests — relabel, non-aligned, unknown label, base read, aggregation,
  ladder-is-the-contract, fingerprint) and
  `src/app/tests/test_live_timeframe_authority.py` (4 tests — refusal before
  persist, unknown-label refusal, servable change persists, cache retires on
  store change). Both green.
- Full pytest suite: PASSED (1622 collected, 0 failures/errors; only
  pre-existing skips).
- `ruff check` + `ruff format --check`: PASSED. `pyright --threads`: 0 errors.
- Validators: imports, structure, language ownership, architecture gate,
  authority, routes, repo-graph (auto-fixed artifact), scope, context — PASSED.
- No orders placed, engine not armed, nothing deployed, nothing pushed; the
  order path and risk controls were not modified.

## 11. Increment 2 evidence (this session)

- New Rust regression tests (all green, run by name):
  - `vayren-domain::live::tests::snapshot_ladder_is_server_authoritative_and_never_synthesized`
    (explicit empty list honored; key-absent keeps the previous ladder; only
    server values select),
  - `…::snapshot_never_appends_an_unsupported_selected_timeframe`
    (`"3m"` against `["30m","1h"]` → not appended, not selected),
  - `…::snapshot_ladder_replacement_reselects_by_value_not_position`,
  - `…::host_setup_never_submits_an_unsupported_timeframe`
    (host-mode setup action carries `""`, never the drift value),
  - `vayren-shell::remote_live::tests::remote_sections_never_synthesize_a_timeframe_ladder`.
- New Python regression test: `test_empty_timeframe_is_a_no_op_not_a_wipe`
  (`src/app/tests/test_live_timeframe_authority.py`).
- Rust: `cargo test -p vayren-domain` 297 passed; `cargo test -p vayren-shell
  --lib` 64 passed; `cargo test --workspace` all green; `cargo fmt --all --
  --check` clean after formatting the touched files.
- Python: full pytest **1618 passed, 5 skipped** (247 s, 0 failures). Ruff
  check + format on all touched files clean. `pyright --threads`: 0 errors.
  All validators PASSED (structure, imports, language ownership, architecture
  gate, authority, routes, repo-graph auto-fixed, scope, context).
- Environment note: `make check`'s `rust` step could not replace
  `target/release/vayren_core.dll` — the desktop app (started 11:24) holds
  it; the equivalent decomposed steps (release kernel build to a scratch
  target dir, debug shell build, workspace tests, fmt check) all pass.
  `make lint`'s repo-wide format check flags the unrelated parallel-session
  file `src/broker/registry.py`; every file this increment touched is clean.
- No orders placed, engine not armed, nothing deployed, nothing pushed; no
  EC2 commands were issued in this increment.

## 12. Increments 3–6 evidence (this session)

### 12.1 Universal strategy contract (Phase 1)

`StrategyDefinition` (`src/strategy/models/definition.py`) gained five
additive, defaulted fields — `supported_timeframes=()`, `required_warmup_bars=0`,
`required_capabilities=()`, `supported_modes=("BACKTEST","LIVE")`,
`last_error=""`. `__post_init__` validates them (non-negative warmup,
non-empty mode subset, sanitized tuples); the six `with_*` copies now route
through `dataclasses.replace` so future fields cannot drift. Construction sites
and stored records keep their exact meaning. `inspect_strategy()` precedence
is deterministic: a non-empty declaration wins over the legacy `LIVE_*` class
attribute; empty falls back to it. `supports_live` stays a *capability*, never
readiness.

### 12.2 Registry failure isolation (Phase 2)

`sync_storage` now records each failure into `_load_errors` (surfaced via
`load_errors()` — identity + operation + reason, no payloads/secrets); one
broken strategy never blocks the others. `LiveSession.register_strategy`
captures `logic_factory()` exceptions into a named, fail-closed contract and
`init_blockers()`; the failed strategy never ticks and never stops the others;
START blocks outright only when *no* strategy could initialize.

### 12.3 Fabricated strategy list removed (Phase 3)

The shell and domain no longer synthesize a five-name strategy list: absent key
keeps the previous server-stated list; explicit empty clears. New tests pin
absent/empty/populated.

### 12.4 Two-mode cleanup (Phase 4)

PAPER/SANDBOX removed end-to-end:

- **Kernel** (`crates/vayren-core/src/execution_engine.rs`): `ExecutionMode` is
  `Backtest|Live`; `resolve_mode` refuses ungated LIVE (no downgrade target).
  Domain/UI `ExecMode` mirrored; `MODE_DOWNGRADE` journal → `MODE_REFUSED`.
- **Python**: `ExecutionMode` = `BACKTEST|LIVE`; `parse_mode` refuses legacy
  `PAPER`/`SANDBOX` labels explicitly; `resolve_mode` raises `LiveModeError`
  with missing gates; `resolve_broker` refuses BACKTEST (no venue) and
  LIVE-without-adapter; simulated venues `PaperBroker`/`SandboxBroker` +
  `native_settle_decision` **deleted** (the dead `native_paper_calculate_fill`
  bridge and kernel `sandbox_policy`/`vy_exec_paper_calculate_fill` retained
  for ABI stability, no production caller).
- **Domain/UI**: `ExecMode` (domain + view_model + shell + system-view) and
  broker `Environment` (vocab + auth + selection) reduced to `LIVE` only;
  legacy values fail closed.
- **Remote**: server/snapshot/server_ops accept `BACKTEST|LIVE` only.
- **Legacy config**: default persisted mode is `BACKTEST`; a stored `PAPER`/
  `SANDBOX` is refused at parse with an actionable message — never
  reinterpreted as LIVE. No migration re-labels a simulated mode to a real one.

### 12.5 Isolation (Phase 5) / Lifecycle (Phase 6)

- Live data/execution flow is mode-gated; LIVE with unmet gates is a named
  refusal (`VENUE_REFUSED` + `LiveModeError`), never a simulated run.
- Per-strategy failure isolation: `register_strategy` factory capture,
  `init_blockers()`, `degraded` property; engine RUNNING requires a started
  session with a resolved venue.

### 12.6 Live reliability, event pipeline hardening & broker reconciliation wiring (Increment 7–8)

Completed and verified end-to-end:

1. **Broker Reconciliation Wiring (`LiveSession`)**:
   - `start()`: Pre-validation broker reconciliation check that fails closed (`blocks_live=True`) if broker state cannot be reconciled.
   - `step()`: Periodic 30-second cadence broker reconciliation in LIVE mode via `reconcile_now()`.
   - `_submit()`: Transport uncertainty classification (`classify_exception(exc)`: `TIMEOUT`, `NETWORK`, `RATE_LIMITED`) marks order as `OrderState.UNKNOWN` without blind retries; deterministic refusals stay `REJECTED`.
   - `_pre_order_checks()`: Blocks submissions if identical `intent_id` exists or if any `UNKNOWN` order remains unresolved.
   - `_dispatch_broker_event()`: Legally transitions `UNKNOWN` orders via `ExecutionEngine.reconcile(client_order_id, target_state, ...)` when broker acks, fills, rejects, or cancels arrive.
   - `reconcile_now()`: Fails closed (`matched=False`) if broker is absent or transport throws; reconciles open orders against broker confirmed state and re-arms protective stop-loss orders only once aligned.

2. **Live Pipeline Hardening & Ordering**:
   - Event ordering: In `step()`, incoming raw events are sorted deterministically by `(timestamp, symbol, seq)` before ingestion.
   - Deduplication: Candle deduplication per `(symbol, timestamp)` via `_seen_candle_stamps`; signal deduplication per `(strategy_id, event_seq)` via `_processed_signals`.
   - Bounded queues & capacity controls: Emits `PIPELINE_LOAD_WARNING` when backlog exceeds 1,000 raw events; never silently drops critical events.

3. **Live Adapter Validation & Recovery (`BrokerFeedProvider`)**:
   - Input sanity: Validates prices are finite and strictly positive (`math.isfinite(price) and price > 0`); clamps negative volumes to 0.
   - Safe recovery: In `reconnect()`, restores all active symbol subscriptions via `self._face.subscribe(tuple(self._subscribed))` before marking feed ready.

4. **Stale Session Timing Correction (`LiveTradingService`)**:
   - Authoritative synchronization: In `_restore_config()` and `_persist_config()`, whenever engine status is not running, any persisted `"RUNNING"` text in `session_timing.status` is immediately corrected to `"STOPPED"`, `start_epoch` cleared to `None`, and `stopped_at` set.

### 12.7 Known remaining / Environment status

- `make check`'s canonical `rust` step cannot replace the release DLL while a running
  desktop app holds it (PID 12388) — the equivalent decomposed steps (release kernel build
  to a scratch `--target-dir`, debug shell build, workspace tests, fmt check) all pass.
- Desktop shortcut `C:\Users\visha\Desktop\VAYREN.lnk` verified and points to `vayren-shell.exe`.
- Strict two-mode architecture (`BACKTEST | LIVE` only) fully maintained; zero live orders
  placed; consent DISARMED; ₹100 risk limit intact.

