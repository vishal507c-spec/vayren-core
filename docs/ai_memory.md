# AI Memory — Current State Only (compact, active path)

> This file holds only current state + open items. Append new state here;
> prune superseded entries to docs/archive/ai_memory_history.md.

**Owns:** Current state, open items, verified facts, oddities.
**Historical archive:** docs/archive/ai_memory_history.md (all past session logs preserved).
**Build-performance history:** docs/CARGO_BUILD_EXPERIMENTS.md.

**Latest update (fyers-rate-limit-fixes-20261017):** IMPLEMENTED FYERS HTTP 429 RATE-LIMIT FIXES, BURST MITIGATION, AND RESILIENCE SUITE.
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

