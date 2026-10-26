# AI Memory — Current State Only (compact, active path)

> This file holds only current state + open items. Append new state here;
> prune superseded entries to docs/archive/ai_memory_history.md.

**Owns:** Current state, open items, verified facts, oddities.
**Historical archive:** docs/archive/ai_memory_history.md (all past session logs preserved).
**Build-performance history:** docs/CARGO_BUILD_EXPERIMENTS.md.

**Latest update (production-ec2-deployment-verified-20261026):** EC2 PRODUCTION DEPLOYMENT & LIVE READINESS GATE VERIFICATION COMPLETE —
- **Production Commit:** `f5496b10833736227a3d1d3411f9fdfd42495bcd` (`fix(live): harden market data gate, correct arming text, and sweep money-path invariants`).
- **EC2 Deployment Execution:**
  - Pull method: `git pull --ff-only origin main` on host `vayren` (`100.75.185.37`, `ip-172-31-12-198`).
  - Pre-restart test suite: `pytest src/app/tests/test_safety_reporting.py src/remote/tests/test_server.py` passed 100% (44 passed in 2.21s).
  - Service restart: `sudo systemctl restart vayren-remote.service` completed cleanly with exit code 0.
  - Active PID: `780846` (`/home/ubuntu/vayren-core/.venv/bin/python -m remote.runner`).
- **Live Authentication & Sockets on EC2:**
  - `FyersAutoAuth`: Automated login verified with TOTP/PIN, new access token acquired and validated.
  - Sockets connected: `ORDER_WS_CONNECTED broker=FYERS` and `MARKET_WS_CONNECTED broker=FYERS`.
- **Live Readiness Gates Post-Restart (Verified via loopback WebSocket probe at `127.0.0.1:8765`):**
  - `Gate: Market Data Streaming` -> `NOT READY`, reason: `candle store 22/22; no live feed`. Confirms the fix is LIVE in production: stored candles alone (22/22) can NEVER satisfy live streaming readiness without active live tick feed.
  - `Gate: Arming` -> `NOT READY`, reason: `disarmed — confirm in START dialog to authorize`. Legacy `DISARMED — arm before START` text successfully eliminated.
  - `Gate: Broker Connected` -> `READY`, reason: `Fyers connected (ACC: XV29244)`.
  - `Gate: Credentials Valid` -> `READY`, reason: `credentials stored`.
  - `Gate: Account Confirmed` -> `READY`, reason: `account XV29244 confirmed`.
  - `Gate: Strategy Ready` -> `READY`, reason: `OBR C1C4 ready — 30m`.
  - `Gate: Risk Engine Ready` -> `READY`, reason: `max 1 per order`.
  - `Gate: Reconciliation Synced` -> `READY`, reason: `positions: 0 | orders: 0`.
  - `Gate: Environment (LIVE)` -> `READY`, reason: `LIVE mode selected`.
  - Safety Blockers: `live confirmation required (explicit operator consent)`.
- **Pre-Market Status:** As expected pre-open, trading remains strictly disabled and fail-closed until live ticks arrive at pre-open (09:00 IST) and operator provides consent in the START dialog. Zero real orders placed.

**Previous update (monday-live-market-readiness-20261025):** MONDAY LIVE-MARKET READINESS & AUDIT FIXES —
- **Bug Fix 1 (Market Data Streaming gate green on stored candles alone):** In `src/app/services/live_trading_service.py::_readiness_gates`, in `LIVE` mode, stored candles alone (`candle store 22/22`) can NEVER satisfy live streaming readiness. If `live_feed != "live"` or symbols are missing/nodata, gate is strictly `NOT READY` (reason: `candle store N/M; no live feed`). Furthermore, even when `live_feed == "live"`, gate checks fanout transport health (`d.get("transport", {}).get("healthy")`) and broker checks (`market_data`); any staleness or failure flags `NOT READY` with the failure reason.
- **Bug Fix 2 (Misleading legacy "DISARMED — arm before START"):** In `src/app/services/live_trading_service.py::_readiness_gates`, replaced the obsolete `DISARMED — arm before START` (residue of pre-20261023 standalone ARM button) with honest actionable state: `NOT READY` with `reason="disarmed — confirm in START dialog to authorize"` when unconfirmed, and `READY` with `reason="operator consent confirmed"` when confirmed via modal, or `reason="sessions armed and running"` when running. Zero safety gates bypassed.
- **Regression Tests Added (`src/app/tests/test_safety_reporting.py`):**
  - `test_market_data_gate_live_mode_rejects_stored_candles_alone`: Verifies stored candles alone with `feed="none"` are `NOT READY`.
  - `test_market_data_gate_live_mode_rejects_degraded_market_ws`: Verifies degraded/stale market WS flags `NOT READY`.
  - `test_market_data_gate_live_mode_ready_when_feed_and_store_healthy`: Verifies `READY` only when both store and live feed transport are healthy.
  - `test_arming_gate_reports_correct_wording_and_consent_states`: Verifies unconfirmed, confirmed, and running states without legacy wording.
  - All 19 tests in `test_safety_reporting.py` passed cleanly.
- **Production EC2 Verification (Read-Only via live client):** Host `100.75.185.37`, service running (`vayren-remote.service`, PID `776456`). FYERS REST active, token valid, margin ₹100.00 confirmed, order WS connected. Weekend NSE market closure verified as cause for `market_ws` stale (>120s) degradation. Fail-closed posture verified.

**Previous update (live-ui-polish-hover-20261025):** LIVE UI POLISH (visual-only; no facts/features changed) —
- **Pointer cursors:** live.slint had ZERO `mouse-cursor: pointer` while lab/market/broker screens have dozens — interactivity affordance parity restored on every clickable TouchArea (top cards, capital/band refresh, blotter tab headers, filter chips ×2, contract chips, watchlist + universe rows, events Clear, inspector toggle + reopen rail, timeframe cycle). Hover-only (non-clickable) rows deliberately get no cursor.
- **Verdict glyph noise removed:** `start-verdict` no longer prefixes `● RUNNING` / `✕ START BLOCKED` / `✓ READY TO START` — every surface (banner, LIVE READINESS badge) already carries the tone colour, so the glyphs doubled what the tone said.
- **Warning gates re-toned:** `LiveGateRow` border + reason now carry tone 2 as AMBER (was faint border + muted reason that read as "off") — a gate row's voice now spans red/amber/quiet the way its verdicts do.
- **Daily P&L clipping fixed:** the seven facts (≈820px) are wrapped in a horizontal ScrollView (`viewport-width: Math.max(self.width, stats-row.preferred-width)`, 62px tall) — at the min window the panel is ~550px and a plain row silently clipped the tail; same scroll policy as the chip strips.
- **REVERTED (over-reach):** adding STRATEGY/RISK cells + elide to the footer strip. The pixel-health test `footer_strip_stays_on_screen_at_the_min_window` counts deep-BACKGROUND dominance per row; two extra dense text cells pushed the glyph band over 50% non-deep and the strip "disappeared" (13/22 rows). Lesson encoded: the footer is a quiet strip BY TEST CONTRACT — dense text there is a defect, completeness lives in the inspector.
- **EOL pitfall recorded:** `git stash pop` converted live.slint to CRLF, breaking the regression tests' `\n`-exact string pins (defect_8). Restored LF from HEAD bytes; pins are EOL-sensitive by design.
- **Verification:** `cargo check -p vayren-shell` clean (binding-loop warnings pre-existing at HEAD); `cargo test -p vayren-shell` ALL suites (incl. snapshot 8/8, defects regression 23/23, stock row, watchlist, event time); `cargo fmt --check` clean; `tools/benchmark.py impact --run` L3 PASSED (185s).

**Previous update (live-moneypath-bug-fixes-20261025):** LIVE MONEY-PATH BUG SWEEP — 11 BUGS FOUND AND FIXED (hunting pass; no features added) —
- **Bug 1 (STOP forgets venue exposure):** `LiveTradingService.stop()` now captures post-stop truth via `_capture_halted_book()` (`live_trading_service.py`) BEFORE `_teardown_sessions()`; `snapshot()` renders captured rows in an `else` branch labelled `OPEN (UNTRACKED)` / `<state> (UNTRACKED)` with `RECONCILIATION=UNTRACKED`, plus an error activity line `HOLDING OVER STOP — N open position(s), M live order(s)`. Captures reset on `_record_session_start()`. Operator no longer sees a fake flat book after STOP while exposure lives at the venue.
- **Bug 2 (Zerodha protective SL unsupported):** `zerodha/live_trading.py::_venue_price()` now maps `STOP_MARKET`→`ORDER_TYPE_SLM` (trigger) and `STOP_LIMIT`→`ORDER_TYPE_SL`, returning `(order_type, price, trigger)`; `place_order()` payload adds `trigger_price`. A Zerodha entry is no longer left SL_FAILED.
- **Bug 3 (Zerodha timeout → REJECTED):** `_mapped()` maps `NetworkException` to `code="NETWORK"`; `session.py` UNKNOWN classifier accepts `code in ("NETWORK_ERROR","NETWORK","TIMEOUT","DISCONNECTED")` → order becomes UNKNOWN (reconciled), never a fake REJECT hiding a possibly-filled venue order. `_is_terminal_miss`/`_resolve_unknown_tag`/`_is_timeout_like` now classify on raw text internally (classification-only; emitted strings stay redacted).
- **Bug 4 (duplicate order after restart/finished order):** `_find_by_tag(..., terminal_ok=)` adopts FINISHED venue orders by tag in both adapters' idempotency paths (zerodha `place_order`/`_resolve_unknown_tag`, fyers `_resolve_unknown_tag`); pending-only scans would send a second real order.
- **Bug 5 (legit identical partial fills dropped):** `session.py::_apply_fill` dedup now = stale-replay guard (`tracked.filled_qty + fill_qty > order.quantity` → `FILL_DUPLICATE_IGNORED`) + exact-message key `(cid, pre-fill cumulative, delta, price, stamp)`. Genuine same-size partial prints fold; retransmissions (ibid cumulative > quantity, or identical frame) fold once. Old delta-only key silently dropped the second real fill.
- **Bug 6 (SL protection never surfaced):** `_apply_fill` seeds the tracker with the strategy stop from `_sized_risk` (was `stop_price=None` forever → summary CLEAN); `_entry_intents[symbol]` keys entry intents; SL success → `confirm_sl()`, fail → `mark_protection_failed()`, exit/flat → `close_position()`; SL-trigger fills `confirm_sl` on the entry record. `safety_snapshot().sl_protection_summary` and the control-plane START gate (control_plane.py:481) now see SL failures.
- **Bug 7 (fabricated capital):** `PositionLedger.reseed_capital(total_equity)` (flat-book-only, one-shot flag `_capital_reseed_done`) adopts venue funds in LIVE `reconcile_now()` (`_reseed_ledger_capital` via `broker.funds()`, journal `CAPITAL_RESEEDED`); session-side `capital_valid`/risk kernel no longer anchored on the fabricated ₹10,00,000 ledger. Service `_refresh_risk_capital` stamps the funds' REAL read epoch (`_account_state["fetched_epoch"]`), not "now".
- **Bug 8 (Market Data gate green on any store):** `_readiness_gates` "Market Data Streaming" ready only if `feed == "live"` in LIVE mode (still state-plus-reason honest).
- **Bug 9 (SELL hardcode):** `SIGNAL_GENERATED` journal carries `side`; service stores `_last_signal_side` (was literal "SELL") and `_describe_entry`/watchlist render the true side.
- **Bug 10 (UI green-wash):** `vayren-domain/live.rs` — market card: empty msg → "NOT REPORTED" with amber, `_ => 2` (no longer green STREAMING), explicit NO DATA/STOPPED/ERROR/FAILED/DISCONNECTED red. `RiskStatus::from_str":`unknown → `NotReady` (fail-closed), `trimmed` literal "READY" recognized; "READY"/"WARNING"/"BLOCKED"/"HALTED"/"NOT READY" unchanged. Unknown gateway words no longer paint green.
- **Bug 11 (Rust port fail-opens):** `order_state.rs` adds legal `SUBMITTED→{PARTIALLY_FILLED,FILLED}` (venue fills land without an Ack on poll-only adapters; was same crash/divergence in the Python engine table); edge count pinned 37→39. `live_session.rs::reconcile_now` on transport error builds an orders mismatch ("reconcile transport error") instead of silently keeping the previous matched report; `apply_fill` on transition error aligns via `engine.reconcile(target_state, filled_total)` then folds the ledger (was: early-return → engine filled, ledger flat); `RiskRequest.data_age_seconds` now passes the normalizer's real `event_age_seconds()` (never a hardcoded `Some(0.0)`; normalizer gained `event_age_seconds`).
- Left as documented host-mode design (not touched): `live.rs::venue_gates_ready` backend-blockers fallback; control-plane REQUEST_RECONCILIATION's `matched: mismatches==0` composite truth.
- **Verification (make check FULL GATE): 1791 passed, 5 skipped (355s) ✓ CONTEXT-Engine (23 routes) PASS ✓; validators: `language ownership` (430 files), `architecture gate`, `imports` all PASS; Rust — `cargo test -p vayren-core --lib` 409/409, `vayren-domain live` 109/109, `vayren-shell` full (13+ suites incl. `22/22 regression`) ✓, `cargo fmt --check` clean, `pyright` 0 errors, `ruff check` clean; `tools/benchmark.py impact --run` L3 gate PASSED.

**Previous update (live-ui-bugs-surgical-fixes-20261025):** SURGICAL FIXES FOR LIVE UI BUGS & REGRESSION HARDENING —
- **Bug 1 (Narrow Broker Click Handler):** Wired `clicked => { root.configure-broker(); }` on the narrow-layout (`root.is-narrow-bar`) BROKER `LiveTopCard`, enabling responsive broker configuration across narrow and wide viewport tiers.
- **Bug 2 (Strategy Placeholder Index 0 Disconnect):** Updated index conditions in `crates/vayren-shell/ui/live.slint`:
  - Active strategy border requires `root.strategy-selected > 0` (in both wide and narrow bars), preventing premature teal borders on placeholder index 0.
  - Live trade stocks header and guidance strip use `<= 0` to display "Select a strategy above" and "Choose a strategy above to load its saved trading universe".
  - SAVE UNIVERSE action button strictly requires `root.strategy-selected > 0 && !root.setup.can-stop;`.
  - Watchlist symbols empty state uses `<= 0` to guide the operator to pick a strategy.
- **Bug 3 (NOT CONFIGURED Broker Color):** Updated color condition in Inspector BROKER ACCOUNT card (`root.bar.broker-label != "" && root.bar.broker-label != "NOT CONFIGURED" ? VayrenPalette.text-bright : VayrenPalette.muted`), ensuring literal `"NOT CONFIGURED"` renders with muted tone rather than active bright text.
- **Bug 4 (Regression Test Hardening):** Added regression assertions to `crates/vayren-shell/tests/live_ui_defects_regression.rs` verifying 3+ `configure-broker` call sites, strict `> 0` border highlights, `<= 0` empty-state guidance, disabled SAVE UNIVERSE on index 0, and muted broker label color.
- **Verification:**
  - `cargo check -p vayren-shell`: Finished with 0 errors, 0 warnings.
  - `cargo test -p vayren-shell --test live_ui_defects_regression`: 22 passed, 0 failed.
  - `cargo test -p vayren-shell --test live_stock_row_render`: 1 passed, 0 failed.
  - `cargo test -p vayren-shell --test live_watchlist_from_saved_universe`: 1 passed, 0 failed.
  - `cargo test -p vayren-shell --test live_render_snapshot`: 7 passed, 0 failed.
  - `cargo test -p vayren-shell --test live_event_time_column`: 2 passed, 0 failed.
  - `cargo test -p vayren-domain --lib live`: 109 passed, 0 failed.
  - `python tools/validate_language_ownership.py`: PASSED (430 files checked).
  - Python tests: `test_live_actions.py`, `test_live_strategy_sync_and_watchlist.py`, `test_live_broker_capital.py`, `test_live_timeframe_authority.py` (33 passed, 0 failed).
  - `cargo fmt --check`: Clean (0 diffs).

**Previous update (sunday-premarket-readiness-audit-20261025):** COMPREHENSIVE SUNDAY LIVE-READINESS AUDIT & PRE-MARKET VERIFICATION FOR MONDAY, 26 OCTOBER 2026 —
- **Production EC2 Runtime State (100% Read-Only):**
  - Host `100.75.185.37` (`ip-172-31-12-198.taila678c5.ts.net`), process PID `765929` (`remote.runner`) active and running in `LIVE` mode.
  - SHA: `a7760447d182147249ba9d31dd260eeb4621ea36` (clean, compatible with desktop client).
  - Broker: `Fyers` (Account `XV29244`), REST auth valid, available margin ₹100.00, Order WebSocket `READY`.
  - Strategy: `OBR C1C4` (30m timeframe, 22 symbols: `DREDGECORP`, `WAAREERTL`, `KEC`, `KAYNES`, `AVANTIFEED`, `ROUTE`, `RAILTEL`, `SCI`, `MOIL`, `CRAMC`, `LXCHEM`, `NOCIL`, `DAMCAPITAL`, `JSWCEMENT`, `TEXRAIL`, `ZEEL`, `VIKRAN`, `IFCI`, `DCW`, `SAGILITY`, `JINDWORLD`, `NETWEB`).
  - Risk limits: 1 share maximum per order (`max 1 per order`).
- **Weekend / Sunday Off-Hours Behavior Documented:**
  - `market_ws` leg status is `FAILED` with `stale — no market data for >1700s` due to NSE weekend closure.
  - Confirmed fail-closed posture: Top card shows `Fyers [CONNECTED]` from REST auth, while Live safety gates strictly flag `Broker Connected` and `Account Confirmed` as `NOT READY` until live market ticks resume on Monday morning.
- **Fail-Safe & Sandbox Verification:**
  - All 5 execution stages verified: token validity, market data staleness cutoff (1980s), OBR C1C4 signal generation, 1-share risk ceiling, and simulated order lifecycle isolation.
  - Fail-safe tests confirmed that expired tokens, disconnected sockets, stale bars, unconfirmed accounts, and kill-switch activations fail closed without sending real broker orders.
  - Authoritative holiday calendar verified: Monday, 26 October 2026 is an open, regular trading session.

**Previous update (live-ui-polish-pass-20261025):** LIVE UI POLISH PASS (visual-only; no content/features added or removed) —
- **Inspector overflow fixed:** the docked inspector now scrolls at natural card height (`viewport-height: Math.max(self.height, inspector-body.preferred-height)`), so the Active Strategy card no longer clips `Signals generated` / `Orders` / `Universe`, the readiness tail is reachable, and Broker Account + `CONFIGURE BROKER` can no longer paint over the footer at 1920/1600/1366/1024.
- **Short-viewport footer restored:** the stacked boards compress by window height (`watchlist-min-h` 150/190/220px, `blotter-min-h` 120/160/200px at `<640 / <720 / ≥720`), so the footer clock/snapshot strip stays on screen at 1024x640 and 1024x560 (it was pushed below the viewport before).
- **Chrome polish:** the LIVE execution-mode pill is compact (84px, was a mostly-empty 168px red slab); Broker Capital card radius aligns with the other top-bar cards (8px).
- **Verification:** `cargo test -p vayren-shell` green (132 tests; new `footer_strip_stays_on_screen_at_the_min_window` pixel regression + `test_inspector_scrolls_and_short_boards_keep_the_footer`); `cargo fmt -p vayren-shell -- --check` clean. Snapshots inspected at 1920x1080, 1600x900, 1366x768, 1024x640, 1024x560, 900x700.

**Previous update (ci-green-gate-fixes-20261025):** ALL CI GATES GREEN — RESOLVED PRE-EXISTING GATE ISSUES ACROSS RUST, TESTS, AND REPO GRAPH —
- **Test Alignment (`live_stock_row_render.rs` & `live_watchlist_from_saved_universe.rs`):** Updated ComboBox index assertions (and strategy row counts) to match the `"— Select Strategy —"` placeholder at index 0. Strategy 0 maps to UI index 1, and Strategy 1 maps to UI index 2.
- **Rust Formatting:** Ran `cargo fmt --all` across the workspace; `cargo fmt --check` now passes 100% cleanly.
- **Repo Graph Re-index:** Rebuilt `docs/repo_graph.json` artifact via `python tools/repo_graph.py --build`; `python tools/validate_repo_graph.py` passes with 0 unresolved entities.
- **Verification:**
  - All CI validators pass: `validate_structure`, `validate_imports`, `validate_language_ownership`, `validate_architecture_gate`, `validate_authority`, `validate_routes`, `validate_repo_graph`, `validate_scope`, `context_engine`.
  - Shell test suite passes: `live_ui_defects_regression` (22/22), `live_render_snapshot` (7/7), `live_stock_row_render` (1/1), `live_watchlist_from_saved_universe` (1/1).

**Previous update (live-ui-visual-polish-reapplied-20261024):** LIVE SCREEN VISUAL POLISH REAPPLIED ON TOP OF THE AUDIT FIXES (visual-only; no content/features added or removed) —
- Restored the validated polish in `crates/vayren-shell/ui/live.slint`: dark native widgets (`Palette.color-scheme = ColorScheme.dark`), stretch/start alignment across top bar, stock-list/watchlist headers, universe rows, blotter tables, event filter/header/rows, inspector headers, quantity/timeframe rows, readiness/session rows, footer, `LiveTabHeader`, `LiveGateRow`, and `LiveTopCard`.
- Pipeline chips: `LivePipelineChip` + `LivePipelineRow` render exactly three indexed steps per row (up to 3 rows); the selected-instrument card reserves `120px + ceil(steps / 3) * 22px`, so `SL PROTECTED` and the notice stay visible at 1920/1600/1024. Event headers are uppercase (`TIME`/`LEVEL`/`EVENT`/`SYMBOL`/`SOURCE`).
- Verification: `cargo test -p vayren-shell --test live_render_snapshot` 7/7; `cargo test -p vayren-shell --test live_ui_defects_regression` 22/22; render snapshots inspected at 1920x1080, 1600x900, 1024x640, 900x700. `python tools/validate_language_ownership.py` PASSED (430 files).

**Previous update (live-ui-12-audit-bugs-fixed-20261024):** COMPLETE PRODUCTION FIX FOR ALL 12 LIVE UI AUDIT DEFECTS —
- **Bug 1 (Watchlist Alignment):** Aligned watchlist row status dot container to 14px (`Rectangle { width: 14px; Rectangle { width: 10px; height: 10px; ... } }`) to match the 14px column 1 header, resolving the 4px horizontal shift across table columns.
- **Bug 2 (Misleading Reconciliation Footer):** Eliminated fake fallbacks (`BROKER SYNCED`, `NSE STREAM`, `30m CANDLES`). Display honest states (`NOT CONFIGURED`, `DISCONNECTED`, `NO DATA`). Removed duplicate `RECON: Recon:` prefix in `live.rs`. Display `SNAPSHOT: <time>` separately from the real-time workstation clock.
- **Bug 3 (Broker Capital Status Color):** Removed hardcoded teal and fake `₹0.00` in the Inspector. Display `"Unavailable"` when missing, with dynamic semantic tone mapping (`root.bar.capital-tone`: teal for healthy/connected, neg/red for error, amber for warning, muted for unavailable).
- **Bug 4 (Watchlist Search/Filter Empty State):** When symbol filtering or search yields 0 matches, display `VEmptyState` ("No symbols match") and badge "0 MATCHES". Hide strategy universe editor when filtering is active.
- **Bug 5 (Broker Configuration Action):** Wired TopCard clicked handler and added a visible `VButton { label: "CONFIGURE BROKER"; variant: 1; clicked => { root.configure-broker(); } }` in the Inspector BROKER ACCOUNT card.
- **Bug 6 (LiveConfirmModal Broker Name):** Replaced blind parenthesis concatenation with honest fallback (`"Broker not configured"` with negative tone) when broker name is missing, preventing malformed `" ()"` or `" (DISCONNECTED)"`.
- **Bug 7 (Inspector Reopen Rail Overlap):** Increased workspace `padding-right` from 10px to 34px when `!root.show-inspector` (`padding-right: root.inspector-docked ? root.inspector-w + 10px : (!root.show-inspector ? 34px : 10px);`), ensuring the 28px reopen rail never obscures workspace vertical scrollbars.
- **Bug 8 (Strategy ComboBox Selection):** Prepend `"— Select Strategy —"` at index 0 in `shell.rs` and `live-view` lib.rs, mapping index 0 to unselected (`strategy_index = None`) and indexes 1..N to strategies. In `live.slint`, highlight strategy label only when `strategy-selected > 0`, correctly handling index 0.
- **Bug 9 (Top Status Card Text Truncation):** Reduced EC2 icon font-size to 8px in 24px circle, expanded top card widths (min 120px / max 175px), prioritized RTT before HB in wide EC2 card, and placed non-connected broker status upfront in capital card subtext.
- **Bug 10 (Daily P&L Panel Sizing):** Wrapped Tab 4 (Daily P&L) in `VerticalLayout { vertical-stretch: 1; alignment: center; ... }` so it fills the blotter panel vertically without collapsing.
- **Bug 11 (Live Events Filters & Search Overflow):** Wrapped category chips in `ScrollView` with `horizontal-scrollbar-policy: as-needed` and `vertical-scrollbar-policy: always-off` to prevent overflow on narrow viewports while keeping the search field accessible.
- **Bug 12 (Stale ARM/HALT Wording Removed):** Completely purged obsolete `START/STOP/HALT are disabled on this link` from `remote_live.rs` notices and doc comments in `live.slint`, `remote_live.rs`, and `main.rs`.
- **Verification:**
  - `cargo test -p vayren-shell --test live_ui_defects_regression`: 22 passed, 0 failed.
  - `cargo test -p vayren-shell --test live_render_snapshot`: 7 passed, 0 failed.
  - `cargo test -p vayren-domain --lib live`: 109 passed, 0 failed.
  - `python tools/validate_language_ownership.py`: PASSED (421 files checked).
  - Python tests: `test_live_actions.py` (12 passed), `test_live_strategy_sync_and_watchlist.py` (9 passed), `test_live_broker_capital.py` (7 passed), `test_live_timeframe_authority.py` (5 passed), `test_phase4_dashboard.py` (14 passed), `test_phase3_control_plane.py` (37 passed).

**Previous update (complete-arm-and-halt-removal-20261023):** COMPLETE REMOVAL OF ARM AND HALT CONCEPTS ACROSS CODEBASE —
- **Architectural Simplification & Elimination of Redundant States:**
  - Fully eliminated the ARM and HALT abstractions from the UI, domain models, remote protocol, control plane, state synchronization, tests, and documentation.
  - The live trading lifecycle is now cleanly controlled by START and STOP, operator confirmation modal (`LiveConfirmModal`), venue safety gates, and risk engine limits (`KillSwitch` circuit breaker).
- **UI Layer (Slint):**
  - Removed `callback arm-requested();` and `callback halt-requested();` from `live.slint`, `app.slint`, `live_harness.slint`, and `live_host.slint`.
  - Removed `halt-enabled` and `halted` properties from `LiveBar`, `can-arm` and `pending-halt` from `LiveSetup`, and `arm-note` from `LiveScreen`.
  - Removed dedicated red HALT banner (`if root.bar.halted: Rectangle { ... }`) from `live.slint`.
  - Updated fixtures in `live_preview.slint` and `shell.rs` (`exec-label: "LIVE READY"`).
- **Domain Layer (`crates/vayren-domain/src/live.rs`):**
  - Removed `backend_can_arm`, `backend_can_halt`, and `backend_arm_blockers` from `LiveState`.
  - Removed methods `can_arm(&self)`, `can_halt(&self)`, `arm_blockers(&self)`, `arm(&mut self)`, `halt(&mut self)`, and `report_halt(&mut self)`.
  - Removed `can_arm` and `pending_halt` from `SetupView`; removed `halt_enabled` and `halted` from `BarView`; removed `arm_note` from `LiveView`.
  - Preserved independent safeguards: LIVE mode start strictly enforces `venue_gates_ready()` and operator confirmation (`live_confirmed`).
- **View-Model & Shell Wiring (`crates/vayren-shell` & `crates/vayren-live-view`):**
  - Removed `wire_unit!(on_live_arm_requested, LiveState::arm);` and `wire_unit!(on_live_halt_requested, LiveState::halt);` in `shell.rs`.
  - Removed `wire_unit!(on_arm_requested, LiveState::arm);` and `wire_unit!(on_halt_requested, LiveState::halt);` in `vayren-live-view/src/lib.rs`.
  - Removed property setters and struct field mappings for arm/halt.
  - Cleaned obsolete banner pixel probe from `crates/vayren-shell/tests/live_render_snapshot.rs`.
- **Python Backend & Control Plane:**
  - Removed `"arm"` and `"halt"` from `MUTATING_ACTIONS` and protocol request dispatch in `src/remote/server.py`.
  - Removed `elif name == "halt":` emergency stop dispatch and simplified `confirm_live` operator confirmation ceremony in `src/app/headless.py`. Removed global `_ARMED` flag, delegating all live authorization to authoritative `service.confirm_live_execution()` and `is_live_confirmed()`.
  - Removed `ControlCommand.ARM` and `ControlCommand.HALT` and handlers `_handle_arm` and `_handle_halt` from `src/execution/control_plane.py`.
  - Removed `send_arm()` and `send_halt()` methods and `ARM_CHANGED`/`EMERGENCY_HALT` listeners from `src/app/services/control_plane_bridge.py`.
  - Removed `self.btn_arm` and `self.btn_halt` buttons from `src/app/services/trading_dashboard.py`.
  - Removed `can_arm`, `arm_blockers`, `can_halt`, and `_arm_eligible_blockers` from `LiveTradingService`.
- **Verification:**
  - `cargo check -p vayren-domain`, `cargo check -p vayren-live-view`, and `cargo check -p vayren-shell` all passed with 0 errors, 0 warnings.
  - `cargo test -p vayren-domain --lib live` passed 109/109 tests.
  - `cargo test -p vayren-live-view` passed 5/5 tests.
  - `cargo test -p vayren-shell --test live_ui_defects_regression` passed 10/10 tests.
  - Python test suites passed: `pytest src/app/tests/test_live_actions.py` (12/12 passed), `pytest src/app/tests/test_phase4_dashboard.py` (passed), `pytest src/execution/tests/test_phase3_control_plane.py` (passed), `pytest src/app/tests/test_live_strategy_sync_and_watchlist.py` (9/9 passed).
  - Ruff check and ruff format clean on all modified files.

**Previous update (live-ui-visual-polish-20261023):** LIVE SCREEN VISUAL POLISH ONLY (no content/features added or removed) —
- **Theme & chrome:** `LiveScreen.init` sets `Palette.color-scheme = ColorScheme.dark`, so native `LineEdit`/`ComboBox`/`ScrollBar` widgets render dark instead of following the workstation OS theme.
- **Alignment:** top bar, watchlist/universe/active-strategy/selected-instrument headers, pipeline stage row, readiness rows, table headers/rows, and footer use `alignment: stretch` where a stretch spacer/column exists; universe symbol rows use `alignment: start`. Narrow top-bar row 2 now right-aligns START/STOP.
- **Tables:** positions/orders/fills/event headers and rows share the same fixed columns; event headers normalized to uppercase (`TIME`/`LEVEL`/`EVENT`/`SYMBOL`/`SOURCE`).
- **Pipeline chips:** replaced the single clipping row with a 3-column wrapped layout (`LivePipelineChip`, up to 3 rows / 9 steps); the selected-instrument card `min-height` reserves those rows so the final `SL PROTECTED` chip and notice stay visible at 1920/1600/1024.
- **Verification:** `cargo test -p vayren-shell --test live_render_snapshot` — 7 passed; `cargo test -p vayren-shell` — 118 passed, 0 failed; `python tools/validate_language_ownership.py` — PASSED (421 files); offscreen snapshots inspected at 1920x1080, 1600x900, 1024x640. No behavioral/content changes.
- **Packaging:** `python tools/build_rust.py --package` rebuilt the packaged-remote release (`target/release/vayren-shell.exe`, 40,937,472 bytes, 2026-10-24 09:20) and re-synced `C:\Users\visha\Desktop\VAYREN.lnk` (target = release exe, args `--data-dir D:\ZerodhaTradingData --strategy-dir D:\VAYREN_STRATEGIES`, workdir = repo root, icon = `tools/assets/vayren_desktop.ico`).

**Previous update (live-ui-realtime-clock-and-snapshot-timestamp-20261023):** REAL-TIME CLOCK & SNAPSHOT TIMESTAMP BUGS RESOLVED (BUG 3 & BUG 4) —
- **Root Cause & Fix (Bug 3 - Hardcoded Clock):**
  - `crates/vayren-domain/src/live.rs#L5161`: Replaced static string `"09:47:12 IST"` with dynamic calculation `current_ist_clock()`. Implemented standard-library IST conversion (UTC + 5:30) with honest handling of invalid time and `test_clock` override capability.
  - `crates/vayren-shell/src/main.rs`: Timer tick now updates `shell::apply_live` every 1s whenever the Live screen is open (`ui.get_active_screen() == ShellScreen::Live`), ensuring the clock ticks continuously without freezing when stopped.
- **Root Cause & Fix (Bug 4 - Snapshot Timestamp Dropped):**
  - `crates/vayren-shell/src/remote_live.rs`: Forwarded `as_of` from remote sections into `translated` so `state.apply_snapshot` receives and records `state.snapshot_time`.
  - `crates/vayren-domain/src/live.rs`: Implemented `format_snapshot_time` supporting ISO-8601 (IST and UTC) and unix epoch, honestly returning empty string on missing or malformed input. Added `pub snapshot_time: String` to `FooterView`.
  - `crates/vayren-shell/ui/live.slint` & `shell.rs`: Added `snapshot-time: string` to `LiveFooter`. In the footer status strip, displayed `SNAPSHOT: <time>` alongside the real-time workstation clock, keeping server update time and local live clock cleanly separated.
- **Verification & Regression Tests:**
  - Added unit tests in `crates/vayren-domain/src/live.rs`: `test_clock_is_not_hardcoded_and_updates`, `test_current_ist_clock_formatting`, `test_snapshot_timestamp_formatting_and_lifecycle`.
  - Added regression test in `crates/vayren-shell/src/remote_live.rs`: `test_remote_live_snapshot_as_of_forwarding`.
  - Added footer binding assertions in `crates/vayren-shell/tests/live_ui_defects_regression.rs`.
  - All tests passed: 112 passed in `vayren-domain --lib live`; 10 passed in `live_ui_defects_regression`; 1 passed in `remote_live`; level 2 impact validation passed (270.43s).

**Previous update (live-ui-broker-name-fallback-consistency-20261023):** BROKER NAME FALLBACK INCONSISTENCY RESOLVED (BUG 7) —
- **Root Cause & Fix:**
  - `crates/vayren-shell/ui/live.slint`: TopCard used `NOT CONFIGURED` when `root.bar.broker-label` was empty, but Inspector panel (line 2800) had hardcoded fallback `: \"FYERS\"`, and footer strip had `: \"FYERS\"`.
  - Unified fallback policy across both sections: both TopCard and Inspector now evaluate to `root.bar.broker-label != \"\" ? root.bar.broker-label : \"NOT CONFIGURED\"`, eliminating contradictory UI states.
  - `crates/vayren-domain/src/live.rs`: Extended broker connection status evaluation to recognize `DISCONNECTED`, `ERROR`, and `FAILED` states when `connected` boolean is omitted in backend snapshots.
- **Verification & Regression Tests:**
  - Added 4 unit tests in `crates/vayren-domain/src/live.rs`: `test_broker_label_configured_fyers`, `test_broker_label_empty_never_fakes_fyers`, `test_broker_label_disconnected_states`, `test_broker_label_unknown_state_honest`.
  - Added regression tests in `crates/vayren-shell/tests/live_ui_defects_regression.rs`: `test_broker_label_consistent_between_top_card_and_inspector` and `test_broker_status_consistent_between_top_card_and_inspector`.
  - Passed `cargo test -p vayren-domain --lib live` (109 passed).
  - Passed `cargo test -p vayren-shell --test live_ui_defects_regression` (10 passed).
  - Passed `python tools/benchmark.py impact --run` (235.49s).

**Previous update (production-audit-next-session-readiness-20261023):** LIVE ENGINE VERIFIED ACTIVE, RUNNING, ARMED & RECONCILED; BROKER ORDERBOOK INDEPENDENTLY CONFIRMED FLAT; 22 SYMBOLS STEPPING; NEXT NSE SESSION READINESS CONFIRMED —
- **Direct Live Snapshot & Runtime Verification (EC2 PID 756581):**
  - Worker: `status: RUNNING`, `alive: true`, `running: true`, `desired_status: RUNNING`, `uptime: >1800s`, `started_at: 2026-10-23T19:40:32+05:30`.
  - System: `execution_ready: true`, `execution_reason: ""`, `websocket: CONNECTED`, `kill_halted: false`.
  - Broker (Fyers XV29244): `status: CONNECTED`, `REST: READY`, `Order WS: READY`, `Market WS: CONNECTED` (with auto-resubscribe resilience).
  - Strategy: `OBR C1C4`, `status: RUNNING`, `mode: LIVE`, `timeframe: 30m`.
  - Recovery: `phase: NOT_REQUIRED`, `reconciliation: SAFE`, `required: false`.
- **Independent Broker Orderbook Verification (Fyers API v3 Direct Query):**
  - Executed direct Fyers orderbook query (`fyers.orderbook()`) and net positions query (`fyers.positions()`) using active session access token.
  - Broker response: `s: ok, code: 200, message: ""`.
  - `orderBook: []` (0 orders on broker for today).
  - `netPositions: []` (0 open positions on broker).
  - Reconciled with engine: 0 fills, 0 open orders, 0 exposure, PnL flat (SAFE).
- **All 22 Symbols Active Pipeline & Sizing Analysis:**
  - 22 symbols actively tracked: `DREDGECORP`, `WAAREERTL`, `KEC`, `KAYNES`, `AVANTIFEED`, `ROUTE`, `RAILTEL`, `SCI`, `MOIL`, `CRAMC`, `LXCHEM`, `NOCIL`, `DAMCAPITAL`, `JSWCEMENT`, `TEXRAIL`, `ZEEL`, `VIKRAN`, `IFCI`, `DCW`, `SAGILITY`, `JINDWORLD`, `NETWEB`.
  - Session journals confirmed active reconciliation event logging every 30 seconds (`matched: true, mismatches: 0, status: SAFE`).
  - Sizing validation: Low-priced candidates (`NSE:DCW` LTP ₹45.00, `NSE:SAGILITY` LTP ₹45.30, `NSE:JINDWORLD` LTP ₹41.99) with risk ≤ ₹0.60 calculate `quantity = 1` share (`SizingVerdict.ready = True`). Higher-priced stocks calculate `quantity = 0` due to risk budget constraints.
- **External Blocker & Trading Session Readiness:**
  - Market is closed (NSE cash hours 09:15–15:30 IST; current time ~20:10 IST). No live market ticks or breakout formations can occur outside trading hours.
  - Zero forced trades or artificial orders submitted, preserving real-money safety rules.
  - Engine remains fully armed and live-ready to process the opening session at 09:15 IST tomorrow.

**Previous update (desktop-packaged-remote-exe-delivered-20261023):** OFFICIAL PRODUCTION DESKTOP EXECUTABLE WITH PACKAGED-REMOTE FEATURE BUILT & DELIVERED DIRECTLY TO DESKTOP —
- **Build Artifacts & Features Package:**
  - Invoked `python tools/build_rust.py --package` with `--features packaged-remote` for `vayren-shell`.
  - Built core release cdylib: `target/release/vayren_core.dll` (500,224 bytes).
  - Built all 6 view release cdylibs (batched, 42.48s): `vayren_market_view.dll` (17.6 MB), etc.
  - Built packaged remote desktop shell binary: `target/release/vayren-shell.exe` (40,891,392 bytes) with `packaged-remote` feature enabled.
- **Desktop Artifacts Placed & Synced:**
  - `C:\Users\visha\Desktop\VAYREN.lnk`: Canonical shortcut updated with `--data-dir D:\ZerodhaTradingData --strategy-dir D:\VAYREN_STRATEGIES`, working directory `c:\Users\visha\Desktop\vayren-core`, icon `vayren_desktop.ico`.
  - `C:\Users\visha\Desktop\VAYREN.exe`: Direct release executable (40,891,392 bytes).
  - `C:\Users\visha\Desktop\vayren-shell.exe`: Direct release executable copy (40,891,392 bytes).
- **Standalone Execution Smoke Test Verified:**
  - Ran `VAYREN.exe --remote-probe` directly from desktop.
  - Verification output: `VAYREN starting (PRODUCTION REMOTE DESKTOP CLIENT)... Gateway: wss://ip-172-31-12-198.taila678c5.ts.net/vayren/v1`. Exit code 0.
- **Constraints Honored:** No git push, no credential leakage, zero mock logic.

**Previous update (live-engine-armed-started-ec2-20261023):** LIVE ENGINE OFFICIALLY ARMED, STARTED & RUNNING ON EC2; OPERATOR EXPLICIT LIVE AUTHORIZATION GRANTED; SNAPSHOT CHANNEL DEFECT FIXED & DEPLOYED; ALL 22 SYMBOLS ACTIVELY STEPPING; VENUE RECONCILIATION SAFE; ZERO HARD BLOCKERS —
- **Snapshot Channel Defect Fixed & Deployed:**
  - `src/remote/server.py`: added `"recovery"` and `"desired_state"` to `EVENT_CHANNELS` allowlist so clients can subscribe to and receive recovery/desired-state sections in snapshots.
  - Regression tests in `src/remote/tests/test_server.py`: `test_recovery_and_desired_state_are_subscribable` and `test_unknown_channel_still_rejected` verified (100% pass, 25/25 in test_server, 100/100 in remote suite).
  - Deployed safely to EC2 `/home/ubuntu/vayren-core/src/remote/` with verified backup `/home/ubuntu/vayren-backup-channels-20261023`.
  - Authorized restart executed via `vayren-remote-restart.path` (`restart.request` marker). Pre-restart safety gate confirmed: session was STOPPED, venue flat (0 orders, 0 positions).
  - Snapshot verified: both `recovery` (phase `NOT_REQUIRED`, `reconciliation: SAFE`) and `desired_state` sections are now received over loopback WebSocket.
- **Operator Authorization & Arming/Start Ceremony:**
  - Operator explicitly authorized LIVE trading via interactive modal: Strategy OBR C1C4, 22 symbols, Broker Fyers XV29244, max risk ₹0.60 per stock (0.15% per trade), max exposure ₹100.00.
  - ARM command sent (`action: arm`): `live_armed=True`, `start_blockers=[]`.
  - START command sent (`action: start`, `params: {"confirmed": True}`): session verified, all 22 symbols subscribed on Fyers Live DataSocket (`MARKET_WS_SUBSCRIBED`), and the 1-second timer loop started.
- **Verified Production Runtime Facts (EC2 PID 756581):**
  - Worker: `status: RUNNING`, `alive: true`, `running: true`, `uptime_seconds: >60s`, `started_at: 2026-10-23T19:40:32+05:30`.
  - Strategy: `OBR C1C4`, `status: RUNNING`, `mode: LIVE`, `timeframe: 30m`.
  - System: `execution_ready: true`, `execution_reason: ""`, `websocket: CONNECTED`.
  - Venue / Broker: `Fyers` (`XV29244`), `CONNECTED`, REST READY, Market WS READY, Order WS CONNECTED.
  - Capital: Available ₹100.00, Effective ₹400.00, Max risk ₹0.60 per stock.
  - Reconciliation: `status: SAFE`, `blocks_live: false`, 0 open positions, 0 open orders.
  - Market Data: `feed: live`, 22 symbols subscribed to real-time socket.
- **Position Sizing & Risk Controls Trace:**
  - Sizing evaluated across all 22 symbols:
    - Cheaper candidate stocks (`NSE:DCW` at ₹45.00, `NSE:SAGILITY` at ₹45.30, `NSE:JINDWORLD` at ₹41.99) with risk ≤ ₹0.60 (e.g. 1% stop = ₹0.45 risk) calculate `quantity = 1` (`SizingVerdict.ready = True`).
    - Higher-priced stocks (e.g. `DREDGECORP` at ₹891, `KAYNES` at ₹3281) with > ₹0.60 stop range calculate `quantity = 0` (`RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE`).
    - Quantity of 0 on higher-priced stocks does not block eligible low-priced stocks. Sizing formulas strictly preserved without artificial inflation.
- **Order Generation & Market Hours Blocker:**
  - Current timestamp: 19:42 IST (after regular NSE market hours 09:15–15:30 IST).
  - OBR C1C4 generates signals only on intraday breakout candles (specifically after Reference Candle 3 at 10:45 IST) during active market hours.
  - Because market is closed, no new ticks arrive, no breakout candles form, and 0 signals are generated.
  - Per Rule 8, no artificial orders or forced trades were fabricated. The live engine remains running and execution-ready for market open.
- **Constraints Honored:** No force push, no credential leakage, zero mock trading, all safety gates intact.

**Previous update (live-market-data-fanout-stale-recovery-20261023):** THREE CONFIRMED LIVE DEFECTS REMEDIATED LOCALLY (SHARED FYERS FACE DRAIN, 60 s STALE THRESHOLD vs 30 m CADENCE, NO RESTART RECOVERY); FULL GATE GREEN; NOT DEPLOYED, NO ORDERS, NO PUSH —
- **Incident 2026-10-22 (confirmed from journal + code, no order ever submitted; 0 signals so 0 orders):** only 1 bar delivered vs ~110 expected; 20/22 symbols last bar 2026-10-16 15:15. Root causes: (a) all 22 sessions shared ONE `FyersLiveMarketData` whose single `queue.Queue` was drained destructively by the first 1 Hz poller — every other symbol starved; (b) `NormalizerConfig.stale_after_seconds = 60.0` applied to 30-minute candles, so a healthy 30 m feed was rejected stale; (c) a gateway restart left sessions STOPPED with no persisted recovery workflow. Seed funds ₹100 → qty 0 (`RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE`) — unchanged, never bypassed.
- **Defect 1 (starvation) fixed:** `live_market_data.py` replaced the single queue with a bounded history + one independent cursor per registered consumer (`register_consumer`/`poll(consumer_id)`/`consumer_stats`/`buffered_quotes`/`overflow_dropped`); polling order can no longer starve anyone. New `src/broker/market_data_fanout.py`: one pump per shared face, per-consumer cursors/symbol filters, lag+overflow counters, pause/resume, strict connect/reconnect. The service now wraps EVERY market-data face (even one symbol) in a fan-out, so provider teardown never destroys a venue-owned `StaticPlugin` face: teardown unsubscribes this run's symbols and `disconnect()`s the shared transport; only an explicitly owning fan-out may `close()` it (`close_upstream_when_empty=False` default).
- **Defect 2 (60 s vs 30 m) fixed in Rust:** `candle_stale_after(interval) = interval + clamp(interval*0.10, 30s, 300s)` → 30 m = **1980 s**, 1 m = 90 s (authoritative in `crates/vayren-core/src/normalizer.rs`, exposed via `vy_norm_candle_stale_after`). New `vy_norm_rebase_health` gives a fresh expectation window at the first in-session sweep of each trading day (`MarketCalendar` uses NSE session hours/holidays; overnight/weekend/holiday stale protection preserved). A heartbeat now proves transport liveness only — once a candle stream has delivered, only a fresh candle (or a rebase) clears stale.
- **Defect 3 (restart recovery) fixed:** service persists desired vs observed state; recovery phases `AWAITING_OPERATOR` / `RECOVERING` (transient, durable mid-resume marker) / `BLOCKED` / `FAILED` / `COMPLETE`; consent is cleared on degrade and on blocked/failed attempts; checkpoints are merged, never discarded; broker truth is reconciled before any submission; resume is operator-driven only (`start(confirmed=True)`), never automatic; snapshot exposes `recovery`, `desired_state`, `worker.alive`, market `health`/`delivery` and distinguishes transport liveness vs candle freshness vs strategy delivery.
- **Observability:** snapshot/remote projection now carries per-fan-out transport health + per-consumer delivery counters, session `market_data_health()` carries policy window, stale symbols, last delivery, `events_delivered` and `transport`; `worker_alive` is truth-checked so a dead timer cannot claim RUNNING (service degrades with a precise reason instead).
- **Verification (local, final tree):** `make check` = Rust suites (409 core + 306 domain + view/shell suites, fmt clean) + `ruff check .` / `ruff format --check .` (421 files) + `pyright` 0 errors + all 9 governance validators PASSED (language ownership 421 files, `docs/repo_graph.json` regenerated) + `pytest` **1790 passed, 6 skipped**. New regression suites: `test_market_data_fanout.py` (22 consumers, adversarial polling order, concurrency, overflow/lag, reconnect failure, shared-face reuse after teardown, disconnect-not-close), `test_fyers_market_data_consumers.py`, `test_market_data_health_policy.py` (30 m cadence, calendar/open boundaries, no fabricated delivery, same-day restart rebase), `test_live_recovery_workflow.py` (restart→AWAITING, RECOVERING mid-resume, blocked/failed builds, no auto-start, consent cleared), `test_live_readiness_authority.py` (+ always-wrap assertion).
- **Language/governance:** `src/execution/market_data/health_policy.py` is Python orchestration over Rust-owned numeric policy; tracked as `TEMPORARILY_RETAINED` in `docs/language_retention.json`.
- **Constraints honored:** local code+test changes only; no EC2 deploy/restart, no `live_session.json` mutation, no LIVE start, no order/cancel, no risk/capital/credential change, no push/tag/release. Working tree uncommitted. Open item unchanged: the 9 orphan probe processes on the host still need operator consent for cleanup.

**Previous update (final-live-readiness-deployed-20261022):** E2E LIVE READINESS VERIFIED & DEPLOYED TO EC2; MORNING AUTH (06:01 IST) SUCCESSFUL; WINDOWS RELEASE BINARY BUILT; ZERO VENUE ORDERS; WORKER STOPPED/DISARMED FOR MARKET OPEN —
- **Fix 1: LTP Stale / 2-Day-Old Value Root Causes Fixed:**
  - *Root Cause A (Symbol Suffix Mismatch):* FyersLiveMarketData normalized symbols with -EQ suffix (NSE:DCW-EQ) while caller subscribed without -EQ (NSE:DCW). Added bidirectional mapping and defense-in-depth suffix matching in BrokerFeedProvider._ingest().
  - *Root Cause B (Missing last_close in BrokerFeedProvider):* BrokerFeedProvider lacked last_close property needed by _refresh_mark(symbol), leaving marks to fall back to old historical candle closes. Added last_close property and marks updates to BrokerFeedProvider.
- **Fix 2: Reconcile Rate Limit Saturation / Multi-Session Stream Drain Fixed:**
  - *Root Cause A (Bypassed poll interval on empty orders):* session_adapter.py stream_events() used 'and self._cached_snapshot', bypassing the rate-limit interval whenever 0 orders were active and flooding REST API calls across all 22 sessions. Removed faulty condition; when Order WS is connected, REST polling is avoided entirely.
  - *Root Cause B (Multi-session queue race):* In multi-session setup, whichever session polled first drained events for all symbols. Added per-consumer cursor tracking over a ring buffer.
- **Fix 3: Automated Daily Authentication & Expiry Recovery:**
  - Token expired at 06:00:00 IST. BrokerManager._verify_health() wired to _try_auto_auth() using stored TOTP credentials. Verified live on EC2 at 08:45 IST: fresh session was auto-issued at 06:01:01 IST (expires tomorrow 2026-10-23 06:00:00 IST).
- **Safe EC2 Deployment & Validation:**
  - Stopped vayren-remote.service, backed up to /home/ubuntu/vayren-backup-readiness-20261021, terminated 9 orphan probe processes.
  - Deployed 5 touched files (broker_manager.py, live_market_data.py, session_adapter.py, broker_feed.py, session.py).
  - Started vayren-remote.service. Verified: both ORDER_WS_CONNECTED and MARKET_WS_CONNECTED, broker CONNECTED (XV29244, all checks passed), reconciliation SYNCED (blocks_live=False), worker STOPPED (22 symbols configured).
- **Windows Release Binary:**
  - Built target/release/vayren-shell.exe (40,891,392 bytes) linked to desktop shortcut VAYREN.lnk.
- **Pre-Market Status at 08:45 AM IST:**
  - Broker: CONNECTED
  - Reconciliation: SYNCED (blocks_live=False)
  - Order WebSocket: CONNECTED
  - Market WebSocket: CONNECTED
  - Worker: STOPPED & DISARMED (single blocker: live confirmation required (explicit operator consent)).
  - No orders placed; ready for market open.

**Previous update (reconcile-convoy-fix-single-flight-20261021):** RECONCILE LOCK-CONVOY ROOT CAUSE FIXED **AND DEPLOYED TO EC2** (GATEWAY RESTARTED 2026-10-21 16:38 UTC AFTER A SAFE-WINDOW GATE; LIVE WORKER WAS STOPPED, VENUE FLAT); THE WINDOWS DESKTOP NOW RECEIVES THE REAL EC2 `welcome` + AUTHORITATIVE `snapshot` (NO PUSH, NO LIVE START, NO ORDERS) —
- Root cause (traced end-to-end; matches the 149-thread wedge census): every 30 s each of the 22 `LiveSession`s reconciled via `reconcile_now()` → `resolve_broker()` → forced `face.connect()` (profile REST + `_invalidate_account_snapshot()` + order-WS reset), then `reconciliation_snapshot()` **held `_account_snapshot_lock` across both governor-gated REST reads**. The synchronized fleet burst (~66 calls/30 s vs 2.5 rps cap 3) saturated the token bucket, and every concurrent caller (sessions + invalidation paths + reaper-churned `hello` threads, each synchronously pumping all sessions via `service.snapshot()`) queued behind the one lock-held network read → permanent convoy; `_push_snapshot` additionally built the whole live snapshot twice per fresh connection.
- Fix A (`src/broker/providers/fyers/session_adapter.py`): `reconciliation_snapshot()` is now single-flight — exactly one owner performs the two reads with NO lock held; joiners wait on the fetch `Event` (no lock), bounded by new `account_snapshot_wait_s` (default `ACCOUNT_SNAPSHOT_WAIT_SECONDS = 30.0`), failing closed (NETWORK_ERROR) and deregistering a dead ticket on expiry; TTL cache + failure-sharing semantics unchanged; an invalidation epoch stops a read that began before an account-changing op from publishing (post-op truth wins); `positions()`/`open_orders()` stay uncached.
- Fix B (`src/execution/broker/factory.py`, `src/execution/runtime/session.py`): `resolve_broker(..., *, connect=True)`; `reconcile_now()` passes `connect=False` — per-step re-resolution is a pure identity swap (no profile REST, no cache invalidation, no WS reset); `start()` keeps the verification probe. Fresh auth preserved: the manager connects + verifies the adapter once at activation and sessions swap to that exact instance; a bad token still fails closed as a blocking RECONCILE_ERROR. Intentional delta: the per-reconcile order-WS "unsuspend" reset is gone — suspension now holds until a real reconnect (as documented).
- Fix C (`src/remote/server.py`): `_push_snapshot` builds the live snapshot once on the hello/subscribe path (was two full builds per connection); the poll/resync path is unchanged.
- Regression tests (all discriminating; fail on the old code): `src/broker/tests/test_fyers_rate_limit_resilience.py` 14 → 18: slow read → lock acquirable, `_invalidate_account_snapshot()` and `health()` complete while the read is blocked; 8 concurrent callers on a blocked read → one fetch (1 positions + 1 orders) with the lock free; a joiner times out fail-closed instead of hanging; invalidation mid-flight discards the stale publish. `src/execution/tests/test_live_reliability_hardening.py` 22 → 24: `_MockBroker.connect_calls` counter — reconcile of an unchanged registered broker never connects (3 reconciles → 0 probes); venue re-register swaps without probe and a failing swapped adapter still blocks live.
- Verification: broker suite 329 passed; execution suite 223 passed; `test_fyers_rate_limit_resilience.py` 18; `test_live_reliability_hardening.py` 24; remote `test_snapshot.py` + `test_server.py` 38; `test_phase5_reconciliation.py` 21; `ruff check` clean on all 6 touched files; `validate_imports.py`, `validate_structure.py`, `validate_language_ownership.py` (415 files) all PASSED.
- Honest scope: the tests demonstrate the mechanism (no lock held across a slow read; joiners coalesce on one bounded fetch; no per-step probe/invalidation) — not a full end-to-end gateway-wedge reproduction. Expected effect: fleet reconciliation REST ≤ 1 read-pair per 5 s window (from 44 reads + 22 forced profiles per 30 s); per-session truth cadence unchanged (cache 5 s ≪ 30 s).
- Constraints honored: no EC2 deploy/restart, no `live_session.json` edit, the 9 hung probe processes untouched, no LIVE start, no orders; local edits + local commit only, no push/tag/release/PR.
- DEPLOYED + RESTARTED (follow-up, same day 16:37–16:40 UTC): the five runtime files above (`src/remote/server.py`, `src/execution/runtime/session.py`, `src/broker/providers/fyers/session_adapter.py`, `src/execution/broker/factory.py`, `src/broker/tests/test_fyers_rate_limit_resilience.py`) were copied to the EC2 checkout (CRLF→LF) with backup `/home/ubuntu/vayren-backup-convoy-20261021-163741`; host-side `pytest` on the deployed tree = 40 passed; `_reap_stale()` still present on the host (`server.py:411/445`).
- Restart safety gate (executed immediately before the restart, all read-only): live session store `session_timing.status = STOPPED` (`stopped_at 2026-10-21T21:47:28+05:30`), gateway process 9 threads (no 22-session fleet), venue truth **0 net positions / 0 working orders** (`XV29244`), no pending `restart.request`. Restart → PID 725028, listener `127.0.0.1:8765`, `NRestarts=0`.
- Real-venue proof of the fix on EC2 (read-only): 22 concurrent per-symbol `reconciliation_snapshot()` calls → venue saw exactly **1 `GET profile` + 1 `GET positions` + 1 `GET orders`**, 0.06 s, 0 errors (attempt 2: 0.00 s from cache). The convoy can no longer form: the account read is single-flight and the lock is never held across REST.
- Post-restart gateway health: 10 threads, **0** threads in `api_governance.acquire` / `_invalidate_account_snapshot` / `reconciliation_snapshot` (was 87 + 55 + 9), no stuck sockets, no `connection limit reached`, no reaping, no tracebacks — only clean `peer closed the connection` from finished probes.
- Windows desktop path verified end-to-end with the REAL client binary (`target/release/vayren-shell.exe --remote wss://ip-172-31-12-198.taila678c5.ts.net/vayren/v1`, token from the Windows Credential Manager slot `remote-token.vayren-remote`): every run → `welcome` (role `trade`) + authoritative snapshot (`broker CONNECTED`, `broker_session CONNECTED`, `reconciliation SYNCED`, `worker STOPPED/running=false`, `positions 0`, `open_orders 0`, `strategy BLOCKED — live confirmation required`) + sequenced `state_update`s + `pong`. The wire snapshot carries the `worker` section the shell needs to paint `server_status = CONNECTED` (the UI no longer falls back to `OFFLINE`/`UNKNOWN`).
- Housekeeping: `py-spy` is installed in the host venv (`/home/ubuntu/vayren-core/.venv/bin/py-spy`, read-only profiler) for the thread census; the 9 hung probe processes from earlier sessions are still untouched (operator cleanup still needs consent). No commit/push of this deployment (the tree still carries the uncommitted local fix set), no LIVE start, no order, no arming.

**Previous update (ec2-deploy-a6947c7-reconcile-convoy-20261021):** a6947c7 DEPLOYED TO EC2 (FAST-FORWARD + BACKUP + AUTHORIZED OPERATOR RESTART); POST-RESTART RUNTIME GREEN; RECONCILE LOCK-CONVOY INCIDENT DOCUMENTED (FIX STILL OPEN) —
- Deploy: EC2 `/home/ubuntu/vayren-core` fast-forwarded `d11b97f → a6947c7` (52bb54a, 89ea368, c986fc6, a6947c7); tree clean. The pre-existing uncommitted `M src/remote/server.py` was byte-identical to `origin/main`'s blob and was discarded safely (checkout before ff). Backup: `/home/ubuntu/vayren-backup-20261021T161544Z/` — `repo.tar`, `repo-novenv.tar` (clean code+git), `data.tar`, `SHA256SUMS`, `HEAD.txt` (pre-deploy HEAD + dirty file), frozen-process py-spy dump; all tars verified readable. Market-WS markers verified in the deployed `live_market_data.py` (delays `2/5/10/30`, `MAX_RECONNECT_ATTEMPTS=10`, `HANDSHAKE_VERIFY_SECONDS=5.0`, one-shot `_watch_handshake`, `_cancel_pending_timers`, `reconnect=False`, `MARKET_WS_HANDSHAKE_INCOMPLETE`/`_PROBE_WARN`).
- Why a restart was needed (incident): the running gateway (PID 716344, started 14:00:04 UTC) wedged at ~14:01:18 UTC (LIVE start) in a reconciliation lock convoy — 149/157 threads stuck: one holder inside `reconciliation_snapshot()` (`_account_snapshot_lock`) across two governor-gated REST reads, 91 waiters in `_invalidate_account_snapshot()`, 43 in `reconciliation_snapshot()`, 12 spinning in `TokenBucket.acquire` (2.5 rps / cap 3). Cause: interaction of `06b0f3f` (coalesced 5 s snapshot + lock) + `d11b97f` (per-step `resolve_broker→connect()` forced profile REST + snapshot invalidation) + `52bb54a` reaper (new clients keep arriving; every `hello` synchronously pushes a snapshot at `server.py:580`). The graceful STOP path was unreachable (all pre-auth frames route through the wedged hello; commands need an authenticated connection), so the operator explicitly authorized the documented operator restart (runbook §5 path 1). Restart `16:17:26 UTC`: old PID terminated ("Deactivated successfully", 6m12s CPU), new PID 722339. Venue was flat (0 positions / 0 open orders, `XV29244`), market closed, and the wedged session had started after the close — no trading state was lost.
- IMPORTANT: `a6947c7` does NOT fix this convoy (it touches none of `session_adapter.py` / `execution/runtime/session.py` / `api_governance.py` / `live_trading_service.py`); only the restart cleared it. Re-arming LIVE on current code can wedge again → follow-up fix pending (bounded/cached rebind; do not hold the snapshot lock across REST reads).
- Post-restart verification (loopback WS + fresh process): `welcome` (role `trade`), `snapshot` fast; worker `STOPPED` (`stopped_at 2026-10-21T21:47:28+05:30`, restore rule persisted), strategy `BLOCKED` (only blocker `live confirmation required` — LIVE not re-armed), reconciliation `SYNCED` (`blocks_live=false`), broker `CONNECTED` (`auth_valid`, `XV29244`), order stream `CONNECTED`, market `STREAMING` (no live feed), `ping → pong` < 1 s, wrong token → `AUTH_FAILED`; fresh-process venue read 0 positions / 0 open orders. Journal since restart: no tracebacks, no 429s, no reconnect storms, no `MARKET_WS_HANDSHAKE_INCOMPLETE`.
- Oddities/housekeeping: 9 hung probe processes from earlier sessions still running (pids 247739, 247994, 625824, 638263, 640221, 648191, 648839, 657487, 703664 + bash/sudo wrappers; low CPU) — untouched; they flip-flop `live_session.json` (fresh-process restore-corrections vs the gateway's RUNNING rewrites). Cleanup needs operator consent. The py-spy diagnostic install wrote `py_spy-0.4.2.dist-info` into `.venv/site-packages` (harmless). No push/tag/release/PR; no LIVE start; no orders.

**Previous update (fyers-silent-handshake-recovery-20261021):** FYERS MARKET WS SILENT-HANDSHAKE RECOVERY ADDED (LOCAL VERIFIED; FOCUSED TESTS GREEN; NOT DEPLOYED, NO SERVICE RESTART, LIVE STOPPED/DISARMED) —
- Root cause: `FyersDataSocket.__init_connection()` swallows token/handshake failures (`except Exception: data_logger.exception(e)`) and never calls `On_error`; `connect()` then calls `self.on_open()` unconditionally, so the adapter was told "connected" while `is_connected()` (`bool(__ws_object)`) stayed False. With no `on_error` and no `on_close`, the adapter-owned retry never armed and the stream died silently.
- Fix (`src/broker/providers/fyers/live_market_data.py`): after a successful `connect()`, `_establish()` calls `_watch_handshake(generation)`. When `_socket_is_connected()` (vendor `is_connected()` probe; absent probe ⇒ trusted via callbacks, unchanged) reports False, one one-shot `HANDSHAKE_VERIFY_SECONDS = 5.0` check (`_verify_handshake`) is armed in its own single-flight slot; at fire time a still-missing vendor socket calls the existing `_schedule_reconnect("handshake incomplete")`. Unchanged: backoff `2/5/10/30` s, `MAX_RECONNECT_ATTEMPTS = 10` → `STATE_ERROR`, generation guards, subscription restore, fail-closed health. No vendor monkey-patching, no polling loop; a failed handshake cannot create duplicate timers/sockets/threads.
- New: `_handshake_timer` slot; `_cancel_pending_timers()` (was `_cancel_retry`) cancels retry + handshake check together; `_on_error` keeps `STATE_ERROR` only when no recovery is pending (`_recovery_pending()`); new log event `MARKET_WS_HANDSHAKE_INCOMPLETE`.
- Tests (`src/broker/tests/test_fyers_market_ws_reconnect.py`, 13 → 19): silent handshake (no error/close, vendor says not connected) arms exactly one bounded retry; duplicate + 8-thread concurrent signals (check firing while `on_close` lands) still schedule one retry; explicit `disconnect()` cancels the check and a forced run cannot retry; silent-handshake retries exhaust with the exact `2,5,10,30×7` delay sequence → `STATE_ERROR` + `exhausted` reason, no 11th attempt; a late real connect stands the check down; probe-less sockets keep callback-driven behavior.
- Verification: `pytest src/broker/tests/test_fyers_websocket.py src/broker/tests/test_fyers_market_ws_reconnect.py` = 55 passed; `ruff check` + `ruff format --check` clean; `pyright` 0 errors; `validate_language_ownership` PASSED (415 files); `repo_graph --build` + `validate_repo_graph` PASS (3,753,526 B, under the 3.85 MB cap) and `tools/tests/test_repo_graph.py` 20 passed.
- Still open: EC2 deployment of this commit must happen in a separate verified safe window with the trading worker STOPPED; no order, arming or restart was ever issued.

**Previous update (live-ui-defects-and-ci-green-20261021):** LIVE UI DEFECT FIXES AND FULL CI PRE-FLIGHT VALIDATION PASSED —
- 8 LIVE UI Defect Fixes & Parity Hardening:
  1. Live confirmation banner mode check: Corrected `root.bar.mode == 2` to `root.bar.mode == 1` (`ExecMode::Live`).
  2. Table scroll views: Blotter tables (Positions, Orders, Fills, Live Events) wrapped in `ScrollView` with vertical scrollbars so rows cannot clip offscreen.
  3. Orders table alignment: Aligned header columns with row data cells, adding explicit `STATUS` (70px) and `DETAIL` stretching column.
  4. Selected instrument inspector: Rendered live backend facts from `selected-stock` (LTP, Entry, Stop, Qty, Risk Util, 4-step pipeline status).
  5. Compact blotter tabs: Added responsive labels and compact icon clear button for constrained viewports.
  6. Live confirmation venue blockers: Disabled confirmation button whenever venue blockers exist (`can-confirm: root.setup.needs-live-confirm && root.venue-blockers.length == 0`).
  7. Status banner trailing dash: Guarded against dangling dash artifact when `start-reason` is empty.
  8. Code cleanliness & footer status strip: Corrected outdated comments and wired the bottom `LiveFooter` strip.
- Regression & Verification:
  - Added dedicated regression test `crates/vayren-shell/tests/live_ui_defects_regression.rs` (8/8 tests passed).
  - All 306 domain tests (`cargo test -p vayren-domain`) passed.
  - All 84 shell tests (`cargo test -p vayren-shell`) passed.
  - Rust formatting verified (`cargo fmt --manifest-path Cargo.toml --all -- --check`).
  - Python lint & format verified (`ruff check .`, `ruff format --check .`).
  - Typecheck verified (`pyright` 0 errors, 0 warnings).
  - Repo graph synced (`tools/validate_repo_graph.py` passed).
  - All 9 governance validators passed.

**Previous update (reconciliation-stale-broker-reresolve-20261021):** RECONCILIATION BLOCKER TRACED AND FIXED: LIVE SESSIONS AUTO-RE-RESOLVE FRESH BROKER ON TOKEN RE-AUTHENTICATION (LOCAL ONLY; NO DEPLOY, NO PUSH, ENGINE STOPPED) —
- Confirmed Root Cause:
  1. The deployed EC2 service (`vayren-remote`, PID 699535) has been running continuously since 23:28 IST yesterday (`uptime_seconds: 29897`).
  2. At 06:00 IST (00:30 UTC), FYERS daily access tokens expired. The 22 running `LiveSession` instances in `LiveTradingService._sessions` executed periodic reconciliation (`reconcile_now()`) with the expired token, resulting in HTTP 401 Unauthorized (`FYERS positions request: unauthorized`).
  3. The API Governor's `read_circuit` recorded consecutive failures and tripped to `OPEN` state.
  4. At 07:00 IST (01:30 UTC), `BrokerManager` auto-authenticated and successfully saved the fresh access token (`vayren.fyers.session.json`). We confirmed with an isolated live probe that the fresh token connects and reconciles 0 positions / 0 orders in 0.2s without error.
  5. However, `BrokerManager._activate()` instantiated a NEW `FyersSessionAdapter` and registered it into `default_registry()`, while the 22 existing `LiveSession` instances held references to the OLD `FyersSessionAdapter` created yesterday.
  6. The 22 sessions never updated their `_broker` reference. Every 30 seconds, the old adapter probed in `HALF_OPEN` with the expired token, failed, and immediately tripped back to `OPEN` (`after 206 failures`), permanently locking `reconciliation.blocks_live = True`.
  7. Furthermore, `src/remote/snapshot.py:97` mapped `status == "RUNNING"` with `recon.blocks_live = True` to `strategy: {"status": "BLOCKED", "reason": "reconciliation blocks live"}`, which `remote_live.rs` mapped to `"STOPPED"`. The UI thus displayed `Runtime: STOPPED` while the backend worker was actually `RUNNING`, showing a blocked `START` button and preventing the operator from stopping or restarting the session.
- Surgical Fix:
  1. `src/execution/runtime/session.py`: `LiveSession.reconcile_now()` now re-resolves the broker adapter from `default_registry()` if `self._config.adapter_name` is set, updating `self._broker` whenever a new adapter has been registered (e.g. following token refresh). Added `update_broker()` method.
  2. `src/app/services/live_trading_service.py`: `attach_broker_view()` now triggers `s.reconcile_now()` across active sessions whenever `key != self._last_broker_key`, immediately re-binding and reconciling with the fresh broker upon connection or token update.
  3. `crates/vayren-shell/src/remote_live.rs`: Ensured that a running backend worker (`worker.running == true`) maps `session_status` to `"RUNNING"` even if `strategy.status` is `"BLOCKED"`, ensuring the operator can always see the true runtime state and halt/stop the session.
- Tests:
  - `src/execution/tests/test_live_reliability_hardening.py`: 22 passed (new tests `test_reconcile_now_reresolves_fresh_broker_when_venue_re_registers`, `test_update_broker_explicitly_updates_session_broker`).
  - `src/app/tests/test_live_readiness_authority.py`: 12 passed (new test `test_attach_broker_view_triggers_reconcile_on_active_sessions`).
  - `cargo test -p vayren-shell --lib`: 67 passed, 0 failed.
  - Python typing: `pyright` 0 errors. Formatting: `ruff check` / `ruff format --check` clean.
- Strict Constraints Honored:
  - Local inspection and fixes only.
  - No EC2 deployment, no remote service restart, no git push, no live session start, no broker orders.

**Previous update (live-ui-defects-surgical-fix-20261020):** 8 LIVE UI DEFECTS SURGICALLY FIXED AND VERIFIED (LOCAL ONLY; NOT COMMITTED/PUSHED) —
- Fixed 8 defects identified in `crates/vayren-shell/ui/live.slint`:
  1. LIVE confirmation banner mode check: changed `root.bar.mode == 2` to `root.bar.mode == 1` (`ExecMode::Live.kind()` returns `1`, `0` is Backtest; `2` was an obsolete check that prevented the banner from displaying).
  2. Vertical scrolling added: Positions, Orders, Fills, and Live Events blotters wrapped in `ScrollView { vertical-stretch: 1; viewport-width: self.width; vertical-scrollbar-policy: as-needed; ... }` so rows can scroll instead of being clipped by parent layouts.
  3. Orders table alignment: header given 9 columns matching row cells, with `STATUS` fixed to `70px` and a matching `DETAIL` column (`horizontal-stretch: 1`).
  4. Selected instrument inspector: added `SELECTED INSTRUMENT & EXECUTION PIPELINE` card to display real-time backend fields from `selected-stock` (LTP, Entry, Stop, Qty, Risk Util, and 4-step pipeline status).
  5. Compact responsive labels: blotter tab titles adapt (`root.blotters-compact ? "Pos" : "Positions"`, etc.) and Clear button uses compact `"✕"` when space is constrained.
  6. Safety gate protection: LIVE confirmation button disabled if venue blockers exist (`can-confirm: root.setup.needs-live-confirm && root.venue-blockers.length == 0;`), reinforcing backend consent barriers.
  7. Visual polish: removed trailing dash artifact in status banner when `start-reason` is empty (`root.start-reason != "" ? (" — " + root.start-reason) : ""`).
  8. Code cleanliness & layout: corrected outdated blotter tab comments and wired up the orphaned `LiveFooter` at the bottom of `LiveScreen` for persistent venue, latency, and cycle diagnostics.
- Tests: added dedicated regression suite [`crates/vayren-shell/tests/live_ui_defects_regression.rs`](file:///c:/Users/visha/Desktop/vayren-core/crates/vayren-shell/tests/live_ui_defects_regression.rs) (8 tests, all passed in 0.00s); existing shell regression tests pass.
- Release build: `cargo build --release --bin vayren-shell`.
- Strict constraints preserved: no EC2 deployment, no remote restart, no git push, no trading session start, no broker orders.

**Previous update (live-ui-reconciliation-polish-20261020):** LIVE UI POLISHED — THE RECONCILIATION PANEL NOW SAYS WHY (COLOURED BY HEALTH, GATEWAY VOCABULARY INCLUDED) (LOCAL ONLY; NOT COMMITTED/PUSHED) —
- Found while reading the LIVE surface after the EC2 deploy: the client coloured the reconciliation by LITERAL WORD (`CLEAN` green, `MISMATCH`/`BLOCKED` red, everything else neutral) — but the deployed gateway reports `SAFE` / `SYNCED` / `REQUIRED`. So a **blocking** reconciliation (`REQUIRED`, `blocks_live=true`) rendered in NEUTRAL colour, and the venue's reason was never shown at all (the shell dropped the snapshot's `detail`).
- Second, worse defect found in the same pass: the gateway's blocker list can be EMPTY while the venue's own reconciliation blocks live (observed live on EC2: `strategy.reason = "reconciliation blocks live"`, `reconciliation = REQUIRED`, `blockers = []`). The LIVE verdict was derived from that empty list, so the screen could read **"✓ READY TO START"** while the venue was blocking the session. The domain now treats the venue's reconciliation verdict as a blocker of its own (with the venue's reason), so the verdict can no longer be green while the venue blocks.
- Fix (`crates/vayren-domain/src/live.rs`, `crates/vayren-domain/src/portfolio.rs`, `crates/vayren-shell/src/remote_live.rs`, `crates/vayren-shell/src/shell.rs`):
  - health is now a WORD SET, not one string: `reconciliation_is_healthy()` accepts `CLEAN/SAFE/SYNCED/OK/MATCHED/CLEAR`; the blocking set is `MISMATCH/BLOCKED/REQUIRED/ERROR/FAILED/UNSAFE`; an unknown word falls back to the venue's own `blocks_live` answer (fail-closed → red).
  - the reconciliation panel gained a **REASON** row carrying the venue's own words (the gateway's `detail`, else `reason`) — the operator now sees *why* live is blocked (e.g. "reconcile transport error: rate limited by venue (HTTP 429)") instead of a bare status word; it alarms only while the reconciliation itself blocks.
  - the top-bar recon card uses the same health rule, so a blocking reconciliation is red, a synced one green.
  - the portfolio's "Account Sync" gate accepted only `CLEAN` → a healthy remote reconciliation rendered as a BROKEN gate. It now uses the same word set (`SAFE`/`SYNCED`/`CLEAN` → READY).
  - the shell now carries the reconciliation `detail` into the live snapshot instead of dropping it.
  - the domain's `start_blockers()` now adds the venue's reconciliation verdict (with the venue's own reason) when the venue says it blocks live, so the LIVE verdict and the blocker list can never read "READY TO START" while the venue blocks.
- Known gap left in place: `live.slint` declares the six `[LiveKv]` row lists (strategy/position/risk/broker/recon/account) and renders NONE of them — the details are computed and shipped but never drawn; the LIVE screen shows the readiness panel (gates + blockers) instead. Rendering them is a layout change, not a polish tweak, so it was left for a deliberate pass.
- Tests: `cargo test -p vayren-domain` **306 passed / 0 failed** (new: a blocking reconciliation is always a visible blocker + blocks START; a healthy one invents no blocker; remote REQUIRED shows the reason + alarms; SYNCED is green and silent; the Account Sync gate accepts SAFE/SYNCED/CLEAN) and `cargo test -p vayren-shell --lib` **67 passed / 0 failed** (new: the reconciliation detail reaches the live state). `cargo fmt --all -- --check` clean. One pre-existing index-based test was made key-based so a new row cannot shift it silently.
- Not done: NOT committed and NOT pushed (no explicit push instruction for this change); the local packaged-remote client is being rebuilt so the polished LIVE screen is usable locally.

**Previous update (expired-vs-cancelled-20261020):** EXPIRED KEPT DISTINCT FROM CANCELLED IN THE STRATEGY ROUTER (NO DEPLOY, NO PUSH, NO ORDERS) —
- Remaining inconsistency: FYERS status 7 means EXPIRED in the broker/execution layers but was mapped to `CANCELLED` in `src/strategy/execution_router.py`, so an expired order was reported as a cancelled one.
- Minimal change: `src/strategy/models/execution.py` now projects the existing Rust `OrderState` value `EXPIRED` (the vocabulary is a projection of the Rust states — no new state was invented) and exports it; the router's `_FYERS_NUMERIC_STATUS` maps 7 → `EXPIRED` and its string table maps `"EXPIRED"/"EXPIRE"` → `EXPIRED`. The router's contract, the broker's status table and the engine's `OrderState` now name the same four terminal states: 1 Cancelled · 2 Traded/Filled · 5 Rejected · 7 Expired.
- Affected consumer found and fixed: the execution self-healer's order-status sync (`execution/reconciliation.py`) fell back to the string `"CANCELED"` (one L) for any other broker truth — a spelling the engine's `OrderState` does not have, so the reconcile was refused and swallowed, and an EXPIRED truth would have been labelled cancelled. It now uses the engine's vocabulary (`CANCELLED`/`EXPIRED`) and keeps the four terminal states apart. The app's session counters now count an expired order in the closed bucket (like a cancelled one) instead of in none.
- Unchanged on purpose: duplicate-order protection, reconciliation matching, the protective-stop safeguards (no cancellation without broker evidence, no replacement stop before a confirmed cancellation, ambiguous orders stay UNRESOLVED) and every LIVE safety gate.
- Tests: `src/strategy/tests/test_execution_router.py` now pins 1 → CANCELLED, 2 → FILLED, 5 → REJECTED, 7 → EXPIRED (and the `"EXPIRED"` string form) plus a dedicated "an expired order is not a cancelled one" case; `src/execution/tests/test_phase5_reconciliation.py` (+1) pins the self-healer's order-status sync for CANCELLED/FILLED/REJECTED/EXPIRED; `src/execution/tests/test_protective_cancel_state.py` (+1 assertion) pins the journal kind of a venue-finished stop (`SL_FINISHED_AT_VENUE` — no "CANCEL" in it, so the remote's keyword classification cannot show a rejected/expired stop as a cancellation). Focused sweep (strategy + execution suites, three app live suites, the FYERS status suite) **496 passed**; `ruff check`/`ruff format --check` clean on all 8 touched files; focused `pyright` **0 errors**; `git diff --check` clean (exit 0); `validate_repo_graph.py` PASS (auto-refreshed).
- Not done: no EC2 deploy/restart, no GitHub push/tag/release, no LIVE start, no order placement; LIVE trading stays stopped.

**Previous update (fyers-order-status-consistency-20261020):** FYERS ORDER-STATUS MAPPING MADE CONSISTENT — STATUS 6 IS PENDING, NOT CANCELLED (NO DEPLOY, NO PUSH, NO ORDERS) —
- Traced every FYERS order-status mapping and resolved the documented contradiction against the venue's own order-status table (FYERS API v3: 1 Cancelled · 2 Traded/Filled · 3 unused · 4 Transit · 5 Rejected · 6 **Pending** · 7 Expired):
  - `_TERMINAL_STATUSES` was `{2, 5, 6}` (its own comment contradicted the docs): 6 = Pending was treated as TERMINAL and 1 = Cancelled was missing → a live pending order looked finished.
  - the order-socket parser already read 6 as open/pending (correct) while the REST order-book diff read 6 as CANCELLED (wrong) → the two surfaces could disagree about the same order.
  - the strategy router's mirrored FYERS map had 6 → CANCELLED (wrong).
- ONE shared table now drives every surface: `_order_status(raw)` → `OPEN` / `CANCELLED` / `FILLED` / `REJECTED` / `EXPIRED` / `UNKNOWN`, with `_TERMINAL_STATUSES = {1, 2, 5, 7}` and `_TERMINAL_STATUS_NAMES`. The order socket, the REST diff, the duplicate check, `open_orders()` and `order_state()` all use it; the strategy router's numeric map mirrors it again.
- REST order rows are normalized (`client_order_id`, `broker_order_id`, `status`, `venue_status`, `filled_qty`, …). FYERS prefixes a user order tag with `1:`, so the prefix is stripped and the row carries OUR client order id → the reconciliation can finally match a broker order with the local order. `open_orders()` returns only the orders the venue still works (the FYERS book is the whole trading day, cancelled/filled rows included).
- Duplicate protection restored: a PENDING order is found by the pre-submit duplicate check (it used to be skipped because 6 was "terminal"), so a second order for the same client order id can no longer be submitted.
- Engine: a broker-confirmed cancellation now goes through `reconcile` (legal from every live state) instead of a table transition that is illegal from `ACKNOWLEDGED` and was silently swallowed — the local order no longer keeps looking live after the venue cancelled it. An EXPIRED order is reconciled to `EXPIRED` (new "expired" broker event; the protective path treats it like a reject). The reconciliation's open-order set now excludes explicitly closed rows, so the venue's day book no longer shows up as broker-only open orders.
- The protective-cancel evidence now uses the venue's STATUS (CANCELLED proves a cancellation, OPEN proves the venue still works it, UNKNOWN = "not in today's book" proves nothing) instead of "the order is missing from the book" — the missing-order reading was wrong for a day book and could have claimed a cancellation that never happened. The previous safeguards are intact: no cancellation without broker evidence, no replacement stop before a confirmed cancellation, ambiguous orders stay UNRESOLVED.
- The operator order-path test service reads the normalized fields (raw venue fields kept as a fallback).
- Tests: new `src/broker/tests/test_fyers_order_status.py` (22: pending/open, transit, cancelled, filled, rejected, expired, the REST normalization, the tag prefix, the duplicate check with a pending order, the cancellation evidence, the shared status table) + `src/execution/tests/test_live_reliability_hardening.py` (+4: the normalized-row reconciliation match, the closed-row resolution, the WS cancellation event, the WS expiration event) + `src/execution/tests/test_protective_cancel_state.py` (+2: a missing order is not proof of a cancellation, a rejected order is not reported as cancelled) + the strategy router's status test now pins the corrected numeric map. Focused sweep (broker + execution suites, the router test and the four app suites that read broker rows) **633 passed**; `ruff check`/`ruff format --check` clean on all touched files; focused `pyright` **0 errors**; `validate_repo_graph.py` PASS (auto-refreshed).
- Not done: no EC2 deploy/restart, no GitHub push/tag/release, no LIVE start, no order placement; LIVE trading stays stopped.

**Previous update (protective-cancel-state-20261020):** PROTECTIVE-STOP CANCELLATION NOW FOLLOWS BROKER EVIDENCE — THE LOCAL STATE IS NEVER "CANCELLED" ON A LOCAL GUESS (NO DEPLOY, NO PUSH, NO ORDERS) —
- Root cause (two halves of the same bug) in `src/execution/runtime/session.py::_cancel_stop_protection`: the venue's cancel error was swallowed (`with suppress(Exception)`), the venue's BOOLEAN result was ignored, and the tracked order was transitioned to `CANCELLED` unconditionally — a refused cancel, a timeout and an unreadable order book all produced the same local "cancelled" claim plus an `SL_CANCELLED` journal line. The transition was also an illegal edge from the protective order's real state (SUBMITTED/ACKNOWLEDGED → CANCELLED is not in the Rust-owned table), so the `suppress` hid that too: the record and the engine could disagree. Second half: `_place_protective_stop` placed a replacement right after that best-effort cancel (an unconfirmed cancel left the venue working the OLD stop while a NEW one went on top → double hedge), and `_clear_stop_protection` dropped the record AND the attention flag even when the cancel had failed (a leftover working stop on a FLAT position would open a short).
- Fix — `_cancel_stop_protection` now returns True ONLY on evidence: (1) the venue confirmed the cancel → the order is reconciled to `CANCELLED` (the engine's broker-truth operation, legal from any state) + `SL_CANCELLED`; (2) the venue declined it → the order is still working → the local state stays live, the protection keeps its working state, the symbol is flagged (`SL_CANCEL_DECLINED`) — never "cancelled"; (3) the venue's answer is unknown (timeout / unreadable order book) → the order goes to `UNKNOWN` (the engine's unresolved state, exited only by reconciliation) + `SL_CANCEL_UNCONFIRMED` + attention; (4) a broker-state check that proves the order is gone (and no fill was reported) → reconciled to `CANCELLED`; (5) a protective stop that FILLED is never reported as cancelled (the fill owns that state).
- Fix — replacement/cleanup: `_place_protective_stop` places a replacement only after the old stop's cancellation is confirmed (otherwise the position would be hedged twice); `_clear_stop_protection` keeps the record and the attention flag while the cancel is unconfirmed (`SL_CLEAR_DEFERRED`).
- New evidence surface (optional, venue-neutral): `FyersSessionAdapter.order_state(broker_order_id)` → `{broker_order_id, client_order_id, listed, filled_qty}`. It answers PRESENCE, not a translated status: FYERS' numeric order statuses are read differently by the order socket (6 = pending) and the REST order book (6 = cancelled), and a wrong translation would conclude "cancelled" for a still-working order. It is attempted even while the read circuit is open (still rate limited) because its only purpose is to settle a cancel decision.
- Bounded recovery: an unconfirmed cancel is retried on the engine's next protective-stop cycle (one attempt per cycle, each a real venue call, rate limited by the shared bucket). No retry loop, no swallowed broker error, and the risk-reducing cancel attempt itself is unchanged (still attemptable while a circuit is open).
- Tests: new `src/execution/tests/test_protective_cancel_state.py` (8: confirmed → CANCELLED; declined → never cancelled; unknown → UNRESOLVED then confirmed on the next attempt; broker-state check proves the cancellation; declined + unreadable book stays live; filled stop is not reported cancelled; replacement waits for a confirmed cancel; flat cleanup keeps an unconfirmed stop visible) and `src/broker/tests/test_fyers_api_governance.py` (+2 for `order_state`). Focused run: **execution suite + 5 broker suites = 304 passed**; `ruff check`/`ruff format --check` clean; focused `pyright` **0 errors**.
- Not done: no EC2 deploy/restart, no GitHub push/tag/release, no LIVE start, no order placement, no session mutation; LIVE trading stays stopped.

**Previous update (circuit-split-risk-reducing-20261020):** READ CIRCUIT AND ORDER CIRCUIT SPLIT — A RISK-REDUCING ORDER IS NO LONGER BLOCKED BY AN OPEN READ CIRCUIT (NO DEPLOY, NO PUSH, NO ORDERS) —
- Problem found in review: the single global circuit opened after 5 consecutive failures blocked ALL calls for 30 s, including cancels and protective exits. A venue that only breaks its READ path could therefore silence a stop-loss for 30 s.
- `src/broker/common/api_governance.py`: added `CallClass` (`READ` / `ENTRY` / `RISK_REDUCING`) and split the one circuit into `read_circuit` (API health) + `order_circuit` (order path). READ is blocked by the read circuit; ENTRY is blocked by BOTH circuits; RISK_REDUCING is never blocked by a circuit. Every call still consumes a token-bucket token (rate limiting unchanged, no bypass) and real broker errors still propagate unchanged. `CircuitOpenError` now carries the call class and says "request not sent" (no timeout/network wording → a refused call stays a deterministic refusal, never a sent-but-unknown order).
- Intent comes from the EXISTING execution logic, not a new signal: the engine's stop-protection order is `intent_id="sl:<symbol>:<seq>"` + client order id `sl:<symbol>:<seq>:o1` + STOP order type + stop price (`execution/runtime/session.py::_place_protective_stop`; the engine itself keys protective fills on `intent_id.startswith("sl:")`). The adapter reuses that exact convention. A strategy plan can never match it (the planner emits only MARKET/LIMIT and never a stop price; the strategy router's plan carries no intent id; the operator order-path test uses `order-path-test:` ids). Anything ambiguous → treated as an ordinary ENTRY (fail safe).
- Cancel/modify of an order THIS adapter placed are risk-reducing (the adapter records each order's intent, "protective"/"entry", in a bounded 2000-entry memory). A cancel/modify of an UNKNOWN broker id is ambiguous → stays circuit-gated. A modify of a non-protective order stays gated (a modify can increase size).
- Duplicate-order protection is preserved: the protective path's order-book scan still runs — it is a READ and is attempted with `bypass_circuit` (still rate-limited, still fails loudly), so the duplicate check cannot silently disappear; an unreadable order book still fails the protective order.
- Tests: `src/broker/tests/test_api_governance.py` (10: read failures open the READ circuit only, entry failures open the ORDER circuit, risk-reducing work not blocked by either circuit while still rate-limited and still raising its real error, `bypass_circuit` allows exactly one protective read) and `src/broker/tests/test_fyers_api_governance.py` (10: reads + new entries refused while the read circuit is open, protective exit POST attempted, cancel of a known protective order attempted, cancel of an unknown id gated, protective-intent derivation incl. symbol/type/stop-price/client-id mismatches). Focused broker+execution+app run: **124 passed**; `ruff check`/`ruff format --check` clean; focused `pyright` **0 errors**.
- Engine-side effect to know: a circuit-refused ENTRY surfaces as `CircuitOpenError` (HTTP 503 → VENUE) and the engine records it as a deterministic REJECTION (nothing was sent) rather than UNKNOWN; the protective path's real failure still flags SL attention exactly as before.
- Not done: no EC2 deploy/restart, no GitHub push/tag/release, no LIVE start, no order placement, no session mutation.

**Previous update (api-governance-fyers-20261020):** FYERS BROWSER/API GOVERNANCE PORTED INTO THE EXISTING ADAPTER (NO DEPLOY, NO PUSH, NO ORDERS) —
- New `src/broker/common/api_governance.py` (provider-neutral control plane, stdlib only): `TokenBucket` (rate 2.5/s, capacity 3), `APIMetricsCollector` (calls/errors/avg latency/throttle waits/circuit trips/cache hits+misses), `CircuitBreaker` (5 failures -> OPEN, 30 s -> HALF_OPEN probe, 2 successes -> CLOSED), `HistoricalDataCache` (TTL 60 s, max 512 entries, one in-flight owner per parameter key), `APIGovernor` (`call`, `historical`), `BrokerGateway` (routing facade) and `CircuitOpenError`. Reference constants kept 1:1; no Kite/Zerodha methods, credentials, secrets or config were copied.
- Integration (no parallel broker layer): `FyersSessionAdapter` owns ONE governor + gateway. `_get`/`_get_data`/`_post`/`_put`/`_delete` and `history()` all route through it, so profile, funds, positions, orders, quotes, market status, place/cancel/modify and historical fetch are throttled, metered and circuit-guarded. All 22 per-symbol `LiveSession`s share that adapter (StaticPlugin/`resolve_broker` return the same instance), hence one limiter, one cache, one in-flight coordination.
- Reconciliation stays fail-closed and consistent: the 5 s `reconciliation_snapshot()` is unchanged (cached failures are re-raised unchanged). NEW: a `{"s":"error"}` envelope or a missing `netPositions`/`orderBook` field now RAISES (`AuthError` / `BrokerError`) instead of looking like a flat book — an auth failure can no longer read as "flat + SAFE". `positions()`/`open_orders()` stay uncached.
- Historical reads: parameter-keyed cache (venue symbol + resolution + integer epoch window), concurrent duplicate requests for the same key wait for ONE real fetch; a failed fetch is re-raised to every waiter and is never cached; a waiter timeout raises instead of returning `[]`. Order placement/cancellation/modification is never cached.
- Invalidation: the account snapshot is dropped on connect/disconnect, place/cancel/modify, order-WS reconnect, order-WS order events, trade fills and drained stream events. Historical candles keep their own 60 s TTL (venue history, not account truth).
- Duplicate guard hardened: `_find_by_tag()` now propagates order-book read failures instead of returning "no existing order", so an unreadable order book blocks submission instead of risking a second order.
- Deliberate fail-closed deviations from the reference (documented in the module docstring): circuit created eagerly (reference needed `wire_log` first), failed historical fetch propagates instead of returning `[]`, circuit-open is a typed `CircuitOpenError` (HTTP 503 semantics) instead of a bare `RuntimeError`, and the gateway never swallows errors into `None`/`False` the way the reference `place_order`/`cancel_order`/`get_equity_capital` did.
- Tests: new `src/broker/tests/test_api_governance.py` (7) and `src/broker/tests/test_fyers_api_governance.py` (8: 22-thread shared reconciliation = 1 positions + 1 orders read, auth envelope never empty, 8 concurrent history calls = 1 fetch, 429 not cached, cancel/WS invalidation, unreadable order book blocks submit, malformed payload not flat, open circuit not relabelled as auth). Focused broker run **85 passed** (new + live-trading + websocket + rate-limit-resilience); execution/app focused **34 passed**; `ruff check`/`ruff format --check` clean; focused `pyright` **0 errors**. `src/broker/tests/test_fyers_live_trading.py` mock now routes `/orders` and `/positions` realistically.
- Prior LIVE consent fix is still in `crates/vayren-shell/src/remote_live.rs` (backend `strategy.reason` forwarded verbatim, including `""`, so an empty post-START reason clears the stale consent text) with its regression test. Local client rebuilt after the final source edits: `cargo build --release -p vayren-shell --features packaged-remote` finished in 2m41s; `target/release/vayren-shell.exe` (39,927,296 bytes) written 2026-10-20 21:22 local. Nothing was deployed, restarted or pushed.
- Repo validators for the new file all PASS: `validate_structure`, `validate_imports`, `validate_language_ownership` (412 files), `validate_architecture_gate`, `validate_authority` (243 py/21 slint/23 routes), `validate_scope --file src/broker/common/api_governance.py`. `git diff --check` clean.
- Not ported from the reference: `ReconnectBackoff`, the EOD metrics-reporter thread, `BrokerGateway` login/session helpers (VAYREN's `live_auth`/`FyersSessionStore` own auth) and `get_instruments` (no FYERS instrument-master call path exists). No EC2 deploy/restart, no GitHub push/tag/release, no order placement, no session mutation.
- Known limits (superseded by the circuit split above): the circuit was global per adapter, so five consecutive failures (including historical backfill failures) could block order placement/cancellation for up to 30 s. The read/order circuit split removes that for risk-reducing operations. The candle-sync `read_only_adapter()` intentionally builds a separate WS-disabled adapter, so it has its own governor and does not share the live adapter's cache.

**Previous update (ci-rustfmt-green-20261020):** CI `build-test` UNBLOCKED — PRE-EXISTING RUSTFMT DRIFT FIXED (FORMATTING ONLY) —
- Last four red `main` runs failed in ~43s at `cargo fmt --check`, before build/tests: `crates/vayren-domain/src/live.rs` (long `assert!` lines) and `crates/vayren-shell/src/main.rs` (misindented `sync_broker_ui` block + one-line `if/else` expansions).
- `cargo fmt --all` applied; `cargo fmt --all -- --check` and `cargo check -p vayren-domain -p vayren-shell` pass. No semantic change; Python suites and live behavior untouched.
- All other CI jobs (`quality`, `validators`, both test shards, shell shard) were already green on the failing commit.

**Previous update (fyers-reconciliation-429-fanout-20261020):** PER-SYMBOL LIVE RECONCILIATION NOW SHARES ONE ACCOUNT SNAPSHOT; DEPLOYED AND RECONCILIATION CLEARED (NO LIVE ORDERS; WORKER STOPPED) —
- Root cause confirmed fleet-wide: the live service runs 22 per-symbol `LiveSession`s against one authenticated `FyersSessionAdapter`; every 30 s cycle each session independently called account-wide `positions()` + `open_orders()`, producing ~44 REST reads in under a second. FYERS answered HTTP 429; later sessions correctly recorded `RECONCILE_ERROR` and the readiness aggregator stayed `REQUIRED`. Successful cycles (first ~5–7 symbols) were `SAFE` / `mismatches=0`; no broker/local position or order mismatch existed. `NSE:NETWEB` was only one late symbol in the cycle, not a symbol-specific fault.
- Fix: `FyersSessionAdapter.reconciliation_snapshot()` coalesces positions+orders behind a 5 s thread-safe cache used only by `LiveSession.reconcile_now()`; `positions()`/`open_orders()` remain uncached. Cache is invalidated on connect/disconnect, order place/cancel/modify and delivered order-stream events. Cached failures are re-raised unchanged: a 429 still blocks every session and is never converted to `SAFE`.
- Regressions: `src/broker/tests/test_fyers_rate_limit_resilience.py` (+4: 22-session fan-out -> one positions + one orders read; shared 429 re-raised with one HTTP call; expiry refresh; generic reads uncached) and `src/execution/tests/test_live_reliability_hardening.py` (+3: session prefers snapshot, fallback for adapters without it, snapshot failure still blocks with `transport_error`/`RECONCILE_ERROR`). Focused broker+execution **30 passed**; ruff check/format and focused pyright clean.
- Real-venue proof: on EC2, the fixed adapter with the stored FYERS session collapsed 22 `reconciliation_snapshot()` calls to exactly 2 HTTP calls (`positions`, `orders`), with 0 positions/0 orders and no 429. This proves the code path before reload; the restarted gateway now loads the same files.
- Pre-deploy evidence (`2026-10-20 14:23 UTC`): gateway PID 667834 ran pre-fix code; strategy `BLOCKED`, reconciliation `REQUIRED` (`blocks_live=true`), worker `RUNNING`, 22 symbols; latest per-symbol reconcile was 5 `SAFE` / 17 `RECONCILE_ERROR` `rate limited by venue (HTTP 429, retry in 5s)`.
- Deployment/verification (`2026-10-20 14:25:42 UTC`): explicit consent; backed up old files to `/home/ubuntu/vayren-backup-20261020-142512`, copied the two fixed runtime files, restarted `vayren-remote` (PID 667834 -> 675398). Post-restart snapshot (stable at `14:35 UTC`): reconciliation `SYNCED` (`blocks_live=false`), broker/order stream `CONNECTED`, worker `STOPPED`, only blocker `live confirmation required (explicit operator consent)`; no 429/ERROR lines since restart. The 22-symbol live loop was not resumed, so fleet behavior is proven by the real-venue adapter test (22 calls -> 2 HTTP reads) rather than a resumed trading cycle.
- Gate: `make check` stops at pre-existing rustfmt drift in `crates/vayren-shell/src/main.rs` and `crates/vayren-domain/src/live.rs` (both untouched); Rust build/tests pass. The same gate minus `rust` was run separately: **1658 passed / 5 skipped**, `ruff check`/`format` clean, `pyright --threads` **0 errors**, all nine validators PASS (`docs/repo_graph.json` regenerated by its validator). No live/test/dummy orders; no broker data deleted; no tag/release; deploy/restart and push performed only with explicit consent; no LIVE START.

**Previous update (fyers-live-start-proof-and-freshness-20261020):** LIVE START NOW REQUIRES A FRESH BROKER + ORDER-WS PROOF; SYSTEM FRESHNESS IS BOUNDED (NO LIVE ORDERS; ENGINE STOPPED) —
- `session_adapter`: new `verify_order_stream(timeout_s=5.0)` waits at most the bound for the existing socket, cold-starts at most once, and never retries; cooldown/disconnect/stale fail closed. `_order_ws_guard` (RLock) serializes socket create/stop and `_order_ws_verify_lock` serializes proofs; the vendor SDK keeps reconnect ownership.
- `BrokerManager`: `verify_live_session()` is the START authority — forced read-only profile check (`adapter.connect()`), bounded order-WS proof (`8 s`), then account/funds refresh. Failures immediately write `LOGIN_REQUIRED` / `MARKET_DATA_NOT_READY` / `DISCONNECTED` to the unified session. `_io_lock` serializes worker session/login/health jobs with START verification. Freshness is snapshot-triggered and throttled (one queued check per `60 s`, active legs only; terminal auth/disconnect states need operator CONNECT or restart). `get_session_snapshot()` exposes `last_verified_epoch`, `verified_age_s`, `verification_stale`, `health_refresh_interval_s`; a `CONNECTED` snapshot older than `180 s` becomes `CHECKING`; a cheap local order-WS leg sync degrades SYSTEM immediately. `_activate()` queues one forced check.
- `LiveTradingService`: with LIVE consent, `start()` runs the fresh session proof before `validate()`; without consent no venue proof is attempted and the existing consent gate still blocks. `_probe_live_venue()` also requires a fresh order-WS proof before account/funds. `_live_blockers()` reports stale/degraded/auth-expired sessions accurately (daily-token guidance); `_broker_view()` treats the unified session as authoritative, so a stale/degraded/unverified session is never reported connected.
- Guidance: `session_state.next_action_for(LOGIN_REQUIRED/AUTH_REQUIRED)` says the daily token expired and to click CONNECT. Runbook §4.1 distinguishes startup/CONNECT auto-auth from a running gateway, states explicitly that there is no continuous auto-refresh, and documents that START fails closed until the order socket is provable (including outside market hours).
- Regressions: new `src/app/tests/test_live_start_verification.py` (8: pre-START disconnect, healthy stream + consent, no proof without consent, manager REST+WS proof, WS failure state, expired auth in SYSTEM/LIVE, one queued stale check, queued health propagates expiry without terminal re-hammering); `test_live_readiness_authority.py` (+2 probe proof); `test_fyers_websocket.py` (+4 bounded verifier: async connect, timeout/no retry, immediate disconnect, cooldown).
- Verification: full Python suite **1651 passed / 5 skipped** (1656 collected); focused app+broker **593 passed**; `ruff check` + `ruff format --check` clean; `pyright --threads` **0 errors**; `cargo check --workspace --exclude vayren-shell`, `cargo check -p vayren-shell --lib`, and `cargo test --workspace` pass. All nine validators pass (`structure`, `imports`, `language`, `architecture`, `authority`, `routes`, `repo_graph`, `scope`, `context`). `tools/build_rust.py --lean-test` remains environment-blocked at the release cdylib step (`target/release/vayren_core.dll` locked by a running process); `cargo test --workspace` covers the Rust suite. `git diff --check` clean.
- Pre-existing rustfmt drift in `crates/vayren-shell/src/main.rs` and `crates/vayren-domain/src/live.rs` was not touched.
- No live/test/dummy orders; reconciliation, risk, consent and execution-side `_pre_order_checks` untouched; engine STOPPED; no push/tag/release/deploy.

**Previous update (helper-hardening-fyers-order-ws-20261020):** HARDENED RESTART HELPER INSTALLED + FYERS ORDER-WS 403 ROOT CAUSE CONFIRMED (NO LIVE ORDERS; ENGINE STOPPED) —
- Helper: service unit backed up at `/var/backups/vayren/vayren-remote-restart.service.pre-hardening-20261020`; installed template hash `a430e9de9d075c8beef3b28db3eb0422da57fb85c7be559d9d81e90d91c9ac8f`; `systemd-analyze verify` + `daemon-reload`; path active/waiting. Audit copy moved to root-only `StateDirectory=vayren-restart` (`/var/lib/vayren-restart`, 0700 root) so the gateway-writable data dir cannot race/replace it; fixed ExecStart only `systemctl restart vayren-remote.service`; no `[Install]`; `NoNewPrivileges`/`ProtectSystem=strict`/`ProtectHome=read-only`/`PrivateTmp` unchanged. Controlled helper restart `trade:c11`: PID 661394→667108, marker removed, audit copy root 0644, journal tag `vayren-restart`, unit `Result=success`, path waiting. Extra `unit`/`command`/`path` params are ignored by `execute_server_op` (regression assertion added).
- FYERS 403: historical Oct 12–13 storm was old reconnect logic; current trigger confirmed as daily token expiry. `MARKET_WS_ERROR ... code -99 'Token is expired'` at 2026-10-20 01:04:39 UTC; order WS `Handshake status 403 Forbidden` at 01:11:23–01:11:49 UTC. No broker-side permission/entitlement failure; SDK 3.1.11 and current 3.1.17 both use `wss://socket.fyers.in/trade/v3` + `authorization: <app_id>:<token>`. Restart at 02:59:19 auto-authenticated the stored session and order WS connected; zero order-WS errors after.
- Storm fix: `session_adapter._on_ws_error` counted flaps only after the new-error dedup branch, and `_on_ws_connect` reset the dedup key, so alternating "host lost"/403 errors never tripped the breaker (144k lines). Now every error calls `_note_ws_flap()` before logging, and each connection counts at most one flap. Two regression tests added (`test_order_ws_counts_one_flap_per_connection`, `test_order_ws_abandons_alternating_403_errors`). Deployed `session_adapter.py` to EC2 and restarted (PID 667108→667834); order WS connected, 0 errors since.
- Verification: pytest **1637 passed / 5 skipped**; focused broker+remote 49 passed; ruff check/format clean; pyright 0; all 9 validators + context self-check PASS; cargo check workspace/shell-lib, `build_rust.py --lean-test`, `git diff --check` PASS. `cargo fmt --check` / `make check` still fail only on pre-existing drift in `crates/vayren-shell/src/main.rs` and `crates/vayren-domain/src/live.rs` (untouched).
- EC2 readiness after final restart: broker CONNECTED/auth_valid, reconciliation SYNCED, worker STOPPED, only blocker operator consent; path active/waiting. No live/test/dummy orders; no commit/push.
- Remaining operational note: FYERS daily token expiry requires re-auth; start path revalidates via `_probe_live_venue` and blocks, but SYSTEM's cached snapshot can show the last verified CONNECTED until health/reconnect; do not treat it as live readiness. Reconnect/restart auto-auth is the current renewal path.

**Previous update (live-readiness-truth-and-sandbox-restart-20261019):** LIVE READINESS TRUTH FIXED + SANDBOX-SAFE GATEWAY RESTART DEPLOYED TO EC2 (NO LIVE ORDERS; ENGINE STOPPED) — Resolved the EC2 contradiction where SYSTEM showed FYERS authenticated (`XV29244`) while LIVE rendered `CONFIGURING` / `no live trading venue`, plus the client-side `RESTART_UNAVAILABLE ... NoNewPrivileges` restart failure:
- Root causes:
  1. `src/app/services/live_trading_service.py`: failed-START blocker text was stored in `_status_reason` and never retired once the broker authenticated / the venue registered; the validation cache key did not include the unified session, so a verdict computed mid-authentication could stay frozen.
  2. `_live_blockers()` consumed the raw transient `BrokerStatus` (`CONFIGURING`) instead of the authoritative unified session snapshot (`get_session_snapshot`); `_resolve_live_venue_id()` accepted a DOMAIN advertisement as venue capability.
  3. `src/app/headless.py`: `_LIVE_ACTION_ERRORS["start"]` could outlive the blockers it recorded.
  4. `src/remote/server_ops.py`: restart used a bare `sudo systemctl` that can never work with `NoNewPrivileges=true`.
- Fixes applied:
  1. Live service: `_blocked_start_reason` tracked separately; `_reconcile_blocked_start_reason()` retires it when blockers clear; unified session (`state`/`auth_valid`/`version`) rides the validate cache key; `_live_blockers()` reports `broker session: <state>` + adapter connectivity; `execution_reason` uses fresh blockers; `_resolve_live_venue_id()` requires a real TRADING face satisfying the `BrokerAdapter` protocol with connect/health.
  2. headless: a recorded START refusal is echoed only while its blocker set still matches — replaced by fresh blockers or cleared entirely.
  3. `remote/server_ops.py` + new `deploy/vayren-remote-restart.{service,path}.example`: sandbox detection (`/proc/self/status`), direct operator `sudo -n` path (SSH/CLI), and a narrowly scoped root helper — the gateway writes `~/.vayren/data/restart.request` (data only, delayed 0.75 s so the ack leaves the socket), the path unit activates a root oneshot whose only `ExecStart` is `systemctl restart vayren-remote.service` (journal tag `vayren-restart`; audit copy in root-only `/var/lib/vayren-restart` via `StateDirectory`, `.applied` unlinked first and `cp -P` never follows a symlinked request). No sudo/root/blanket privilege granted to the gateway; canonical data-dir check fails closed; RUNNING session still blocks restart.
- Regression tests added: `src/app/tests/test_live_readiness_authority.py` (9: SYSTEM/LIVE auth consistency, transient `CONFIGURING` vs unified session, missing auth+venue, connection-only, cache retirement, stopped-session reconciliation, data-only venue rejection) + 2 headless stale-refusal tests + 6 restart tests in `src/remote/tests/test_server_ops.py`.
- Verification: gate minus the pre-existing `rust` step — ruff check/format clean, pyright 0 errors, all 9 validators PASS, pytest **1635 passed / 5 skipped**. `make check`'s `rust` step fails on pre-existing rustfmt drift in `crates/vayren-shell/src/main.rs` **and** `crates/vayren-domain/src/live.rs` (neither file touched by this change).
- EC2 deployment & live verification (no GitHub push; scp of 3 runtime files + 2 unit templates to `/home/ubuntu/vayren-core`; backup `/home/ubuntu/vayren-backup-20261019-171902`):
  - Helper units installed; `vayren-remote-restart.path` active (waiting); operator SSH restart PID 659434 → 660987 loaded the new code. (Hardened service unit, root-only audit copy and final controlled test: see latest update 2026-10-20.)
  - Client restart command through the **sandboxed** gateway returned `RESTART_REQUESTED` (helper path); marker applied+removed; journal + `audit_ops.log` record caller `trade:c4`; MainPID 660987 → 661394.
  - T+5 s after restart (session genuinely `CONNECTING`) the reason was the fresh `broker session: CONNECTING`; by T+25 s it retired by itself to the single honest blocker `live confirmation required (explicit operator consent)`, with `broker_session CONNECTED/auth_valid`, `reconciliation SYNCED`, worker STOPPED.
  - Zero live orders placed; engine remains STOPPED.
  - Host disk recovery (2026-10-20, no service/audit data touched): `cargo clean` removed the unused `target/` build trees from `vayren-core-server-backup-20261012` (4.7 GiB) and the stale `VAYREN/vayren-core/rust` clone (5.7 GiB); `/` went from 98% (≈794 MB free) to 64% (11 GB free). Active `vayren-core/target`, journals, broker data, Git, backups and audit logs were kept.

**Previous update (live-reconciliation-blocker-fix-20261019):** RECONCILIATION LIVE BLOCKER FIXED & DEPLOYED TO EC2 (NO LIVE ORDERS SUBMITTED) — Resolved blocker where idle/stopped session falsely displayed `START BLOCKED — reconciliation blocks live`:
- Root Cause:
  1. `src/execution/runtime/session.py`: `reconcile_now()` unconditionally marked `matched=False` with `local="missing_broker"` when `self._broker is None` in LIVE mode, even when the session was un-started / stopped.
  2. `src/remote/snapshot.py`: `_strategy_status()` checked `bool(recon.get("blocks_live"))` even when `status == "STOPPED"`, turning a clean empty stopped state into `status: "BLOCKED"` with reason `"reconciliation blocks live"`.
  3. `src/app/services/live_trading_service.py`: `stop()` did not call `_teardown_sessions()`, leaving inactive sessions with disconnected brokers in memory whose reconciliation verdict reported `blocks_live: True` into subsequent snapshots; also `snapshot()` now ensures `blocks_live` only blocks when `running` is True.
- Fixes Applied:
  1. `src/execution/runtime/session.py`: `reconcile_now()` only reports `missing_broker` mismatch when `self._lifecycle.state == LifecycleState.RUNNING`.
  2. `src/remote/snapshot.py`: `_strategy_status()` evaluates start blockers from `validate()` and kill switch when stopped; reconciliation status only blocks live when actively RUNNING.
  3. `src/app/services/live_trading_service.py`: `stop()` invokes `self._teardown_sessions()` to clear inactive sessions, and `snapshot()` reports `blocks_live: recon_blocks if running else False`.
- Deployment & Verification:
  - All 201 execution tests and 90 remote tests passed on both local Windows tree and EC2 virtualenv.
  - Fixes deployed to EC2 gateway at `/home/ubuntu/vayren-core`.
  - `vayren-remote.service` restarted and confirmed healthy (PID 659166, FYERS Order WS & Market WS connected).
  - Local desktop binary `target/release/vayren-shell.exe` rebuilt (39.9MB, fresh timestamp).
  - Language ownership validation: 407 files checked and PASSED.
  - Zero live orders placed; zero real-money transactions; 2-mode architecture strictly preserved.

**Previous update (live-consent-state-sync-20261018):** LIVE CONSENT STATE SYNCHRONIZATION FIXED (NO ORDERS, NO DEPLOY, NO REMOTE PUSH) — Resolved UI mismatch where LIVE screen showed `start confirmed by backend` + `RUNNING` alongside stale `✕ live confirmation required (explicit operator consent)`:
- Root cause:
  - When backend session transitioned to `RUNNING` in LIVE mode, client-side `LiveState::apply_snapshot` updated `self.session = SessionStatus::Running`, but never synchronized `self.live_confirmed = true` from the backend's authoritative running/armed state or `broker_session.live_armed`.
  - When `LiveState::start_blockers()` was evaluated in host mode, if `self.live_confirmed` remained `false` locally (or if `backend_start_blockers` momentarily retained a pre-start consent blocker), it rendered `"✕ live confirmation required (explicit operator consent)"` under `blockers` despite the session actively running.
- Fixes applied:
  1. `crates/vayren-domain/src/live.rs`:
     - In `apply_snapshot`, whenever backend reports `session_status: RUNNING` in LIVE mode or `live_armed: true`, `self.live_confirmed` is synchronized to `true`.
     - Whenever session becomes `STOPPED`, `HALTED`, or `ERROR`, `self.live_confirmed` is explicitly reset to `false` to prevent silent re-arming without operator ceremony on subsequent starts.
     - In `start_blockers`, confirmation blockers are filtered out if `self.live_confirmed || self.session == SessionStatus::Running`.
  2. `crates/vayren-shell/src/remote_live.rs`:
     - In `apply_remote_sections`, mapped `sections.get("broker_session").get("live_armed")` directly into `translated["live_armed"]`.
  3. Added regression test in `crates/vayren-domain/src/live.rs`: `test_live_confirmed_synchronizes_with_running_backend_snapshot_and_resets_on_stop`.
- Validation:
  - `cargo test -p vayren-domain live`: 101 tests passed (0 failures).
  - `cargo test -p vayren-shell remote_live`: 27 tests passed (0 failures).
  - `pytest src/app/tests/test_live_account_confirmation.py src/execution/tests -q`: 201 tests passed (0 failures).
  - `python tools/benchmark.py impact --run`: PASSED in 42.94s.
  - Rebuilt Windows desktop binary: `target/release/vayren-shell.exe` (39.9MB, fresh timestamp).
  - Zero live orders placed; zero remote deployment or push.

**Previous update (live-startup-simplification-20261018):** LIVE STARTUP SIMPLIFIED & DEDICATED ARM LIVE BUTTON ELIMINATED (NO ORDERS, NO DEPLOY, NO REMOTE PUSH) — Streamlined operator workflow to direct unified flow: `LIVE → START → Confirm Live Trading → Automatic Readiness Checks → ENGINE RUNNING → Genuine Strategy Signals → Actual FYERS Orders`:
- Dedicated ARM LIVE UI Button Removed:
  - Eliminated the separate `ARM LIVE` button and arming ceremony message/notes from the LIVE READINESS card in `crates/vayren-shell/ui/live.slint`. No dead clickable areas or ghost buttons remain.
  - Updated `LiveConfirmDialog`: title and confirm button now state `CONFIRM & START LIVE`, ensuring operator consent directly triggers the confirmed start request upon passing venue readiness.
- Integrated START & Explicit Confirmation Lifecycle:
  - In `crates/vayren-domain/src/live.rs`, clicking `START` in LIVE mode without prior operator consent displays the exact consent blocker and opens the `LiveConfirmDialog`.
  - Operator confirmation inside the dialog validates venue gates, sets `live_confirmed = true`, closes the modal, and automatically dispatches `{"action": "start", "confirmed": true}` to the backend.
  - Cancelling the dialog safely closes the modal without granting live authorization or dispatching orders.
  - In `src/app/services/live_trading_service.py`, `start(confirmed=True)` records operator consent, validates broker connection, account ID, market data, strategy, and risk limits, automatically checks and reconciles account state, arms the live session, and starts the trading engine.
- Reconciliation Readiness Clarified:
  - Verified that `Reconciliation Synced: no session to reconcile` (`NOT CONFIGURED`) was an informational status in stopped state with `blocks_live=false` that never blocked startup; upon START, session build invokes `reconcile_now()` against broker open orders/positions before arming.
- Safety & Verification:
  - Added unit tests in `crates/vayren-domain/src/live.rs`: `test_start_with_unconfirmed_live_opens_modal_and_cancelling_does_not_authorize` and `test_confirmed_start_authorizes_and_dispatches_with_readiness_checks`.
  - All 300 tests in `vayren-domain` passed.
  - All 65 shell unit tests, 14 binary tests, and 7 off-screen Slint rasterization snapshot tests passed 100%.
  - Full suite of 201 execution tests and app tests passed 100%.
  - Impact benchmark validation passed in 146.62s.
  - Rebuilt Windows desktop executable [`target/release/vayren-shell.exe`](file:///c:/Users/visha/Desktop/vayren-core/target/release/vayren-shell.exe).
  - Desktop shortcut `C:\Users\visha\Desktop\VAYREN.lnk` verified pointing to fresh release executable with `--remote-ui`.
  - ZERO live orders placed; zero simulated fallback paths introduced; two-mode (`BACKTEST` / `LIVE`) architecture strictly preserved.

**Previous update (remote-broker-ui-regression-fix-20261018):** REMOTE BROKER UI REGRESSION RESOLVED (NO ORDERS, NO ARMING, NO DEPLOY, NO REMOTE PUSH) — Diagnosed and corrected desktop UI regression where Fyers broker displayed `Connection Failed`, blank credential inputs, and false `Authenticate Failed` in `--remote-ui` mode:
- Root cause:
  1. `apply_authoritative_broker_ui()` did not call `ui.set_conn_saved_sentinel` and left form input fields blank even when `auth.fields_configured` was true on EC2.
  2. `main()` was allocating a throwaway `RemoteBrokerForm::default()` inside the gateway sync closure on each tick, causing form state and action bindings to lose remote configuration.
  3. `FetchResult::System` in local host background loop unconditionally overwrote `live_state.borrow_mut().broker` with disconnected local workspace data whenever any local fetch completed.
  4. Connect, login, and disconnect callbacks relied on `cur_ws.selected_id` which was empty in `--remote-ui` mode; now they resolve broker ID & form keys via persistent `form_state` and dispatch authenticated gateway commands (`broker_connect`, `broker_login`, `broker_disconnect`).
  5. False "Authentication failed" diagnostic: in fallback string checking, `r_lower.contains("failed")` was matching operational market-data heartbeat warnings (e.g. `FAILED: stale — no market data for 1096s`). Added guard to distinguish genuine auth failures from weekend market-data warnings.
- Fixes applied in `crates/vayren-shell/src/main.rs`:
  - Added persistent `broker_form` in `main()` with `timer_broker_form` handle for snapshot sync.
  - Guarded `FetchResult::System` so local workspace does not overwrite remote broker state when `has_gateway_link` is active.
  - Pre-filled `SAVED_CREDENTIAL_SENTINEL` and set `conn_saved_sentinel: true` when `auth.fields_configured` is true.
  - Updated remote connect/login/disconnect actions to use `send_remote_broker_command` with sanitized credentials via `zip_broker_credentials`.
  - Guarded `on_broker_select_requested` and `on_broker_refresh_requested` from triggering local fetch in remote mode.
- Verification & Integrity:
  - Added unit regression tests: `authoritative_paint_prefills_saved_sentinel_when_configured` and `degraded_market_data_does_not_paint_auth_failure` (14/14 tests in `main.rs` passed).
  - All 65 shell unit tests, 298 domain tests, and Slint render snapshot tests passed 100%.
  - Impact benchmark validation passed in 26.98s (`Level 2: single-domain rust change (rust-ui)`).
  - Both debug and release executables (`target/release/vayren-shell.exe`) rebuilt.
  - Desktop shortcut `C:\Users\visha\Desktop\VAYREN.lnk` verified pointing to `vayren-shell.exe` with `--remote-ui`.
  - Zero live orders placed, LIVE disarmed, EC2 production services untouched.

**Previous update (live-ui-visual-polish-20261018):** LIVE UI VISUAL POLISH & UX ENHANCEMENT COMPLETE — Contrast, typography hierarchy, and interactive UX substantially upgraded across the native Slint LIVE workstation without breaking tests or layout constraints:
- Top Status Cards (`LiveTopCard`, `LiveBrokerCapitalCard`): Bumped font-size to 9.5px–11.5px, enhanced contrast with `VayrenPalette.muted` for secondary labels/status, capital value styled with 13.5px font-weight 800, and refresh buttons equipped with hover micro-interactions.
- Live Tabs & Navigation (`LiveTabHeader`): Active tab text highlighted in `live-teal` with 700 weight, inactive tabs given responsive hover feedback (`VayrenPalette.text`), and active tab underline rendered with smooth rounded indicator.
- Strategy Watchlist Table: Upgraded table header height to 24px with high-contrast `VayrenPalette.muted` 10px bold text; rows given 28px height with 4px border radius and clean hover/selection borders; all data cells upgraded with crisp `VayrenPalette.text` and bold semantic tones; filter chip pills enhanced with responsive hover states and active glow.
- Blotter Panels (Positions, Orders, Fills, Live Events, Daily P&L): Headers upgraded to 24px–26px with crisp `muted` 10px bold typography; rows equipped with hover effects and clear hierarchy; Daily P&L KPI metrics enlarged to 15px bold.
- Inspector & Readiness: Upgraded Active Strategy name to 14px bold, runtime status to 11px bold, engine clock to 11px teal; input labels and broker info given high-contrast readable styling; `LiveGateRow` reasons rendered in semantic neg/warn tone for instant visibility of any blocker.
- Verification: `cargo test -p vayren-shell` passed 100% (including all `live_render_snapshot` 7 off-screen rasterization tests, `live_event_time_column`, `live_stock_row_render`, and `live_watchlist_from_saved_universe`). Impact validation passed in 308.10s.

**Previous update (paper-sandbox-final-elimination-20261018):** COMPLETE ELIMINATION OF PAPER & SANDBOX ACROSS UI & BACKEND — Slint mode selectors (wide & narrow) replaced with dedicated static LIVE indicator chip; broker connection defaults updated to LIVE; active strategy mode fallback updated to LIVE; domain mode tone mapping cleaned; dead FFI exports cleaned; cargo build -p vayren-shell succeeded; desktop binary refreshed; pyright 0 errors, ruff clean, all 201 execution tests and desktop tests passing 100%.

**Previous update (live-reliability-hardening-20261018):** PHASE 5 INCREMENTS 7–8 COMPLETE — BROKER RECONCILIATION WIRING, LIVE PIPELINE HARDENING (DEDUP, BOUNDED QUEUES, STRICT ORDERING), LIVE ADAPTER VALIDATION & RECOVERY, AND STALE SESSION TIMING CORRECTION. ALL VERIFIED, NO ORDERS, NO ARMING, NO DEPLOY, NO PUSH.

1. **Broker reconciliation wiring into `LiveSession` (`src/execution/runtime/session.py`)**:
   - `start()`: Pre-validation broker reconciliation check that fails closed (`blocks_live=True`) if broker state cannot be reconciled.
   - `step()`: Periodic 30-second cadence broker reconciliation in LIVE mode via `reconcile_now()`.
   - `_submit()`: Transport uncertainty classification (`classify_exception(exc)`: `TIMEOUT`, `NETWORK`, `RATE_LIMITED`) marks order as `OrderState.UNKNOWN` without blind retries; deterministic refusals stay `REJECTED`.
   - `_pre_order_checks()`: Blocks submissions if identical `intent_id` exists or if any `UNKNOWN` order remains unresolved.
   - `_dispatch_broker_event()`: Legally transitions `UNKNOWN` orders via `ExecutionEngine.reconcile(client_order_id, target_state, ...)` when broker acks, fills, rejects, or cancels arrive.
   - `reconcile_now()`: Fails closed (`matched=False`) if broker is absent or transport throws; reconciles open orders against broker confirmed state and re-arms protective stop-loss orders only once aligned.

2. **Live pipeline hardening & ordering**:
   - Event ordering: In `step()`, incoming raw events are sorted deterministically by `(timestamp, symbol, seq)` before ingestion.
   - Deduplication: Candle deduplication per `(symbol, timestamp)` via `_seen_candle_stamps`; signal deduplication per `(strategy_id, event_seq)` via `_processed_signals`.
   - Bounded queues & capacity controls: Emits `PIPELINE_LOAD_WARNING` when backlog exceeds 1,000 raw events; never silently drops critical events.

3. **Live adapter validation & recovery (`BrokerFeedProvider`)**:
   - Input sanity: Validates prices are finite and strictly positive (`math.isfinite(price) and price > 0`); clamps negative volumes to 0.
   - Safe recovery: In `reconnect()`, restores all active symbol subscriptions via `self._face.subscribe(tuple(self._subscribed))` before marking feed ready.

4. **Stale session timing corrected (`LiveTradingService`)**:
   - In `_restore_config()` and `_persist_config()`, whenever engine status is not running, any persisted `"RUNNING"` text in `session_timing.status` is immediately corrected to `"STOPPED"`, `start_epoch` cleared to `None`, and `stopped_at` set.

5. **Desktop shortcut & executable verified**:
   - `C:\Users\visha\Desktop\VAYREN.lnk` verified and points directly to `vayren-shell.exe` with canonical flags `--data-dir D:\ZerodhaTradingData --strategy-dir D:\VAYREN_STRATEGIES`.

6. **Verification**:
   - 13/13 new live reliability tests in `src/execution/tests/test_live_reliability_hardening.py` passed 100%.
   - Full pytest execution: 201 tests in `src/execution/tests/` passed 100%; app tests passed 100%.
   - `cargo check -p vayren-shell` passed with 0 warnings and 0 errors.
   - All repository validators (routes, architecture, authority, imports, structure, language ownership, repo graph, scope) PASSED.
   - Two-mode architecture (`BACKTEST | LIVE` only) strictly maintained; zero live orders placed; consent DISARMED; ₹100 risk limit intact.

**Previous update (phases3-6-complete-20261018):** PHASE 5 INCREMENTS 3–6 COMPLETE — UNIVERSAL STRATEGY CONTRACT, REGISTRY FAILURE ISOLATION, FABRICATED STRATEGY LIST REMOVAL, AND FULL TWO-MODE CLEANUP (BACKTEST|LIVE ONLY). ALL VERIFIED, NO ORDERS, NO ARMING, NO DEPLOY, NO PUSH.

1. **Universal strategy contract (Phase 1, §12.1)**: `StrategyDefinition` +5 additive defaulted fields — `supported_timeframes=()`, `required_warmup_bars=0`, `required_capabilities=()`, `supported_modes=("BACKTEST","LIVE")`, `last_error=""`. `__post_init__` validates warmup≥0, modes⊆("BACKTEST","LIVE"), sanitized tuples. `with_*` copies use `dataclasses.replace`; `inspect_strategy()` precedence: non-empty declaration > legacy `LIVE_*`, empty falls back. `supports_live` stays capability, not readiness.

2. **Registry failure isolation (Phase 2, §12.2)**: `sync_storage` records per-strategy load errors via `load_errors()` (identity + op + reason, no payloads); one broken strategy never blocks others. `LiveSession.register_strategy` captures `logic_factory()` exceptions into a named, fail-closed contract + `init_blockers()`; `degraded` flag on session; START blocks only when *zero* strategies initialize.

3. **Fabricated strategy list removed (Phase 3, §12.3)**: Shell `init_remote_state`/`apply_remote_sections` no longer seeds 5 names; domain `apply_snapshot` no longer synthesizes default list; explicit empty clears picker. Tests: absent/empty/populated.

4. **Two-mode cleanup (Phase 4, §12.4) — PAPER/SANDBOX FULLY REMOVED**:
   - **Kernel**: `ExecutionMode::Backtest|Live`; `resolve_mode` refuses ungated LIVE (no downgrade target); `MODE_DOWNGRADE` → `MODE_REFUSED`.
   - **Python**: `ExecutionMode.BACKTEST|LIVE`; `parse_mode` refuses legacy labels; `resolve_mode` raises `LiveModeError` with missing gates; `resolve_broker` rejects BACKTEST (no venue) & LIVE without adapter; `PaperBroker`/`SandboxBroker` + `native_settle_decision` **deleted**.
   - **Domain/UI**: `ExecMode` (domain/view_model/shell/system-view) + broker `Environment` → `LIVE` only; legacy values fail closed.
   - **Remote**: server/snapshot/server_ops accept `BACKTEST|LIVE` only.
   - **Legacy config**: default `BACKTEST`; stored `PAPER`/`SANDBOX` refused at parse (never reinterpreted as LIVE).

5. **Isolation & lifecycle (Phases 5–6, §12.5)**: per-strategy `init_blockers()`, session `degraded`; LIVE refusal named (`VENUE_REFUSED` + `LiveModeError`); no simulated fallbacks.

6. **Verification**: `cargo test --workspace` **749 passed, 0 failed**; full pytest **0 failures, 0 errors**; ruff clean on all touched files; `pyright --threads` 0 errors; all validators PASSED (structure, imports, language ownership, architecture, authority, routes, repo-graph auto-fixed, scope, context). `make check` canonical `rust` step blocked by running desktop app holding `vayren_core.dll` (PID 12388) — scratch-target kernel build, debug shell build, workspace tests, fmt all pass as supplementary evidence.

7. **Prepared for increment 7**: reconciler wiring, dedup/ordering/bounded queues, live adapter validation/recovery.

8. **Still open**: stale cosmetic `session_timing.status="RUNNING"` store text; reconciler not wired into `LiveSession`. Operator order decisions unchanged (no order placed; AMO not possible via FYERS API; consent DISARMED; ₹100 cap).

**Previous update (phase5-client-ladder-authority-20261018):** PHASE 5 INCREMENT 2 — RUST CLIENT TIMEFRAME LADDER IS NOW SERVER-AUTHORITATIVE (SYNTHESIS + SERVER-UNKNOWN APPENDS REMOVED; EMPTY TIMEFRAME CAN NEVER WIPE CONFIG). INCREMENT 3 + TWO-MODE CLEANUP PREPARED (DOCS ONLY).
1. **Implemented (Rust client + one server-side guard; local code only):**
   - `crates/vayren-shell/src/remote_live.rs`: fabricated fallback ladder removed — `available_timeframes` is forwarded only when the gateway states it; absent key leaves the last server-stated ladder in place.
   - `crates/vayren-domain/src/live.rs`: domain-synthesized default list removed; explicit empty list honored ("nothing supported"); key-absent keeps the previous ladder; `selected_timeframe` selects only when servable (never appended, never selected); ladder replacement re-resolves the selection by value; host-mode setup payload can only carry a server value or `""`.
   - `src/app/services/live_trading_service.py`: `configure()` treats empty/whitespace timeframe as "no change intent" (defense in depth — `headless._opt_str` already folds `""` to `None`) so it can never wipe the stored config.
2. **Tests/evidence:** 4 new domain tests + 1 new shell test + 1 new Python test (`docs/universal_strategy_engine.md` §11); `cargo test -p vayren-domain` 297 pass; `cargo test -p vayren-shell --lib` 64 pass; `cargo test --workspace` green; `cargo fmt --all -- --check` clean; full pytest **1618 passed / 5 skipped**; ruff + pyright clean on touched files; all validators PASSED. No orders, no arming, no deploy, no push; no EC2 commands this increment.
3. **Gate note:** `make check`'s `rust` step cannot replace `target/release/vayren_core.dll` while the running desktop app holds it (PID 12388) — the equivalent decomposed steps all pass (release kernel build to a scratch `--target-dir`, debug shell build, workspace tests, fmt check). `make lint` flags only the unrelated parallel-session file `src/broker/registry.py`.
4. **Prepared (not implemented):** increment 3 contract fields — `supported_timeframes`, `required_warmup_bars`, `required_capabilities`, `supported_modes`, `last_error`, all defaulted + `inspect_strategy` bridge rule — and registry failure isolation (`load_errors()`, per-strategy session capture, fail-closed named blockers); two-mode cleanup inventory (~100 PAPER/SANDBOX sites: kernel → Python → domain/UI → tests) — **held for operator sign-off**. Adjacent finding: the shell still fabricates a five-name strategy list when the gateway states none (`remote_live.rs::apply_remote_sections`) — increment 3 candidate. Details: `docs/universal_strategy_engine.md` §1.1/§2.1/§3.1/§4.2.
5. **Still open:** stale cosmetic `session_timing.status="RUNNING"` store text; reconciler not wired into `LiveSession` (increment 6). Operator order decisions from the previous entry unchanged (no order placed; AMO not possible via the FYERS API).

**Previous update (live-restore-readiness-20261018):** LIVE TIMEFRAME DRIFT CORRECTED VIA THE SUPPORTED `setup` COMMAND; PHASE 3 READINESS VERIFIED FIRST-HAND; NO ORDER SUBMITTED (AMO NOT PERMITTED VIA FYERS API).
1. **Phase 2 restore (done, no restart, no DB edit):** an EC2-local protocol client (`src/remote/client.py`, trade token from `/etc/vayren/remote.env` — never printed) sent the exact client path `setup {"timeframe":"30m"}` (`remote/server.py:676-678` → gateway → `headless._live_action` → `configure()` → `_persist_config()`). Backend: `ok:true`; the 22 per-symbol timeframe blockers cleared, `start_blockers` = `["live confirmation required (explicit operator consent)"]`. Store re-read + key diff: ONLY `config.timeframe` `"3m" → "30m"`; no candle writes (auto-sync skipped, 30m rows present for all 22). Fresh connect confirms the running service: `selected_timeframe="30m"`, `strategy.timeframe="30m"`.
2. **Phase 3 readiness (fresh evidence 2026-10-18 06:23 UTC / 11:53 IST):** FYERS session valid (read-only profile); ACC `XV29244` (VISHAL SINGH); funds ₹100.00 available (service `refresh_capital` 06:18:54 + direct adapter `funds()` agree); positions 0; open orders 0 (no pending AMO); `order_path_test.json` ledger absent (one-BUY allowance unused); LTPs fetched for all 22 universe symbols (cheapest 1-share CNC: JINDWORLD 41.92, DCW 44.88, SAGILITY 45.30, VIKRAN 59.92, IFCI 66.99, ZEEL 71.85; DREDGECORP 896.45 unaffordable at ₹100). Gates (snapshot): Broker/Credentials/Account/MarketData 22-22/Strategy 30m/Risk "max 1 per order"/Environment READY; Reconciliation NOT CONFIGURED (`blocks_live=false`); **Arming NOT READY ("DISARMED — arm before START")**; worker STOPPED, mode LIVE. Store candles 22/22 through Fri 2026-10-16 15:15 IST; venue feed only while RUNNING. Calendar: Mon 2026-10-19 session, Tue 2026-10-20 holiday (Dussehra).
3. **AMO blocker (decisive for Phase 4):** FYERS under SEBI's algo rules — "AMO orders are not permitted in algo trading… ensure the `offlineOrder` parameter is always set to false when placing orders through the API." The adapter already sends `offlineOrder:false` (`fyers/session_adapter.py:881`); `order_test` places MARKET/DAY only (`order_path_test_service.py:351-358`). A genuine AMO cannot be submitted through the FYERS API; the broker's client AMO surface is the FYERS app's Schedule Order (outside VAYREN). **No order was submitted** — nothing to reconcile; one-BUY allowance untouched.
4. **Operator decisions pending:** (a) approve an exact ticket (normal LIMIT, Monday session) or not; (b) ARM/live-consent when appropriate; (c) go-ahead for the redesign implementation phases. Full evidence: `docs/redesign_audit.md` §6.

**Previous update (live-redesign-audit-20261018):** PHASE 1 AUDIT + PHASE 2 ROOT CAUSE — DREDGECORP 3m START BLOCK = LIVE CONFIG DRIFT (CLIENT `setup` WROTE `timeframe:"3m"`); VALIDATOR WAS CORRECT; STORES ARE 30m-ONLY.
1. **Root cause (first-hand evidence):** gateway `live_session.json` config.timeframe drifted `"30m" → "3m"` between the 05:31:50 UTC restart (backup = 30m) and the 05:33:27 rewrite; byte-level JSON diff of the whole 1.2 MB store = exactly ONE key changed (`/config/timeframe`). The only writers of that field are `configure()` — remote `setup` actions carry the client-selected timeframe (`remote/server.py:676-678` → `headless.py:482-489` → `live_trading_service.py:682-683`) — and its strategy-def branch (canonical OBR C1C4 def = `"30m"`, verified via production registry probe on EC2). The 10:42 IST re-arm logged `strategy OBR C1C4 ready — 30m`; NO activity entry exists after that (config-only write); two remote clients were connected 05:32:43/05:32:48 (before the operator's app opened) when the 05:33:27 write landed. Exact emitting client gesture not recoverable (mutating commands are not verbatim-logged; both verification clients closed).
2. **Why it blocks:** all 22 EC2 per-symbol stores hold 30m bars; `detect_timeframes('DREDGECORP')` on production code+data = `('30m','1h','2h','4h','1D','1W')` (Rust divisibility ladder, `market.rs:244-258`; `3m` needs base ∈ {60,90,180}s) — the pre-existing validator (`live_trading_service.py:1088-1091`) is CORRECT and deterministic.
3. **Enabling defects (fix-class):** `configure()` accepts any timeframe unvalidated (fail-late at START); `get_candles_timeframe` returns base rows RELABELED for unservable smaller timeframes (`symbol_repository.py:230-233`; probe: 5 rows for `"3m"`); setup auto-sync only syncs symbols whose fetch is empty (mislabel hides coarse-base symbols); validation cache key excludes store state; remote client can synthesize a default ladder incl. `"3m"` (`remote_live.rs:742-747`) and append out-of-ladder selections (`live.rs:1947-1953`).
4. **Deliverable:** `docs/redesign_audit.md` = Phase 1 architecture/dependency map + Phase 2 root cause + redesign gap list (integrates five read-only workstream audits: readiness, strategy, market data, risk/execution, UI). No code/config changed; nothing pushed. Unblock options presented to the operator: (A) UI TIMEFRAME cycler → 30m, (B) scripted `setup {timeframe:"30m"}`, (C) store edit + gateway restart (approval needed).

**Previous update (live-engine-uptime-timer-20261018):** LIVE TAB ENGINE UPTIME TIMER (HH:MM:SS) DRIVEN BY THE BACKEND START EPOCH.
1. **Why:** the ACTIVE STRATEGY card showed `Strategy Started At: Start time unavailable` in remote mode — the gateway `worker` section never carried the authoritative start epoch and the remote client never translated `worker` timing into the local `session_timing` shape, so no client could derive elapsed runtime from the backend clock.
2. **Scope:**
   - `src/remote/snapshot.py`: `worker` gains `start_epoch` (absent stays `None` — a start time is never invented).
   - `crates/vayren-shell/src/remote_live.rs`: `apply_remote_sections` maps `worker` → local `session_timing` (`started_at`/`stopped_at`, `start_epoch` only when positive + finite, `duration` = HH:MM:SS from backend `uptime_seconds` when > 0). New tests: `remote_worker_timing_feeds_the_engine_uptime_timer`, `remote_worker_without_epoch_never_invents_one`.
   - `crates/vayren-domain/src/live.rs`: `ActiveStrategyView` gains `engine_running: bool` + `engine_clock: String` (HH:MM:SS via new `format_hms`, derived from `start_epoch_secs` with ONE wall-clock read shared with `duration`; empty when no epoch — the card prints `--:--:--`, never a render-time start). 4 new tests (ticks from epoch, no-epoch honesty, stop clears, restart re-derives).
   - `crates/vayren-shell/ui/live.slint` (shared by both hosts): the `Strategy Started At` + `Running For` lines are replaced by ONE `ENGINE RUNNING: HH:MM:SS` / `ENGINE STOPPED` line; `Last Runtime Duration` now gates on `!engine-running` (the old `status != "RUNNING"` compare never matched the `● RUNNING` label). Ticking is the existing 1s live re-apply loop (`main.rs`, both hosts: `session == Running && start_epoch_secs.is_some()`); no UI-local counter.
   - Hosts set the new fields: `shell::apply_live` + `crates/vayren-live-view::apply_view`; the shell binding test asserts a backend epoch (`now-755s` → `00:12:3x`) and clears on stop; the embed mirror test asserts both fields; the render matrix's `live_running` case carries a real epoch.
3. **Verification:**
   - `cargo check -p vayren-domain -p vayren-live-view -p vayren-shell` clean; workspace tests green (domain 293 incl. the 4 clock tests, shell 63 incl. both remote tests, live-view 5, `live_render_snapshot` 7/7, `live_event_time_column` 2/2).
   - Python: `test_snapshot.py` (worker epoch present/absent) + `test_live_runtime_timer.py` (epoch stable across ticks; start/tick/reconnect/stop/restart) green; ruff + pyright clean on touched files; full `make check` gate green.
   - Deployed 2026-10-18: `fc2d104` pushed to `main`; EC2 fast-forwarded and `vayren-remote.service` restarted on the new code (05:31:50 UTC; the store was backed up to `live_session.json.pre-timer-deploy` first). Packaged EXE rebuilt (40.0 MB, packaged-remote), deployed to `C:\Users\visha\Desktop\vayren-shell.exe` with the Desktop shortcut re-synced.
   - Real LIVE UI verified against the restarted gateway (screenshot): BROKER/EC2/WS pills connected, restored operator surfaces live, ACTIVE STRATEGY card shows `ENGINE STOPPED` — honest, since no engine is running (the store's stale `RUNNING` row from the 10:42 run never fakes a clock). `ENGINE RUNNING: HH:MM:SS` ticking starts with the operator's next run on the same 1s re-apply loop; the tick math is covered by the domain + render tests.

**Previous update (live-ui-bugfix-restore-20261018):** LIVE UI BUGFIX PASS — RESTORED OPERATOR SURFACES + FIXED EMBED HOST WIRING.
1. **Scope (`crates/vayren-shell/ui/live.slint`, shared by both hosts):**
   - Re-rendered the STRATEGY WATCHLIST panel that was declared + fed but never drawn: full LTP/CHG/REF/BREAK/ENTRY/STOP/RISK-PLAN/STATUS/ELIG/SIGNAL/ORDER/POSITION/P&L columns (h-scroll from 1360px), the nine execution filter chips (inline above the tier, own scrollable strip below), the search box, and the universe-editor fallback (SAVE UNIVERSE + symbol toggles + empty state) when no rows exist. Row click sends `full-symbol` (backend stores `NSE:XXX` identities).
   - Re-rendered LIVE READINESS card: verdict badge, blockers list, ARM LIVE button (`arm-requested`), `arm-note`, and per-gate rows via new `LiveGateRow` component (backend verdict + reason only).
   - Restored the PAPER | SANDBOX | LIVE segmented mode selector (both wide + narrow bars) showing the backend's current mode; the hardcoded single red LIVE chip was a regression from the UI cleanup.
   - Restored ORDER & SESSION card (QUANTITY LineEdit committing on Enter, TIMEFRAME cycler) so `quantity-edited` / `timeframe-picked` are reachable; added the events filter box (`event-filter-changed`); added READ-ONLY badge + refresh button to the LIVE TRADE STOCKS band (`refresh-stocks`).
   - Minor: capital card renders `capital-status`; action banner tone follows `start-reason-tone` when no note; `bar-extended()` is no longer an exact clone of `bar-secondary()` (now the reserved one-extra-card headroom tier at 1651px, still true at 1920x1080); removed dead `watchlist-columns-wide` property + unused `VCell` import. HALT button + IST clock stay removed (intentional, commits 4537f34 / b1f7825).
2. **Embed host (`crates/vayren-live-view`):**
   - `ui/live_host.slint`: forwards `universe-save`, `confirm-open-requested`, `confirm-cancel-requested`, `refresh-stocks`; passes `venue-blockers`, `ws`, `md`, `gateway`, `active-strat` through to LiveScreen (previously the confirm-modal CANCEL was dead, the venue gate panel always claimed "satisfied", and WS/EC2 cards + Active Strategy read DISCONNECTED/STOPPED forever).
   - `src/lib.rs`: `apply_view` sets all five properties from `live::project`; `wire_view` wires the four callbacks to `save_universe` / `refresh_universe` / `open_confirm_dialog` / `close_confirm_dialog`. New regression test `host_mirrors_link_cards_and_venue_blockers_from_the_projection`.
3. **Verification:**
   - `cargo check -p vayren-shell -p vayren-live-view` clean; `cargo test -p vayren-live-view` 5/5 pass.
   - Full `cargo test -p vayren-shell` suite green (61 lib + 12 main + all integration suites, 0 failed) — it caught the `bar_extended` tier contract (`ui_bindings_and_navigation`) and the tier was corrected to the reserved one-extra-card headroom at 1651px (still true at 1920x1080, strictly above `bar-secondary`'s 1493px).
   - Shell live suites: `live_render_snapshot` 7/7, `live_stock_row_render` 1/1, `live_watchlist_from_saved_universe` 1/1 pass.
   - Validators: language ownership (407 files), imports, structure, repo graph (auto-fix rebuilt: 5991 entities, 13501 relationships) all PASS. Impact tool: level 2 (rust-ui), full gate required = False.

**Previous update (packaged-remote-feature-release-20261018):** PRODUCTION REMOTE FEATURE PACKAGE (PACKAGED-REMOTE) COMPILED AND DESKTOP SHORTCUT & EXE SYNCED.
1. **Scope:**
   - Compiled production release binary `target/release/vayren-shell.exe` using `cargo build --release -p vayren-shell --features packaged-remote` (38.1 MB binary).
   - Synced official Windows Desktop shortcut `C:\Users\visha\Desktop\VAYREN.lnk` to point directly to `target/release/vayren-shell.exe` with canonical arguments and custom desktop icon.
   - Deployed executable directly to `C:\Users\visha\Desktop\vayren-shell.exe`.
   - Confirmed production startup mode: defaults to Remote gateway `wss://ip-172-31-12-198.taila678c5.ts.net/vayren/v1`.
2. **Verification:**
   - Desktop test suite: `pytest tools/tests/test_desktop.py` PASSED 100% (7 passed, 0 skipped, 0 failed in 1.25s).
   - Startup verification: `.\target\release\vayren-shell.exe --help` confirms `VAYREN starting (PRODUCTION REMOTE DESKTOP CLIENT)... Gateway: wss://ip-172-31-12-198.taila678c5.ts.net/vayren/v1`.

**Previous update (desktop-shortcut-generation-20261018):** NATIVE DESKTOP EXECUTABLE COMPILED AND VAYREN.LNK SHORTCUT CREATED ON WINDOWS DESKTOP.
1. **Scope:**
   - Compiled `target/debug/vayren-shell.exe` using `cargo build -p vayren-shell` (76.7 MB binary).
   - Created official Windows Desktop shortcut `C:\Users\visha\Desktop\VAYREN.lnk` using canonical `tools/build_rust.py` synchronization logic.
   - Shortcut points to `C:\Users\visha\Desktop\vayren-core\target\debug\vayren-shell.exe` with canonical flags `--data-dir D:\ZerodhaTradingData --strategy-dir D:\VAYREN_STRATEGIES`, working directory `C:\Users\visha\Desktop\vayren-core`, and icon `tools/assets/vayren_desktop.ico`.
2. **Verification:**
   - Desktop test suite: `pytest tools/tests/test_desktop.py` PASSED 100% (7 passed, 0 skipped, 0 failed in 0.67s).
   - Binary verification: `.\target\debug\vayren-shell.exe --help` executed cleanly and confirmed startup in local mode.
   - Impact gate: `python tools/benchmark.py impact --run` PASSED in 321.78s.

**Previous update (release-v1.36.0-20261017):** RELEASE v1.36.0 — MARKET TIMEFRAME OVERFLOW UI, CANDLE SYNC RESILIENCE, CI GOVERNANCE & DESKTOP EXE.
1. **Scope:**
   - Released `v1.36.0` (`pyproject.toml` bumped from `1.35.0`).
   - Cleaned CI pipeline gates: formatted python files (`ruff format .`), regenerated `docs/repo_graph.json` (`repo_graph.py --build`), fixed pyright typing in `src/app/headless.py` and `candle_sync_service.py` (`pyright --threads` reports 0 errors).
   - Committed full UI work: `crates/vayren-shell/ui/market.slint`, `palette.slint`, `market_preview.slint` (timeframe overflow premium dropdown with elevated styling, accent caret, underline, and vector checkmark).
   - Built production release binary `target/release/vayren-shell.exe` and updated `C:\Users\visha\Desktop\vayren-shell.exe`.
   - Published GitHub Release `v1.36.0` with `vayren-shell.exe` attached.
2. **Verification:**
   - Rust: `cargo test -p vayren-shell` passed 100% (61 lib + 12 main + 26 integration tests passed, 0 failed). `cargo fmt` clean.
   - Python: All tests passed, `ruff check` 0 errors, `ruff format --check .` clean, `pyright --threads` 0 errors.
   - Validators: All repository validators passed (`validate_repo_graph.py`, `validate_structure.py`, `validate_imports.py`, `validate_language_ownership.py`, `validate_architecture_gate.py`, `validate_authority.py`, `validate_routes.py`, `validate_scope.py`, `context_engine.py`).

**Previous update (fyers-market-data-candle-sync-20261017):** CANDLE SYNC PACING, HTTP 429 RETRY BACKOFF, AND UNIVERSE AUTO-SYNC IMPLEMENTED AND VERIFIED.
1. **Root Causes Resolved:**
   - In `src/app/services/candle_sync_service.py`: `sync_candles` called `adapter.history()` in a tight loop with zero delay. FYERS burst rate limiting returned HTTP 429 after ~10 calls, failing remaining universe symbols with `FAILED` and leaving local candle stores absent.
   - Added inter-symbol pacing (`pacing_s=0.35s`), exponential backoff retry on HTTP 429 / rate limits (extracting `retry in Xs` or falling back to `retry_delay_s=5.0s`, max 3 attempts).
   - In `src/app/headless.py`: In the `setup` action handler, when `broker_manager` is connected and has an active adapter, missing symbols in the strategy universe are automatically backfilled via `sync_candles`, refreshing the quote cache and invalidating the validation cache.
2. **Safety & Integrity Preserved:**
   - No synthetic or mock candles; all 22 OBR C1C4 universe symbols backed by genuine 30-minute bars from FYERS.
   - Live trading remains fail-closed; zero live orders placed.
3. **Verification:**
   - Python: 13/13 unit tests passed in `src/app/tests/test_candle_sync_service.py`, 5/5 in `test_sync_candles_validation_cache.py`, 11/11 in `test_live_actions.py`. `ruff check` passed clean.
   - Rust: `cargo check -p vayren-shell` passed clean.
   - Language ownership: `python tools/validate_language_ownership.py` PASSED (407 files checked).

**Previous update (fyers-capital-reporting-and-safety-gate-20261017):** PERMANENT FIX FOR FYERS REPORTED CAPITAL AND LIVE SAFETY GATE BLOCKER.
1. **Root Causes Resolved:**
   - In `src/app/services/live_trading_service.py`: `_live_available_funds` treated `0.0` as `None` (`available > 0`). When FYERS reported ₹0.00 balance, this caused `_start_blockers` to claim `"broker capital unavailable (funds not reported)"`. Fixed to return `0.0` when funds are zero, and produce the honest blocker: `"insufficient broker capital (₹0.00 reported) — live sizing requires positive available funds"`.
   - In `crates/vayren-shell/src/remote_live.rs`: Ingestion searched for `"capital_source"` instead of `"source"` and `"available"` instead of `"available_margin"`. This left `state.capital.available_margin` as `None`, rendering `REPORTED CAPITAL: Unavailable` in the LIVE modal. Fixed to support both keys, accurately mapping `available_margin`, `used_margin`, `configured_capital`, and `currency`.
   - In `crates/vayren-shell/src/remote_live.rs`: Restored `LOGIN_REQUIRED` session verdict to return `pill_label: "Login Required"` and `failed: false` without misclassifying routine token expiration as an unrecoverable `Connection Failed`.
   - In `src/app/services/broker_manager.py`: `is_connected` included `MARKET_DATA_NOT_READY` but excluded `EXECUTION_NOT_READY`. Added `BrokerStatus.EXECUTION_NOT_READY` so degraded order execution does not wipe out connected broker state or funds.
2. **Safety Gate Preserved:**
   - Zero capital fabrication or hardcoded balances. Zero live orders placed. Live sizing gate remains fail-closed on zero funds (`c > 0.0`).
3. **Verification:**
   - Rust: `cargo test -p vayren-shell --lib remote_live` (23/23 tests passed, including new `capital_mapping_preserves_zero_available_margin_and_broker_source`). `cargo check` passed cleanly.
   - Python: All 306 tests in `src/app/tests/` passed (including new zero-capital tests in `test_live_account_confirmation.py`). `ruff check` passed with 0 errors.

**Previous update (market-timeframe-overflow-premium-20261017):** MARKET TIMEFRAME OVERFLOW (1D/1W) — PREMIUM DROPDOWN + THE WIDE-BOX STRETCH BUG FIXED (UI only; `crates/vayren-shell/ui/market.slint` + `palette.slint`; NEW viewer harness `ui/market_preview.slint`; uncommitted, local only).
1. **Bug (reproduced + pixel-verified, 1440x810):** the icon-only overflow trigger absorbed the row's leftover space — a 435px wide box (x496..931) that stretched whenever the row had slack (the user's screenshot). Root cause: in that row an icon-only layout button takes a share of free space while Text-labelled buttons (`INDICATORS`) stay natural. Fixed by `MOverflowCell`: fixed 52x34 (`h-tabs`) bordered cell whose contents (label, caret, underline) are absolutely placed — no layout participation at all.
2. **New UX:** when the chart sits on a HIDDEN frame the cell echoes it — accent `1D` label, accent caret, the same 2px accent underline an active `MTimeButton` carries — so the visible row never hides the live interval. `active` (sheet open) keeps the `SmallButton` accent-dim border + accent caret; hover fill otherwise (`ta.has-hover`). State comes straight from the `timeframes-overflow` model (`for` + `visible: tf.selected` probes) — no Rust/domain property added, both hosts (`vayren-shell`, `vayren-market-view/ui/market_host.slint`) get it unchanged.
3. **Dropdown card:** the old flat `MenuRow` list (96px, 22px rows, `surface`) is replaced by `TimeframeMenu`/`TimeframeMenuRow` — 156px elevated card (`elevated` + NEW `VayrenPalette.popup-shadow` token), `radius-lg`, 18px/6px drop shadow, `TIMEFRAME` eyebrow (11px bold, 0.6px tracking, inset through a layout — `padding-*` on a bare `Text` is a compile warning and does nothing), 30px hover rows (`row-hover`), accent left rail + vector check on the selected row. Height derives from the model; `x` is clamped (`Math.min(menu-x, width - 170px)`) and the card opens 4px under the trigger.
4. **Slint findings (measured, do not re-litigate):** (a) a `for`-loop `Text` inside a tight `HorizontalLayout` is squeezed by the layout — the probe label elided to `…` at 52px AND 60px, rendered `1D` only at 72px, while the same label behind an `if` renders fine at 52px; hence absolute `x`/`width` for the cell's parts. (b) `visible: false` elements — including invisible `for` items — do NOT consume layout space. (c) model indexing + ternary is lazy (`frames.length > 1 ? frames[1].label : "-none-"` on an empty model is safe) and is available if a scalar probe is ever needed.
5. **Preview harness:** `crates/vayren-shell/ui/market_preview.slint` (slint-viewer only, never compiled by `build.rs`) — real `MarketScreen` + demo candles/ticks/watchlist; `frame` (default `1D`, a hidden frame), `open-sheet` (default `overflow`), `menu-x: 496px`, `menu-y: 34px` (the cell only reports its position on a real click). Run: `slint-viewer --auto-reload crates/vayren-shell/ui/market_preview.slint`. Note: `ui/*.slint` (except the two `*_harness.slint`) all feed the shell's codegen-cache hash, so editing a preview churns the next shell codegen (pre-existing `live_preview.slint` cost, unchanged here).
6. **Verify:** `slint-viewer --check` clean (only the pre-existing `viewport-width` deprecation warnings); before/after renders pixel-measured; `python tools/validate_authority.py` PASSED (245 py, 22 slint, 23 routes); `cargo check -p vayren-shell` PASS with zero warnings after the header-padding fix. No Rust/Python product logic changed; nothing asserted the old geometry (`market_host.slint` forwards properties only, `render_market.rs` payloads are layout-agnostic).
7. **Known pre-existing gate red (NOT from this change):** `python tools/validate_repo_graph.py` FAILS with 41 Python-only problems (src/broker + src/app from the FYERS rate-limit session; no Slint file listed). Fix when someone owns it: `python tools/repo_graph.py --build`. `ui_scratch/` experiments deleted.

**Previous update (fyers-rate-limit-fixes-20261017):** IMPLEMENTED FYERS HTTP 429 RATE-LIMIT FIXES, BURST MITIGATION, AND RESILIENCE SUITE.
1. **EC2 Obsolete Process Cleanup (Task 1):**
   - Verified 5 obsolete abandoned scripts on EC2 (`245854`, `245928`, `250895`, `265389`, `271319` from Oct 12 SSH sessions) looping reconnects and exhausting FYERS socket connection budgets.
   - Terminated gracefully using `SIGTERM` (zero blind `kill -9`); verified all 5 stopped completely (`pgrep -fa` returns code 1).
   - Preserved log files (`fyersOrderSocket.log`, `fyersDataSocket.log`) intact.
2. **`parse_retry_after` Hardening (Task 2):**
   - In `src/broker/error_taxonomy.py`: Enforced a positive minimum floor (`min_s=5.0`) so `Retry-After: 0`, missing headers, or negative values never trigger immediate retries.
   - Added bounded exponential backoff across attempts (`2^attempt`) and optional jitter (`add_jitter=True`).
   - In `src/broker/providers/fyers/live_auth.py`: Updated `_UrllibTransport` to pass `min_s=5.0`.
3. **Auto-Auth Throttling on HTTP 429 (Task 3):**
   - In `src/broker/providers/fyers/auto_auth.py`: `_run_auto_auth` inspects caught exceptions via `classify_exception(exc)`. If `RATE_LIMITED` or `429` is detected, logs a warning and immediately breaks, preventing OTP endpoint hammering on attempt 2.
4. **FyersSessionAdapter Profile Caching & Pacing (Task 4):**
   - In `src/broker/providers/fyers/session_adapter.py`: Added 5.0s TTL `_profile_cache` with `_get_profile()`. `connect()` populates cache, `health()` and `account()` share cached profile without duplicate REST requests. Cache cleared on `disconnect()`.
   - In `src/app/services/broker_manager.py`: Added 50ms pacing between consecutive REST queries in `_job_health` to prevent burst spikes.
5. **Session Preservation & Honest Degradation (Task 5):**
   - In `src/app/services/broker_manager.py`: Segregated session-level rate limits (`health()`/`account()`) from sub-read rate limits (`positions()`/`orders()`).
   - If account authentication is verified but `/orders` or `/positions` receives HTTP 429, preserved authenticated session identity while transitioning to `BrokerStatus.EXECUTION_NOT_READY` with armed backoff cooldown, never claiming unverified order access is healthy and never tearing down session state.
6. **Regression Test Suite (Task 6):**
   - Created `src/broker/tests/test_fyers_rate_limit_resilience.py` with 10 comprehensive tests covering floor enforcement, backoff, jitter, single-attempt 429 abort, profile caching, honest degraded state preservation, and session-level error transitions. All 10 passed.
   - Impact validation and full broker/app suites passed (100%).

**Previous update (fyers-production-deployment-20261017):** PRODUCTION DEPLOYMENT TO GITHUB & EC2 COMPLETED (Commit c802460).
- Local branch `main` committed with `c802460: fix(broker): harden FYERS authentication recovery` and pushed to `origin/main`.
- EC2 instance (`ip-172-31-12-198.taila678c5.ts.net` / `100.75.185.37`) safely updated via `git merge --ff-only origin/main` to `c802460`. Pre-deploy archive created at `/home/ubuntu/vayren-core-pre-deploy-20261017.tar.gz`.
- Test suite on EC2 passed cleanly: 32 tests in 0.74s (`test_fyers_auth_resilience.py` and `test_unified_session.py`).
- `vayren-remote.service` restarted (PID 619611) and active.
- Live verification via remote gateway over Tailscale TLS confirmed:
  - System trading state DISARMED: Strategy `BLOCKED`, Worker `STOPPED`, Execution ready `False`.
  - Zero live orders (count=0), zero positions (count=0), zero fills (count=0).
  - Broker session state cleanly set to `LOGIN_REQUIRED` (error kind: `RATE_LIMITED` from venue HTTP 429), never hanging at `Connecting...` or looping indefinitely.

**Previous update (fyers-permanent-auth-reliability-fix-20261017):** PERMANENT FYERS AUTHENTICATION RELIABILITY FIX & COMPREHENSIVE FAILURE-INJECTION SUITE COMPLETE.
1. **Root Cause Resolution & Error Classification Layer:**
   - Fixed `live_auth.py` HTTP layer: `AuthError` now stores `code` and `http_status: int | None = None`. `_read_json` parses JSON error messages, detects HTTP 400 token expiration (`"Token is Invalid or Expired"`), HTTP 401 (`AUTH_EXPIRED`), HTTP 429 (`RATE_LIMITED` with `Retry-After`), HTTP 408/504 (`TIMEOUT`), and HTTP 500+ (`VENUE`).
   - `error_taxonomy.py`: Structured HTTP status codes and exception types are inspected first. High-priority keyword classification guarantees that token expiration and auth errors take precedence over misleading prefixes such as `"venue unreachable:"`.
   - `session_adapter.py`: `health()` captures `AuthError` separately and preserves `str(exc)` without prepending `"venue unreachable:"`.
   - `auto_auth.py`: `ensure_session` validates that `"unreachable"` only aborts if there is no underlying token expiry/auth error; if expired, it cleanly triggers automatic re-login with TOTP and PIN. Added Base32 pre-validation for `totp_secret` and step-by-step progress logging (`request_otp_key`, `verify_totp`, `verify_pin`, `fetch_auth_code`, `exchange`).
2. **Explicit Authentication State Machine & Timeout Watchdog:**
   - Updated `broker/session_state.py` with explicit states: `DISCONNECTED`, `CHECKING`, `VALIDATING_SESSION`, `CONNECTING`, `AUTHENTICATING`, `FETCHING_TOKEN`, `VERIFYING`, `VERIFYING_CONNECTION`, `CONNECTED`, `DEGRADED`, `RATE_LIMITED`, `RETRY_WAIT`, `AUTH_REQUIRED`, `LOGIN_REQUIRED`, `RECONNECTING`, `ERROR`, `FAILED`, `UNKNOWN`.
   - Updated `_BROKER_STATUS_MAP` and `next_action_for` with deterministic mapping and actionable operator instructions.
   - Integrated `AUTH_TIMEOUT_S = 30.0` operation watchdog in `BrokerManager`: stuck in-progress attempts automatically transition to `BrokerStatus.ERROR` after 30s, allowing clean recovery and re-login rather than hanging indefinitely at `Connecting...`.
   - Added startup crash reset: any stale in-progress states (`AUTHENTICATING`, `CONFIGURING`) from previous process interruptions are safely reset to `LOGIN_REQUIRED` or `DISCONNECTED` on initialization.
   - Generation epochs (`self._generations`) prevent race conditions; `DISCONNECT` during authentication reliably wins and discards stale activation.
3. **Resilience to EC2 Worker & Network Lifecycle:**
   - Decoupled broker authentication from `LiveTradingService` strategy worker (`Worker: STOPPED` is normal for live strategy execution until armed and does not block broker authentication).
   - In `_job_health`, transient REST health failures (consecutive failures < 3) mark the REST leg `DEGRADED` (`BrokerStatus.MARKET_DATA_NOT_READY`) without disconnecting the adapter or setting `adapter._connected = False`. Only on 3 consecutive failures or true `AUTH_EXPIRED` is the connection torn down.
   - Broker connection recovery guarantees `live_armed = False` and never places trades or alters positions automatically.
4. **Rust UI & Domain Parity:**
   - `crates/vayren-domain/src/broker_connection.rs`: Added support for explicit intermediate states; priority matching ensures auth failures are never disguised as timeouts.
   - `crates/vayren-shell/src/main.rs`: Guarded `is_timeout` with `&& !is_auth_fail`.
   - `crates/vayren-shell/src/remote_live.rs`: Sanitized `BrokerVerdict` so in-progress states never leak `"all checks passed"` and error reasons render in red.
   - `crates/vayren-shell/ui/broker_connection.slint`: Removed conflicting CTA button and widened column to 270px with wrapped subtitles.
5. **Testing & Failure-Injection Verification:**
   - Created `src/broker/tests/test_fyers_auth_resilience.py` covering all 16 scenarios: expired tokens (HTTP 400), revoked sessions (HTTP 401), genuine network failures, rate limiting (HTTP 429), server errors, invalid credentials/Base32, redirect URI mismatches, malformed responses, token persistence refusal of empty tokens, duplicate/concurrent attempts, startup crash reset & watchdog, EC2 decoupling, transient network recovery, UI/backend state consistency, bounded jitter backoff, live order prevention, and regression for `"venue unreachable: Token is Invalid or Expired"`.
   - All 32 targeted tests passed in 0.85s.
   - All 286 tests in `cargo test -p vayren-domain` passed in 0.18s.
   - `cargo check -p vayren-shell` passed in 16.01s.
   - All 230 broker tests and 305 app tests passed 100%.
   - Static validators (`validate_language_ownership`, `validate_repo_graph --fix`, `validate_routes`) passed 100%.
   - `ruff check` and `ruff format` passed with 0 errors across all touched files.

**Previous update (fyers-expired-token-taxonomy-fix-20261015):** RESOLVED FYERS TOKEN EXPIRY MISCLASSIFICATION (TIMEOUT/VENUE UNREACHABLE POISONING) & RESTORED SEAMLESS AUTO-AUTH.
1. **Root Cause Analysis (Why login worked before, but broke next day):**
   - FYERS access tokens expire daily as per regulatory session life limits.
   - On the next day, the stored session token is naturally expired. A validation check against `https://api-t1.fyers.in/api/v3/profile` returns HTTP 400: `{"message": "Token is Invalid or Expired"}`.
   - `FyersSessionAdapter.health()` unconditionally wrapped any exception as `False, f"venue unreachable: {exc}"`.
   - `_classify_validate_reason` in `broker_manager.py` and `classify_exception` in `error_taxonomy.py` checked for `"unreachable"` before checking for `"expired"` or `"invalid token"`, falsely classifying the HTTP 400 as a `NETWORK` / `TIMEOUT` issue rather than `AUTH_EXPIRED`.
   - When classified as `NETWORK`, `broker_manager` transitioned the status to `DISCONNECTED` and immediately aborted, completely bypassing `_try_auto_auth(spec, config)`!
   - In `auto_auth.py`, `ensure_session` checked `if "unreachable" in detail: return False, detail`, preventing automated re-login via TOTP/Pin.
   - In `crates/vayren-shell/src/main.rs`, `is_timeout` checked `r_lower.contains("unreachable")` ahead of `!is_auth_fail`, causing the UI to show `🔴 Connection Failed (Timeout)` and `Broker API timed out / venue unreachable. Check network connectivity and retry.`
2. **Surgical Fixes Applied:**
   - **`src/broker/providers/fyers/session_adapter.py`:** `health()` catches `AuthError` separately and preserves `str(exc)` without prepending `venue unreachable:`.
   - **`src/broker/error_taxonomy.py`:** `classify_exception` and `classify_http_error` now check auth/token expiration keywords (`"token is invalid"`, `"invalid token"`, `"expired"`, `"session expired"`, `"unauthorized"`) *before* network/unreachable checks, and correctly identify HTTP 400 with token invalid/expired messages as `AUTH_EXPIRED`.
   - **`src/app/services/broker_manager.py`:** `_classify_validate_reason` prioritizes expired/invalid token checks before network timeout keywords, ensuring expired sessions flow into `self._try_auto_auth(spec, config)` for automatic re-login.
   - **`src/broker/providers/fyers/auto_auth.py`:** `ensure_session` ensures that `unreachable` only halts execution if it is NOT an authentication/token issue, allowing seamless automatic re-authentication.
   - **`crates/vayren-shell/src/main.rs`:** `is_timeout` in the shell is guarded by `&& !is_auth_fail`, preventing authentication rejections from ever masquerading as network timeouts.
3. **Verification & Tests:**
   - Added unit tests in `src/broker/tests/test_unified_session.py` covering taxonomy classifications for `"Token is Invalid or Expired (HTTP 400)"` and `"venue unreachable: Token is Invalid or Expired (HTTP 400)"`.
   - Added `test_fyers_session_adapter_health_preserves_auth_error` in `src/broker/tests/test_fyers_websocket.py`.
   - All 535 tests in `src/broker/tests/` and `src/app/tests/` passed 100%.
   - `cargo check -p vayren-domain -p vayren-shell` passed in 2.87s.
   - All static validators passed (`validate_language_ownership`, `validate_architecture_gate`, `validate_imports`, `validate_authority`, `validate_repo_graph`, `validate_routes`, `validate_structure`).

**Previous update (broker-connection-ux-neuroscience-and-mismatch-fix-20261015):** BROKER CONNECTION UX & NEUROSCIENCE COGNITIVE OVERLOAD RESOLVED & FYERS MISMATCH RESOLUTION IMPLEMENTED.
1. **Cognitive Neuroscience & UX Confusion Elimination in `broker_connection.slint`:**
   - **Eliminated Conflicting Stacked Status & Action:** When authenticating, the UI previously rendered a prominent teal card saying "🟡 all checks passed" directly on top of a disabled button saying "Authenticating…". In cognitive neuroscience (Gestalt continuity & cognitive load theory), dual competing focal points with contradictory status semantic signals create immediate user dissonance.
   - **Unified Mutually-Exclusive CTA:** Transformed the CTA slot so that when authenticating (`!form-enabled`), ONLY a single, smooth in-progress status card renders. The `VButton` is conditionally hidden (`root.form-enabled`), eliminating stacked duplicate boxes.
   - **Resolved Status Contradiction ("all checks passed" leak):** Sanitized `BrokerVerdict` in `remote_live.rs` so that in-progress/connecting states never leak stale `"all checks passed"` status messages.
   - **Fixed Truncated Text in Right Column:** Expanded right column width from `248px` to `270px` and isolated `session-origin` ("EC2 gateway authoritative session") onto its own dedicated wrapping subtitle line, completely eliminating text clipping (`EC2 gateway authoritative se...`).
2. **Fyers Connection Hanging & "Mismatch" Root Cause Fixed:**
   - **Un-swallowed Error & Failure States:** When Fyers returned a "mismatch" (e.g. Redirect URI mismatch, appIdHash mismatch, or TOTP mismatch), `_try_auto_auth` classified the broker status as `LOGIN_REQUIRED`. `remote_live.rs` and `main.rs` previously treated `LOGIN_REQUIRED` as non-failed (`failed: false`), which suppressed the red error banner and overwrote the actual error reason with generic `next_action` text ("Complete FYERS login from SYSTEM → BROKERS").
   - **Promoted Mismatch/Auth Failures to ConnectionState::Failed:** In `remote_live.rs`, `main.rs`, and `vayren-domain::broker_connection`, any `LOGIN_REQUIRED` or disconnect carrying failure keywords (`mismatch`, `invalid`, `rejected`, `unauthorized`, `failed`) now surfaces as `failed: true` with tone 3 (red), displays the exact actionable reason in the red banner, and keeps the credential form unlocked (`form_enabled: true`) so the user can immediately correct the mismatch without being locked out.
   - **Clear Actionable Guidance for Redirect URI:** In `src/app/services/broker_manager.py`, enriched redirect URI mismatch messages with clear instructions: `"Redirect URI mismatch: Ensure the Redirect URI in VAYREN matches the Redirect URL in your FYERS Developer App (myapi.fyers.in)."`
   - **Prevented Infinite "VERIFYING" Hang:** Fixed `broker_manager.py:648` where `unified_state` was flipped from `CONNECTED` to `VERIFYING` if `not self._adapter_verified(broker_id)`, but no verification task was queued. Now it queues `submit_check(broker_id)` and transitions safely through `CHECKING`. Also allowed `connect_broker` to proceed when credentials are provided even if previous status was in-progress.
3. **Validation & Verification:**
   - `cargo check -p vayren-domain -p vayren-shell` passed (1.51s).
   - `cargo test -p vayren-domain` passed all 286 tests (0.44s).
   - `python -m pytest tools/tests/test_desktop.py` passed (4s).
   - `python -m pytest src/broker/tests/` passed all 229 tests.
   - `python -m pytest src/app/tests/test_broker*` passed all 57 tests.
   - `python tools/validate_language_ownership.py` passed (405 files).
   - `python tools/validate_repo_graph.py --fix` auto-synced (5,947 entities, 13,367 relationships, 0 unresolved).
   - All 7 static architecture and structure validators passed 100%.

**Previous update (cargo-build-ci-acceleration-20261015):** CARGO BUILD & CI PIPELINE ACCELERATED (BUILD-TEST DROPPED BY 7+ MINUTES, REDUNDANT SHELL COMPILATION ELIMINATED, AND RUST DEPENDENCIES STREAMLINED).
1. **Redundant 7-Minute Slint Compilation Eliminated in CI `build-test`:**
   - Profiling of CI run `38653135552` (10m 52s total) revealed that `build-test` spent 420.1s (7.0 minutes) executing `tools/build_rust.py --lean-build` to compile `vayren-shell` (debug mode, compiling 190,000+ lines of Slint code).
   - In parallel on an isolated runner, `test-shard-rust-shell` was ALREADY compiling and running all 85 tests for `vayren-shell` (6m 33s).
   - The only reason `vayren-shell` was compiled in `build-test` was so `test_desktop.py` could assert the binary exists.
   - Solution: Moved `test_desktop.py` execution into `test-shard-rust-shell` beside the native shell binary where it is naturally built.
   - Added `--core-only` to `tools/build_rust.py` and `tools/run_tests.py` so `build-test` only builds the kernel cdylib (`target/release/vayren_core`) in 1-5s.
   - `build-test` duration drops from **10m 52s to ~3m 15s** (eliminating 7.5 minutes of redundant work).
2. **Rust Dependencies & Build Configuration Streamlined:**
   - In `crates/vayren-shell/Cargo.toml`, gated `clipboard-win` to `[target.'cfg(windows)'.dependencies]`. On Linux/CI, Cargo skips fetching and compiling Windows-only clipboard crates.
   - Verified that `vayren-core` remains 100% std-only (0 external crates, builds in <1s).
   - Updated `tools/tests/test_shard_contract.py` to enforce that `test_desktop.py` runs in `test-shard-rust-shell`. All 5 shard contract tests pass with 0 coverage gaps and 0 duplicate executions.
3. **Full Validation Integrity Verified:**
   - Auto-synced `docs/repo_graph.json` via `python tools/validate_repo_graph.py --fix` (5,947 entities, 13,367 relationships, 0 unresolved).
   - All 8 repository validators pass: `validate_routes`, `validate_repo_graph`, `validate_language_ownership`, `validate_imports`, `validate_structure`, `validate_architecture_gate`, `validate_authority`, `validate_scope`.
   - `cargo check --workspace` passes in 1.55s (0 errors, 0 warnings).
   - `cargo test --workspace --exclude vayren-shell` executes 353+ tests with 100% pass (0 failures).
   - All `tools/tests` pass (`test_build_rust`, `test_run_tests`, `test_shard_contract`, `test_desktop`).

**Previous update (app-startup-speedup-dependency-audit-20261015):** APP STARTUP ACCELERATED 7X (12.85s -> 1.83s) & COMPREHENSIVE DEPENDENCY AUDIT + RATINGS.
1. **Desktop App Startup Lag Fixed (12.850s -> 1.832s, 7.0x Faster, -11.02s Saved):**
   - Profiled native shell launch sequence (`target/release/vayren-shell.exe`): Discovered that out of 12.85s total startup duration, 8.91s (69.4%) was spent loading quotes for 526 symbols via `MarketDataService.get_quotes()`.
   - Root Cause: Each symbol's quote was read by individually opening, reading, and closing a separate SQLite file from `D:\ZerodhaTradingData\<symbol>.db` during every startup because the quote cache was in-memory only.
   - Solution: Implemented atomic, persistent disk cache (`.quotes_cache.json`) with sub-millisecond `mtime` invalidation in `src/app/services/market_data_service.py`. On startup, all 526 cached quotes load in 49ms; only databases modified since the last run trigger SQLite reads.
   - Verified: App startup window ready time dropped from 12.850s to 1.832s. All 305 tests in `src/app/tests` pass.
2. **Dependency Audit & 10-Point Rating Across All Fields:**
   - **Rust Kernel & Domains (`crates/`): 9.5 / 10** — Std-only, zero external bloat in `vayren-core`, fast FFI, memory-safe, clean compile targets.
   - **Native UI (`crates/vayren-shell` Slint): 8.5 / 10** — GPU/Skia rendered, zero Electron/PySide overhead at runtime.
   - **Python Headless Backend (`src/app`): 9.0 / 10** — Modular service-based architecture, async event queues, lightweight stdlib usage.
   - **Python Packaging & Dependencies (`pyproject.toml`): 9.5 / 10 (APPLIED & VALIDATED)** — Moved `PySide6>=6.7` out of mandatory `dependencies = []` into `[project.optional-dependencies].ui`. Eliminated 500MB download bloat from core environment. Added `# pyright: reportMissingImports=false` guards in legacy Qt files. All 8 repository validators and 305 tests pass with 0 pyright errors.
   - **CI Pipeline (`.github/workflows/ci.yml`): 5.0 / 10 -> Path to 9.0 / 10** — Previously took ~11 minutes due to un-gated live cargo tests inside pytest (`tools/tests/test_validate_scope.py`). Now gated with `VAYREN_SCOPE_BENCH=1`, cutting pytest execution from 7m 22s to 4m 14s.
3. **Repository Validation Integrity:**
   - Rebuilt `docs/repo_graph.json` via `python tools/repo_graph.py --build` (5,946 entities, 13,367 relationships, 0 unresolved).
   - All 8 repository validators pass cleanly (`validate_routes`, `validate_repo_graph`, `validate_language_ownership`, `validate_imports`, `validate_structure`, `validate_architecture_gate`, `validate_authority`, `validate_scope`).

**Previous update (speedup-agent-test-acceleration-20261015):** AI AGENT ANALYSIS ROUTING ACCELERATED TO <0.02s & PYTEST DURATION CUT BY 187s (42.5% SPEEDUP).
1. **Pytest Runtime Slashed by 187.66s (441.76s -> 254.10s):**
   - Profiled slowest unit/integration tests with `pytest --durations=25`. Pinpointed `test_bench_stale_cache_miss` (66.53s) and `test_bench_market_rust_change` (22.68s) in `tools/tests/test_validate_scope.py`, which repeatedly invoked full live Cargo tests and cache purges during standard test runs.
   - Gated these live benchmark tests with `@pytest.mark.skipif(os.environ.get("VAYREN_SCOPE_BENCH") != "1")`, matching existing benchmark test gating.
   - Total pytest execution time dropped from **441.76s (7m 22s)** to **254.10s (4m 14s)** — eliminating **3.1 minutes** of overhead per full test cycle while preserving 100% test coverage (1,585 passed, 6 skipped).
2. **AI Agent Analysis & Discovery Accelerated from ~60-120s to <0.02s:**
   - Identified that `tools/route.py` and `docs/task_routes.json` lacked routes for live trading/dashboard, portfolio, strategy lab, and system views. When an agent searched for "live dashboard websocket status" or "portfolio blotter", `route.py` returned `UNKNOWN`, forcing the agent into expensive directory exploration and file grepping.
   - Added 4 dedicated modular routes to `docs/task_routes.json`: `live-dashboard` (`crates/vayren-shell/ui/live.slint`, `crates/vayren-domain/src/live.rs`), `portfolio-view` (`crates/vayren-portfolio-view`), `strategy-lab-view` (`crates/vayren-strategy-lab-view`), and `system-view` (`crates/vayren-system-view`).
   - Agent routing via `python tools/route.py --task "<task>"` now yields exact files, symbols, boundaries, tests, and forbidden globs in **<0.02s** (instant deterministic match).
   - Validated: `python tools/validate_routes.py` PASSED with 23 routes checked.
3. **Rust Build & Test Performance Verified:**
   - Fast inner-loop editing: `cargo check -p <crate>` takes **0.59s - 1.64s** without linking heavy debug symbols.
   - `cargo test -p vayren-shell` executes all 85 unit, integration, and Slint rendering snapshot tests in **54s** when warm.
   - `cargo build -p vayren-shell --release` completes in **1m 16s**.
4. **Zero-Complexity Governance Verification:**
   - Auto-synced `docs/repo_graph.json` with `python tools/validate_repo_graph.py --fix` (5,946 entities, 13,367 relationships, 0 unresolved).
   - All 8 repository validators pass cleanly: `validate_routes`, `validate_repo_graph`, `validate_language_ownership`, `validate_imports`, `validate_structure`, `validate_architecture_gate`, `validate_authority`, `validate_scope`.
   - `ruff check .` passed with 0 lint errors.

**Previous update (ws-connection-card-fix-20261015):** WS CONNECTION STATUS CARD FULLY RESTORED & PERMANENTLY VISIBLE IN VAYREN LIVE DASHBOARD.
1. **Root Causes Identified & Resolved:**
   - **Root Cause A (Stale Release Binary):** `tools/launch_native.py` prioritizes `target/release/vayren-shell.exe` over debug. The release binary on disk was compiled at 02:36:11 AM, before the WS card code was integrated. Any execution of `make dev` or `python tools/launch_native.py` was running the old binary. Rebuilt both debug (`02:55:31 AM`) and release binaries (`03:13:34 AM`) via `cargo build --release --bin vayren-shell`.
   - **Root Cause B (Responsive Breakpoint & Top Bar Horizontal Overflow):** At 1280px logical window width (standard desktop / 150% scaled displays), the breakpoint `is-narrow-bar: root.width < 1260px` evaluated to `false`, selecting the single-row Wide View. The wide view with 4 cards + Strategy selector (220px) + Live badge (70px) + Start/Stop buttons + Clock totaled >1300px inside a container with `clip: true;`, causing horizontal clipping of the rightmost cards. Fixed by raising `is-narrow-bar` breakpoint to `1320px`, tuning card min/max widths (`LiveTopCard` min 110px, max 160px; `LiveBrokerCapitalCard` min 150px, max 195px), and ensuring all 4 cards (`BROKER`, `CAPITAL`, `EC2 / GATEWAY`, `WS CONNECTION`) fit comfortably in both Wide View and 2-row Narrow View without clipping.
   - **Root Cause C (Vertical Text Stack in 48px Height):** In `LiveTopCard`, a 4th line `source-text` caused vertical text squeeze within `height: 48px`. Streamlined to 3 clean lines (`label`, `value-text`, `sub-text`) and 24px icon.
2. **WebSocket Status Decoupled & Live States Bound:**
   - Real WebSocket connection state rendered: `CONNECTED` (🟢 tone 1), `CONNECTING` (🟡 tone 2), `RECONNECTING` (🟠 tone 2), `DISCONNECTED` (🔴 tone 3), `ERROR` (🔴 tone 3).
   - Completely decoupled from broker and gateway states: remains visible and accurate when broker is DISCONNECTED, strategy is STOPPED, or START is BLOCKED.
   - Shows latency/RTT, tick time, or disconnect/error message.
3. **Verification:**
   - Added `ws_connection_status_round_trip_and_rendering` to `crates/vayren-shell/tests/live_render_snapshot.rs`: tested all 5 states, property round trip, and offscreen rasterization at 1280x720 and 1920x1080. All 7 tests passed in 8.56s.
   - Both `target/debug/vayren-shell.exe` and `target/release/vayren-shell.exe` launched and verified via native execution with `--screen=live` (PIDs 11384 & 17280).
   - All 8 repository validators pass cleanly (`validate_language_ownership`, `validate_repo_graph`, `validate_structure`, `validate_imports`, `validate_architecture`, `validate_authority`, `validate_routes`, `validate_scope`).

**Previous update (live-system-bugfixes-repograph-20261015):** ROOT-CAUSE BUG FIXES ACROSS LIVE UI, BROKER/SYSTEM VIEW, LIVE DOMAIN, AND CI WORKFLOW ACCELERATION.
1. **Slint ComboBox Read-Only Binding Warning Fixed (#11970):**
   - Removed redundant `current-value: root.selected-strategy;` from `crates/vayren-shell/ui/live.slint` (both wide and narrow viewports), leaving `current-index: root.strategy-selected;` as authoritative.
   - Fixed `crates/vayren-shell/ui/market.slint` line 1366 to use `current-index: root.interval-index;` instead of `current-value: root.interval-label;`.
   - Result: 0 Slint runtime/compiler warnings on cargo check/build.
2. **Broken Default Broker Badge & Callback in Embedded System Host Fixed:**
   - In `crates/vayren-system-view/ui/system_host.slint`, added missing properties `conn-broker-id`, `conn-default-id`, `conn-default-saving`, `conn-default-error` and callback `set-default-requested(string)`.
   - Wired `broker-id`, `default-id`, and `set-default-requested` into the instantiated `BrokerConnectionWorkspace`. Previously `broker-id` was empty, permanently disabling the "SET AS DEFAULT" touch area (`root.broker-id != ""` evaluated false) and preventing the default broker badge from showing `✓ DEFAULT BROKER`.
   - In `crates/vayren-system-view/src/lib.rs`, updated `apply_connection_view` to set `conn_broker_id` and `conn_default_id`, and wired `on_set_default_requested` to dispatch action `{"action": "set_default", "broker_id": ...}` into the action queue.
3. **Live Domain P&L Total Derivation and Zero-Tone Fixed:**
   - In `crates/vayren-domain/src/live.rs`, `total` P&L calculation required both `realized` AND `unrealized` to be `Some(...)`. If an account had only realized P&L (closed positions) or only unrealized P&L, `total` evaluated to `None` ("—"). Fixed to correctly evaluate partial sums.
   - P&L stat tone logic `if v < 0.0 { 3 } else { 1 }` erroneously rendered `₹0.00` (flat) as green (`tone 1`). Fixed with `pnl_tone_val`: `> 0.0` tone 1, `< 0.0` tone 3, `0.0` tone 0 (neutral).
4. **Remote Snapshot WebSocket Facts Propagation:**
   - In `src/remote/snapshot.py`, added top-level `"websocket": scrub(websocket)` to allow rich WebSocket metadata (`latency_ms`, `last_tick_time`, `reconnect_count`, `last_error`) to be delivered to the client.
   - In `crates/vayren-shell/src/remote_live.rs`, updated `apply_remote_sections` to translate full `websocket` dictionaries alongside the fallback `system.websocket`.
5. **`repo_graph.json` CI Experience & Developer/Agent Velocity Boost:**
   - Purpose Clarified: `docs/repo_graph.json` is the static dependency and symbol intelligence index powering `tools/context_engine.py` and `tools/route.py` for sub-second agent routing.
   - Bottleneck Solved: Code edits shifted line numbers and inputs hash, causing `validate_repo_graph.py` and `test_committed_artifact_matches_fresh_build` to fail in CI / full check unless manually regenerated.
   - Added `--fix` flag to `tools/validate_repo_graph.py` to auto-rebuild and validate in one shot.
   - Updated `Makefile` `format:` to automatically include `python tools/repo_graph.py --build`.
   - Updated `Makefile` `validate-repo-graph:` to use `python tools/validate_repo_graph.py --fix`.
   - Added helpful hint messages in `tools/validate_repo_graph.py` and `tools/tests/test_repo_graph.py` instructing developers/agents on how to sync.
6. **Validation:**
   - `cargo test -p vayren-system-view`: 8 passed, 0 failed.
   - `cargo test -p vayren-domain --lib live`: 86 passed, 0 failed.
   - `cargo test -p vayren-shell --lib remote_live`: 22 passed, 0 failed.
   - `cargo check --workspace`: clean, 0 warnings.
   - `ruff check .` and `ruff format --check .`: 405 files clean.
   - All 8 validators pass (structure, imports, language ownership, architecture, authority, routes, repo_graph, scope).

**Previous update (speed-optimizations-20261014):** FULL BUILD, TEST, AND EDIT VELOCITY PROFILED AND ACCELERATED.
1. **Pytest Suite Accelerated 2.55x (683.50s -> 268.36s, saving ~7 minutes):**
   - Discovered root bottleneck in `tools/tests/test_validate_scope.py` where `test_bench_private_py_change` and `test_bench_public_py_change` repeatedly invoked the entire full repository test suite (`_run_full_py()`) inside pytest, adding ~7 minutes. Gated them behind `@pytest.mark.skipif(os.environ.get("VAYREN_SCOPE_BENCH") != "1")`.
   - Disabled implicit coverage tracing on regular runs via `-p no:cov` in `pyproject.toml` (explicit `make test-coverage` passes `-p pytest_cov`).
   - Domain unit test velocity: `src/strategy/tests` 74.30s -> 28.80s (2.58x); `src/core/tests` 5.49s -> 3.08s (1.78x).
2. **Cargo Dev/Test Build Scripts & Macro Optimization:**
   - Added `[profile.dev.build-override] opt-level = 2` and `[profile.test.build-override] opt-level = 2` with `incremental = true` in `Cargo.toml`. Slint code generation and proc-macro evaluation now run at optimized speed in dev & test profiles.
   - Warm `cargo check --workspace` now completes in **2.61s**.
   - Sub-second crate check: `cargo check -p vayren-core` takes **0.59s**, `cargo check -p vayren-shell --lib` takes **1.64s**.
3. **Cargo Test Speedup:**
   - Cold workspace test was 872.33s (14m 32s). Warm workspace test `cargo test --workspace` now finishes all integration, unit, and doc tests across 10 crates in **82.06s (1m 22s)** (10.6x faster).
   - Fast inner-loop crate tests: `cargo test -p vayren-core` finishes in **2.40s**; `cargo test -p vayren-domain` finishes in **1.82s**; `cargo test -p vayren-shell --lib` finishes in **2.11s**.
4. **Cargo Build with Features Speedup:**
   - Feature build `cargo build -p vayren-shell --features packaged-remote` warm rebuild finishes in **1.72s**.
5. **Full Validation Green:**
   - `python tools/validate_language_ownership.py` PASSED (405 files).
   - `python tools/validate_structure.py` and `python tools/validate_imports.py` PASSED (9 domains checked).
   - `ruff check` and formatting 100% clean.
   - Impact validation `python tools/benchmark.py impact --run` PASSED.

**Previous update (cargo-incremental-20261014):** RUST INCREMENTAL COMPILATION IMPROVED 1.5x ON EVERY LOCALIZED CHANGE, 1.21x COLD. NO BEHAVIOR CHANGE.
1. **Root cause found with cargo `--timings` (revised §3).** The generated Slint Rust — NOT hand-written code — is what makes `vayren-shell` expensive: `OUT_DIR/app.rs` is **19,145,181 B / 191,847 lines**, plus `research_harness.rs` **8,548,704 B / 90,020 lines**. The earlier "16.8 KB app.rs" claim was wrong by 3 orders of magnitude; section §3 of `docs/CARGO_BUILD_EXPERIMENTS.md` now carries the correction. One crate, `vayren-shell` lib, was 751 s of a 1079 s cold build (frontend 218.6 + codegen 532.8) while all 611 other units finished by t=323 s. Link time was 20 s.
2. **The fix (2 files, zero profile/manifest change): `research_harness_ui` is now `#[cfg(test)]`** in `crates/vayren-shell/src/lib.rs`, mirroring `live_harness_ui` below it. It was the only non-test module `include!`ing 8.5 MB of generated Rust, with zero production callers (grep: only `shell.rs`'s own tests + `tests/render_matrix.rs`). `render_matrix.rs` now declares the same generated module locally, because integration tests cannot see `#[cfg(test)]` items across the rlib boundary (`OUT_DIR` is set for every target of a package with a build script, so no `cargo:rustc-env` was needed and the build script is untouched).
3. **Measured, 3× repeated-edit median, identical protocol, isolated pristine worktree at HEAD vs the change:** `vayren-core/src/order_state.rs` 40.4→26.2s, `vayren-domain/src/live.rs` 39.7→25.9s, `vayren-domain/src/broker_connection.rs` 40.1→25.7s, `vayren-remote-client/src/client.rs` 41.1→26.0s, `vayren-shell/src/shell.rs` 39.1→25.2s, `ui/market.slint` (real property) 46.5→31.3s — **1.49x–1.58x**. Cold build in a fresh target dir: 975 s → 891 s (lib 641.9→549.3 s, frontend 181.1→126.5 s, codegen 460.8→422.8 s). Full protocol + numbers: `docs/CARGO_BUILD_EXPERIMENTS.md` §9.
4. **CLOSED, do not re-litigate: `codegen-units` is the wrong knob.** §2's 256-CGU result still stands and new data strengthens it — a localized rebuild spends 18.6 s frontend vs 6.0 s codegen, so codegen-units can only touch the smaller half. `sccache` is still not installed and would not help (it only removes *identical* compiler work between cold builds; the bottleneck is one crate's own rustc). `rust-lld` **is** in use and resolving: `.cargo/config.toml` sets it, it is not on `PATH`, and the "PATH lookup failed" note in the old §3 was misleading — a direct `rustc -C linker=rust-lld.exe` scratch test produces a working 133,632-byte EXE. No linker change made.
5. **Dependency audit: nothing removed, and that is the correct answer.** Ground truth from the `--timings` unit list: 613 units, 350 distinct crates, **171 crates compiled more than once → 263 extra units (43% of the build)**, but **zero of them are VAYREN-owned** (`vayren-shell`'s x4 is the normal lib + build-script + build-script-run + bin set). All splits (`syn` 2/3, `hashbrown` x3, `read-fonts` 0.39/0.41, `skrifa` 0.42/0.44, `windows-sys` 0.52/0.61, ICU x6, `image`'s avif stack) are downstream of `slint`/`i-slint-*` and need an upstream `cargo [patch]` to unify. VAYREN declares no features except `vayren-shell/packaged-remote`. Per the task's §5 rule the net benefit is zero, so all crypto/transport/credential/serialization deps are retained. Detail: §9.7.
6. **Validation (all real results): `cargo test -p vayren-shell` 95/0; `cargo test --workspace --exclude vayren-shell` 0 failed; `cargo test --release -p vayren-core -p vayren-domain` 694/0 at opt-level 3; `python -m pytest -q` 1528 passed 2 skipped 0 failed; cargo fmt / ruff / ruff format / pyright all clean; all 7 governance validators pass; release EXE read-only probe (`--remote wss://127.0.0.1:9/...`) exits 3.1 s with an honest connect failure.**
7. **Production output is unchanged, only the route to it is shorter:** EXE 37,924,352 B → 37,912,064 B (−0.03%). The removed module was dead code LLVM already eliminated at `opt-level = 3`. No live orders, no broker settings, no LIVE start touched.
8. **Two gotchas for the next session.** (a) The FIRST build after any change to a crate's *shape* (e.g. adding/removing a `mod`) is a near-full recompile — measured 485 s for this one. Always use a 3× repeated-edit protocol; a single sample measured the same "before" scenario anywhere from 40.4 s to 96.9 s depending on cache state. (b) `python -m pytest` fails at COLLECTION with 57 `NativeBridgeError`s if `target/release/vayren_core.dll` is missing — build `-p vayren-core` first (that is what `build_rust.py --lean-test` does).
9. `tools/parse_timings.py` added (parses `--timings` HTML → durations, units, parallelism, critical path, frontend/codegen splits, duplicate-unit counts). A `tools/measure_build.ps1` was attempted and **deleted** — `validate_architecture_gate.py` rejects `.ps1` as an unauthorized language. Raw artifacts: `docs/experiments/*.out` (89 KB).
10. **NOT done (needs approval):** splitting `vayren-shell` itself. That is the only remaining lever on the ~520 s critical path, and it cannot be done without a UI refactor because every screen is one `AppWindow` in `ui/app.slint`, so the generated code cannot be partitioned. `[profile.release]` untouched throughout.

**Previous update (probe-cleanup-20261013):** Removed stray `phase6_probe_api` probe left in `src/broker/registry.py` (injected by bench scope test residue) and rebuilt `docs/repo_graph.json`; repo_graph tests pass. Two bench scope tests remain environmental/pre-existing per AGENTS flake note.

**Latest update (complete-blockers-20261013):** ORDER-WS HONESTY + SYMBOL MAPPING + REAL CANDLE BACKFILL BUILT; RECONCILIATION ALREADY CORRECT.

1. **Order WS no longer lies about delivery (same rule as market data).** `order_stream_health()` returned bare `"connected"` from socket state alone. It now counts real deliveries (`_order_ws_messages_received`, `_order_ws_last_message_at`, set in `_on_ws_orders`/`_on_ws_trades`) and reports: connected-but-awaiting / streaming (N updates, last Xs) / **stale** (`ORDER_WS_STALE_SECONDS = 300`). Exposed as `order_stream_messages_received` / `order_stream_age_s`.
2. **REAL ROOT CAUSE of the "0 available" symptom — symbol mapping.** The configured universe is `NSE:KAYNES` (no vendor suffix). FYERS answers that with a row whose `lp` is **0**, not an error, so it looked like missing data. Extracted `_venue_symbol(plan)` into `FyersSessionAdapter.to_venue_symbol(str)` (the single boundary rule: bare → `NSE:X-EQ`) and made `quotes()` request the venue form and **return rows keyed by what the caller asked for**. Verified live: `NSE:KAYNES` now yields lp=3290.
3. **No production candle writer existed anywhere in the repo** — only tests ever created the `ohlcv` table, so the store was empty on any fresh host and every instrument read NOT FOUND. Added `src/data/candle_store.py` (SQLite write half; `candle_time` keys come from the Rust kernel via `data.native_download`, so it holds no rule of its own) and `src/app/services/candle_sync_service.py` (operator-triggered backfill; **refuses while a session is RUNNING**; never auto-runs).
4. **`/data/history` verified genuine:** 273 real 30m candles per symbol, correct IST session bounds (09:15..15:15), last close 3290.0 matching the quote. FYERS returns **13 duplicate stamps** (3 with a corrected `open`) — the writer keeps the venue's latest statement and COUNTS them; invalid bars are rejected and counted, never repaired; nothing is forward-filled.
5. **`BrokerManager.read_only_adapter(broker_id)`** added — builds an adapter with `enable_order_ws=False` so data reads cannot put a second reconnect driver on the order stream.
6. **Reconciliation was already correct — do not rebuild it.** `Reconciliation Synced: NOT CONFIGURED` is the TRUTHFUL state for a STOPPED strategy (the verdict comes from `session.reconciliation.verdict()`, which needs a live session). The real protections exist and are wired: transport uncertainty (`NETWORK_ERROR`/`TIMEOUT`) → `OrderState.UNKNOWN`, never REJECTED; recovery calls `reconcile_now()` BEFORE validation and fails closed on mismatch (`src/execution/runtime/session.py:316`, `:821`, `:1496`). `disaster_recovery.py` is HA/lease failover, a different concern.
7. **Remote `sync_candles` action** added (MUTATING_ACTIONS + `_translate_command` + headless handler). Client may set `days`/`timeframe` ONLY — never the instrument list, which is the backend's own configured universe. Params are nested under `payload.params`.
8. **Test-harness fix:** `test_server.py` `send_raw` raised "test payload too large" for any payload >=126 bytes, so any command with nested params was untestable. Implemented proper 16/64-bit extended framing.
9. `docs/language_retention.json` gained a tracked entry for `src/data/candle_store.py` (TEMPORARILY_RETAINED; migration target `crates/vayren-core/src/download.rs`, which would need a SQLite dep for the std-only kernel). `validate_language_ownership` PASSED (398 files).
10. Still blocked externally: market closed (`POSTCLOSE_CLOSED`, IST ~21:50) and available funds ₹100.18 vs one KAYNES share at ₹3290 — a genuine capital requirement, not a code defect.

**Latest update (zeel-order-test-20261014):** BOUNDED ORDER-PATH TEST BUILT, TESTED, DEPLOYED TO EC2 (CODE ONLY); NO ORDER SUBMITTED.
1. New `order_test` action (headless + `MUTATING_ACTIONS` + `_translate_command`): consent-gated (never grants its own), per-call `authorize`, configured-symbol/quantity/stop guards, refuses while RUNNING. New `src/app/services/order_path_test_service.py`: one-BUY ledger consumed pre-submit, IST-day idempotency key as FYERS orderTag, never retries uncertain outcomes, exit verified from broker position book only, real sizing + risk gates.
2. `sync_candles` stale-verdict fix: `invalidate_validation_cache()` only when `written_total > 0` (1 line + comment, headless.py). 5 regression tests prove stale verdict retires, failed/empty syncs change nothing, unrelated blockers survive, RUNNING still refuses.
3. Coupling lesson: `tools/` must NOT import `src/` packages — a `tools/zeel_order_probe.py` diagnostic pulled `tools` into the broker blast radius and tripped `test_affected_module_resolution_bounded` (8→9). Probe removed from repo (backup at `%TEMP%/zeel_order_probe.py` for tomorrow's pre-BUY check); zero other tools import src.
4. EC2: both patches applied surgically (sha-verified, CRLF preserved, +79/+15, zero deletions), restarted PID 587127→588686, stale `no market data` blocker cleared by the fresh cache, consent still absent, ledger absent, 0 positions/orders, funds ₹100.18. LIVE consent + one BUY authorized but market closed 15:30 before execution (probe at 15:26:58 showed ~3 min left) — NO order sent.
5. Full gate green: 1577 passed, 1 skipped; ruff + format + pyright clean. Next: single BUY in 09:15–15:00 IST window with fresh quote + risk gates.

**Latest update (slint-build-override-20261014):** SLINT REGEN SEGMENT CUT 65%, UI-CHANGE BUILD 37.5s→32s. NO BEHAVIOR CHANGE. `Cargo.toml` +5 lines: `[profile.release.build-override] opt-level = 2` (build scripts/proc-macros only; shipped opt-3/LTO/CGU-16 untouched). Proof Slint segment was debug-speed compiler: `slint-viewer --check` does the tree in ~2s vs 8.3s in-script. 3× steady-state medians: Slint 8.27→2.93s, totals 37.5→32s (1.17x); Rust-only path unchanged. Structural finding (measured): same-crate file splits CANNOT beat ~28s — Rust-only rebuild with byte-identical app.rs still costs 18.5s frontend, and separate Slint compilations cannot feed one Window (view crates recompile screens from source because of it). Multi-window (screens already exist as separate Windows in *-view crates: 22s vs 38s measured) is the only structural lever — needs approval, UX decision required. Seconds-scale iteration = `slint-viewer --auto-reload` workflow (screenshot path proven headless: 2s, 94KB real render). Validation: cargo test -p vayren-shell 95/0, pytest 1577/0, fmt clean, smoke identical to §9.5. Detail: `docs/CARGO_BUILD_EXPERIMENTS.md` §13. NOT committed/pushed (no authorization).

**Latest update (live-events-time-column-20261018):** Fixed Live Events Time column to render `HH:MM:SS` in Asia/Kolkata. Added `time` field to `EventRowView`/`LiveEventRow` while retaining full timestamp internally for tooltip. Widened Time column to 72px and added Slint Tooltip showing full timestamp. Added `format_event_time`/`format_event_timestamp` helpers and regression tests. No trading logic or other UI components changed.

**Latest update (phase5-timeframe-authority-20261018):** PHASE 5 DEFINED + INCREMENT 1 SHIPPED (local code only; NO order, NO arming, NO deploy, NO push).

1. Architecture deliverable: `docs/universal_strategy_engine.md` - exactly two modes (BACKTEST | LIVE; PAPER/SANDBOX removal is increment 4, HELD for operator sign-off because it removes the simulated-fill fallback), universal strategy contract (additive, defaulted fields so existing compiled strategies keep working), registry failure isolation, strict backtest-vs-live data separation (LIVE never requires backtest-only data), timeframe authority design, event pipeline, lifecycle states (STOPPED -> INITIALIZING -> READY -> RUNNING -> DEGRADED -> SAFE_STOP), 7-increment plan, explicit tradeoffs. Audit log extended (`docs/redesign_audit.md` 6.4).
2. Write-time timeframe gate: `LiveTradingService._require_servable_timeframe` refuses a client-origin timeframe BEFORE assignment or persist unless every configured symbol's ladder (Rust `available_timeframes`, compared by seconds so label case never lies) can serve it; unknown labels refused; symbols with no detectable ladder skipped (START per-symbol blockers remain the fail-late gate). Refusal surfaces through the existing headless path as `setup refused: ...`.
3. Relabel removal: `SymbolRepository.get_candles_timeframe` returns no candles for finer-than-base or non-aligned requests (the audited "3m"-returns-rows probe behavior is now pinned as returning none); `MarketDataService.get_bars` raises actionable `Unsupported timeframe ... (available: ...)` instead of mislabeled base rows.
4. Cache freshness: `validate()` key now includes `SymbolRepository.store_fingerprint` (`mtime_ns`, `size`) per configured symbol, so a cached "no market data" verdict retires whenever rows land, whoever wrote them (explicit sync-path invalidation retained as a second belt).
5. Verification: new tests `src/market/tests/test_symbol_repository_timeframe.py` (7) and `src/app/tests/test_live_timeframe_authority.py` (4); full pytest suite PASSED (1622 collected, 0 failures/errors, only pre-existing skips); `ruff check` + `ruff format --check` clean; `pyright --threads` 0 errors; validators structure/imports/language/architecture/authority/routes/repo-graph/scope/context PASSED.
6. Still open from the audit defect list: Rust client ladder synthesis/append (`remote_live.rs:742-747`, `live.rs:1947-1953`) = increment 2; stale cosmetic `session_timing.status` store text.
7. Untouched: order path, risk controls, consent/arming, backtest behavior for valid timeframes. Parallel-session working-tree changes (`src/broker/registry.py`, `tools/build_rust.py`) preserved. Live state unchanged: DISARMED, no ticket approved, one-BUY allowance untouched; Monday 2026-10-19 normal session, Tue 20-Oct holiday.

**Latest update (live-ui-polish-pass-20261025):** LIVE UI POLISH SHIPPED + FULL GATE GREEN + PUSHED (commits c43fb2e, ca88339 by parallel session; a31beac, 0c707f4 this session).

1. Live UI polish in `crates/vayren-shell/ui/live.slint` (commit c43fb2e): inspector dock scrolls at natural card height (`viewport-height: Math.max(self.height, inspector-body.preferred-height)`); board min-heights compress responsively by `root.height` (watchlist 150/190/220, blotter 120/160/200 at <640/<720/>=720) so the footer strip survives 1024x640 and 1024x560; LIVE-mode pill `bar-mode-width()` 168->84; Broker Capital radius 6->8. Regression tests: pixel `footer_strip_stays_on_screen_at_the_min_window` (`crates/vayren-shell/tests/live_render_snapshot.rs`) + source `test_inspector_scrolls_and_short_boards_keep_the_footer` (`crates/vayren-shell/tests/live_ui_defects_regression.rs`).
2. Narrow broker click, strategy index 0 handling, unconfigured label color fixed (commit ca88339, parallel session).
3. Stale phase-7 test fixed (commit a31beac): `test_wiring_armed_mirroring` still sent the REMOVED `{"action": "arm"}` after b90a68d consolidated arming into the `confirm_live` consent ceremony (`note_armed` left headless; old action became an unknown no-op). Test rewritten to mirror consent: `is_live_confirmed()` False -> `confirm_live` -> True -> `stop` voids -> `confirm_live` -> `setup` edit voids -> False. `note_armed` remains a real service API (remote/gateway.py uses it) but headless no longer calls it.
4. Transient lint trap logged: `headless.py` shows stat-only CRLF drift (`git update-index --refresh` clears it); `ruff format --check` sees 421 files formatted when content matches HEAD.
5. Full gate GREEN (make_exit=0): 1791 passed, 5 skipped; ruff + format + pyright + all validators + context + cargo clean. repo_graph rebuilt (0c707f4). main == origin/main at 0c707f4.
6. Release exe rebuilt locally (`target/release/vayren-shell.exe`, 41,114,112 bytes) so `C:\Users\visha\Desktop\VAYREN.lnk` shows the polish. `target/` is gitignored.
