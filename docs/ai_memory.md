# AI Memory — Current State Only (compact, active path)

> This file holds only current state + open items. Append new state here;
> prune superseded entries to docs/archive/ai_memory_history.md.

**Owns:** Current state, open items, verified facts, oddities.
**Historical archive:** docs/archive/ai_memory_history.md (all past session logs preserved).
**Build-performance history:** docs/CARGO_BUILD_EXPERIMENTS.md.

**Latest update (probe-cleanup-20261013):** Removed stray `phase6_probe_api` probe left in `src/broker/registry.py` (injected by bench scope test residue) and rebuilt `docs/repo_graph.json`; repo_graph tests pass. Two bench scope tests remain environmental/pre-existing per AGENTS flake note.

**Latest update (manage-connection-inert-20261013):** `Manage Connection →` BUTTON WAS INERT — ROOT CAUSE WAS A STALE LOCAL REPAINT, NOT A MISSING HANDLER.
Root cause (traced in the executed EXE path): the click handler existed and ran all along (`submit()` → `connect-requested`/`retry-requested` → host flow → gateway command); no overlay/modal/hit-test issue (`app.slint:857` renders the workspace directly when `!screen-pending`). The button looked dead because the CTA label came from the LOCAL workspace paint while its ENABLED flag came from the EC2 verdict: local FYERS verified this PC's vault token → `cta_label="Manage Connection"` (the CONNECTED-only label, a button that cannot act), while the EC2 authority said LOGIN_REQUIRED → button rendered but any click could only be answered with a refusal, so it "opened nothing".
Two compounding defects: (1) `main.rs` System fetch arm repainted the panel from the LOCAL workspace on every poll, overwriting the authoritative paint; (2) `apply_authoritative_broker_ui` never set the full field set, so `cta_label`/`cta_arrow`/`env_label`/`disconnect_*`/`display_name` survived from the last local paint.
Fix: `local_system_paints_panel(has_gateway_link)` — a local answer may only refresh the view cache, never paint, when a gateway owns the session; the authoritative painter now writes the WHOLE set incl. `cta_label(state, name)`, `cta_enabled = !in_progress` (DISCONNECTED/LOGIN_REQUIRED stay actionable), `disconnect_visible = connected`, `env_label = "EC2 LIVE"` (no stale PAPER), display name/mark/venue subtitle/help caption from the EC2 row.
Tests: 5 new in main.rs (stale local "Manage Connection" is replaced by "Connect to Fyers" + enabled; DISCONNECTED offers enabled connect; VERIFYING disables CTA; local snapshot never paints with a gateway link; form bound to EC2 schema keys). bin tests 12 pass, lib 60 pass, `cargo check --all-targets` exit 0, fmt clean, graph PASS.
EXE: rebuilt packaged-remote 19:43 (38,223,360 B, B3277284…); `EC2 LIVE`/`Connect to `/`EC2 gateway` present in live paths. Desktop shortcut target IS the new binary. NOTE: `Desktop\vayren-shell.exe` is a SEPARATE copy locked by two RUNNING old-build instances (PID 7000, 10336 started 19:35) — user must close them before that copy can be refreshed; shortcut launch is already current. Click-level verification still needs the user: not yet verified in the real UI.
No Python change this round → no EC2 deploy/restart (service untouched, PID 548991).

**Previous update (refresh-capital-command-20261013):** `refresh_capital refused — UNKNOWN_COMMAND` FIXED END-TO-END; CAPITAL STILL (CORRECTLY) UNAVAILABLE UNTIL REAL FYERS AUTH.
Root cause: the LIVE capital-refresh button emitted a valid `refresh_capital` action (vayren-domain `LiveState::refresh_broker_capital`), the host flow forwarded it to the gateway correctly, but the gateway NEVER had it: `refresh_capital` was missing from `server.MUTATING_ACTIONS` and from `_translate_command`, so `_on_command` refused it before the backend. A pre-existing half-path: `_apply_broker_command` treated `refresh_capital` as a broker command reading `manager.get_session_snapshot`, but nothing could ever route to it — the only entry point is `_translate_command`. UI was correct; server was incomplete.
Fix (Python only, 2 files): allowlisted + translated `refresh_capital` (no broker id invented — backend resolves the venue from the authoritative selection); `_command_result` honours `action_ok` so a refused read answers ok=False. headless `refresh_capital` handler now (a) isolates per-broker `refresh_funds` failures with contextlib.suppress instead of aborting the whole action, (b) `_await_broker_settled` only when the manager really has `_specs` (MagicMock stubs have none), (c) answers the REAL outcome: funds present → "broker capital refreshed"; absent → "broker funds unavailable (<status>) — no verified broker response"; no venue → "no broker configured — nothing to refresh". No funds are ever invented.
Tests: 5 new (2 socket-level in test_server.py incl. a scripted backend refusal asserting ok=False + reason in detail; 3 in test_live_broker_capital.py: verified-refresh note, unavailable note, no-broker note). remote+app+broker 546 pass; ruff check/format clean (347 files); pyright 0; imports/structure/authority/graph PASS.
Deploy: backup `capital-cmd-20261013-135534`, scp 2 files hash-matched, on-host 22 pass, SSH restart PID→548991 active/0-restarts/0-errors. On-host REAL transport verification (fresh server + trade token, real WebSocket frames): welcome role=trade → command_result ok=True detail="broker funds unavailable (LOGIN REQUIRED) — no verified broker response", capital {available:null, source:configured, status:LOGIN REQUIRED}, duplicate request_id → cached=True (no double execute).
NO EXE REBUILD: no Rust source changed since the 19:14 build (A81A1C4A…) — the client already emits and forwards the action; only the server lacked it. Verified no `.rs`/`.slint` newer than the EXE.
Truth: Available Capital stays Unavailable because EC2 FYERS is still LOGIN_REQUIRED (no stored token). The command now works and reports honestly; funds appear only after real FYERS auth on EC2. LIVE not started, no orders.

**Previous update (real-root-cause-host-flow-20261013):** THE PREVIOUS FIX WENT INTO DEAD CODE — REDONE IN THE EXECUTED FLOW.
1. Proof the first fix missed: `run_remote_ui` (main.rs:2601) has ZERO callers — main() always runs the host flow (local backend always spawned) plus remote sections when a gateway URL resolves. Evidence in binary: live-code strings from the dead path absent (`EC2 gateway`:0), host-path strings present. So SYSTEM CONNECT still hit the LOCAL manager (gen-15 local intent file @16:24 proves repeated local CONNECTs) while EC2 was never contacted (no intent file, no service logs, stored token still expired on revalidation).
2. Correction to yesterday's probe claims: `shared_manager()` is per-PROCESS singleton, so my in-process "version 0 / desired DISCONNECTED / empty audit" readings described my probe process, NOT the service. Conclusions stand on stronger evidence (no intent file + no logs + token revalidation), but the probe readings themselves are retracted as service-state claims.
3. Fix (host flow = the only flow): CONNECT/RETRY/DISCONNECT route to EC2 via RemoteCommand when `remote_command_tx` is Some (same pattern as SET AS DEFAULT), fail-closed with notes otherwise; local path byte-identical when no gateway. Host CommandAck arm paints broker failures. Origin label conditional on gateway presence (EC2 vs This PC). run_remote_ui dead code left untouched (still compiles).
4. Regression test: `system_and_live_agree_on_the_reported_mismatch_shape` (legacy connected + session LOGIN_REQUIRED → both refuse + agree). Origin label: `conn-session-origin` prop through app.slint → workspace; set once per launch path.
5. Validation: cargo check --all-targets exit 0, shell-lib 60 pass, fmt clean, authority/structure/graph PASS, python untouched (no rerun needed). EXE rebuilt packaged-remote 19:14 (38,202,368 B, A81A1C4A…): `EC2 gateway`/`gateway link closed`/`connect_broker`×6 all present IN LIVE CODE paths this time; Desktop copy synced to same hash. No EC2 deploy/restart (no server changes); service still PID 547489, 0 restarts, 0 errors.
6. STILL NEEDS USER: launch the synced Desktop EXE (label must read "EC2 gateway"), SYSTEM must show Login Required (agreeing with LIVE), then one CONNECT heals EC2. Local D:\ intent (CONNECTED gen 15) is user runtime state — left alone.

**Latest update (ec2-owned-connect-fix-20261013):** SYSTEM CONNECT NOW AUTHENTICATES THE EC2 SESSION; SYSTEM≡LIVE BY CONSTRUCTION.
Root causes (all proven in code, then fixed):
1. Remote-mode CONNECT/RETRY/DISCONNECT were unwired no-ops — the packaged EXE's SYSTEM buttons could only ever verify a Windows-local backend session, never EC2. Gateway already accepted `connect_broker`/`disconnect_broker` (idempotent, request_id); the client never sent them.
2. `broker_session` (authoritative) rode ONLY command-result snapshots, never the streaming path (`get_snapshot` didn't inject it) — the UI could not show unified state except right after a command.
3. SYSTEM (`sync_broker_ui`) read only legacy `broker.connected` while LIVE treated an "expired" reason as failure — same snapshot painted opposite verdicts; plus the connected arm masked auth-failure reasons (1-line order fix: `&& !is_auth_fail`).
Fix: gateway injects `broker_session` into every snapshot; scrub keeps bool `secret` render flags (string secrets still dropped, both existing tests unchanged); Rust `authoritative_broker()` + `session_verdict()` shared by SYSTEM painter and LIVE translator (top-level session → row session → legacy fallback); remote CONNECT/RETRY/DISCONNECT wired via authenticated RemoteCommands (sentinel-stripped, role/link-gated, optimistic activity paint only, resync repaints truth); form rebuilt from venue `credential_schema` (values never cross); restart-failure message now says SSH instead of echoing sudo stderr (no privilege change — NoNewPrivileges sandbox is the design).
Validation: python remote+unified+transitions 106 pass, app+broker 458 pass, ruff/pyright clean, all governance PASS (repo_graph rebuilt twice — once raced with cargo fmt); Rust domain 286, shell-lib 59 (6 new), `cargo check --all-targets` exit 0, `cargo test -p vayren-shell` exit 0, cargo fmt clean. EXE rebuilt packaged-remote 17:34 (38,177,280 B, ECC5B23A…; marker+command paths+verdict fragments verified — short literals live as mov-immediates under opt-level=3, not contiguous rodata, so naive byte-count misses them; debug binary + lib tests prove the code).
Deploy: 6 python files to EC2 (hash-match), backup `ec2-session-fix-20261013-115534`, on-host 69 pass, SSH restart PID→547489 active/0-restarts/0-errors. Streaming snapshot verified: broker_session LOGIN_REQUIRED, rows carry session+schema keys, STOPPED + blockers, live_armed=false, no orders. Venue blocker clears on `_activate` after real auth (EC2 spec already has real `register_fyers_live_instance`).
Sudo answer: gateway-side `sudo systemctl restart` can NEVER work (NoNewPrivileges=true sandbox); SSH passwordless sudo is the intact authorized mechanism (used for both restarts). Nothing weakened.
BLOCKER (single, needs user): run the NEW EXE → SYSTEM → BROKERS must now show "Login Required" (same as LIVE) → CONNECT with FYERS creds → EC2 heals → both show Connected. No orders/LIVE start by me.

**Latest update (first-principles-code-elimination, 2026-10-13):** AUDIT + REMOVE ~6.3k LINES, ZERO REGRESSION.
1. Removed (grep-proven zero callers outside own tests, no gate/docs/CI-command use):
   - `tools/speed/` (9 files) + `test_speed_loop.py` + `test_speed_metrics.py` — manual-only instrumentation.
   - `tools/self_opt.py` (1,308 lines) + test — only consumer of `failure_intel`; nothing else imports it.
   - `tools/vayren_intel.py` (1,384 lines) + test — tests-only imports; archive records its freshness time-bombs failing CI.
   - `tools/crop_render.py`, `tools/ppm_to_png.py`, `tools/assets/*.py` (one-shot render/icon utils; .ico products kept) + `pillow` dev dep (only they used it).
   - `src/data/throttle.py` (zero-importer shim; canonical `broker/common/throttle.py` untouched), `control_plane.redact_token` (zero callers), inline Retry-After parse folded into tested `parse_retry_after` (+ new 429 regression test).
   - Accompanying edits: CI pure-shard list (4 names), `pyproject` extraPaths, `validate_structure` data list, `download-config` route (dropped deleted file + stale alias), ledger entry for deleted shim, rebuilt `repo_graph.json`.
2. Kept with reason: all 8 validators (distinct policies), `failure_intel` (unique recovery job, CI-pinned, direct CLI), `benchmark` (`make fast`), `forensics` (mandated workflow), graph/index/route/packet/surface/engine chain (gate-transitive), build/release/launch drivers. No merge/simplify warranted (redact/scrub/backoff pairs have distinct semantics).
3. Verification: full `pytest` 1474 passed + 2 skipped (12:42); `cargo test --workspace --exclude vayren-shell` all ok; `build_rust.py --package` release `packaged-remote` shell built (38,109,696 B, same size as prior build — no Rust changes) + shortcut synced; ruff/pyright/validators clean.
4. Residual risks (pre-existing): pytest workers can linger after suite completion (killed 2 stale trees, one blocking the DLL link — kill stale `pytest` before release builds); `cargo test -p vayren-shell` Slint suites covered by CI shard, not run locally. Note: `src/broker/registry.py` trailing probe comment + EC2 deploy in prior entry are the other party's concurrent work, left untouched.
5. Postscript fix (regression I introduced, now fixed): `build_rust.py --package` created an arg-less desktop shortcut on this machine (none existed → smoke test went from skip to fail). `_sync_desktop_shortcut` now defaults fresh shortcuts to the canonical `launch_native` data/strategy dirs (14-line diff); `test_desktop.py` 7/7 green.

**Previous update (unified-session-deployed-ec2-20261013):** UNIFIED FYERS SESSION DEPLOYED TO EC2, GATEWAY VERIFIED LIVE.
1. Local: working tree = HEAD 4387d79 + WIP (new `src/broker/session_state.py`, `error_taxonomy.py`, `audit.py`, `tests/test_unified_session.py`; rewritten `app/services/broker_manager.py`, `app/headless.py`, `remote/gateway.py`, `server.py`, `snapshot.py` (+broker_session, server-only sections preserved), `fyers/live_auth.py`; `vayren-domain/broker_connection.rs` unified-state mapping). Path corrections vs plan: manager=`src/app/services/broker_manager.py`, headless=`src/app/headless.py`, rust=`crates/vayren-domain/...` (`vayren-shell/src/broker_connection.rs` does not exist). Unrelated local deletions (tools/speed, tools/vayren_intel, data/throttle shim) left untouched; EC2 keeps its throttle shim.
2. EXE current, no rebuild: `target/release/vayren-shell.exe` 2026-10-13 15:34, 38,109,696 B, SHA256 999CADE9...; newer than every Rust source; binary contains packaged-remote marker (tailscale default URL) + new `RATE_LIMITED`/`CHECKING SESSION` arms (absent at HEAD).
3. Validation (current checkout): unified 17/17; on-host EC2 40/40 (unified+fyers-live-auth+status-transitions); `pytest src` 1071 passed; root suite 1473 passed + 1 skipped, 2 failed in 967s — BOTH proven environmental, pass in isolation: (a) `test_desktop_shortcut_launches_rust_directly` (external actor rewrote `VAYREN.lnk` mid-run at 16:09; 7/7 pass alone; test files git-clean, shortcut untouched by me); (b) `test_bench_private_py_change` (cache-hits bench sensitive to 16-min loaded run; passes alone in 121s). Neither file is in my diff. Ruff check+format clean (347 files); pyright 0 errors; imports/structure/language/architecture/routes/authority/scope validators PASS; repo_graph rebuilt (was stale) now PASS; `cargo test -p vayren-domain` 286 passed; `cargo test -p vayren-shell --lib` 53 passed; `cargo check -p vayren-shell` exit 0.
4. EC2 deploy: host `vayren`, checkout was clean 4537f34 (ancestor of local HEAD) + 2 untracked debug files left alone; disk 90% (2.9G free, fine). Backup `/home/ubuntu/vayren-backups/unified-session-20261013-102840/` (8 files + revision/service-unit/selection-ref). Deployed 12 files via scp, all SHA256 match local. No Cargo/Rust on EC2 (Python runner only). Untracked EC2 debug files + all secrets/data/strategies preserved.
5. Service: `sudo systemctl restart vayren-remote.service` PID 271890→545983, active, NRestarts=0, loopback 127.0.0.1:8765 held by new PID, startup clean ("backend idle; trading starts only via client command"), 24h log has only routine connect/idle noise, 0 errors. NOTE 10:13 UTC audit entries show an unknown trade-role client attempting backup/restart (failed, no privilege) — concurrent operator activity, service unaffected.
6. Live gateway proof: empty-token probe → `AUTH_FAILED`, empty snapshot; in-process `get_session_snapshot("fyers")` → state LOGIN_REQUIRED, desired DISCONNECTED, configured=true, token_reused=false, legs rest/market_ws/order_ws independent (UNKNOWN), funds null, live_armed=false, strategy_running=false, next_action="Complete FYERS login from SYSTEM → BROKERS". Snapshot secret-free by field inspection. server.py default path /vayren/v1 confirmed.
7. LIVE safety: no consent restored, no strategy started, no orders (engine orders [] / orders_today 0, persisted LIVE config account_confirmed=false). No production DISCONNECT/CONNECT cycle run (would be pointless: token already invalid).
8. BLOCKER (single, needs user): real FYERS authorization. Stored EC2 token invalid/expired → fresh interactive login required: open VAYREN UI → SYSTEM → BROKERS → complete FYERS login once. No PIN/TOTP/secret requested in chat. After login, re-run the session probe to confirm CONNECTED + funds.

**Previous update (zero-confusion-fyers-connection-system, 2026-10-13):** ONE-CLICK FYERS CONNECTION WITH EC2-OWNED SESSION.
1. Root causes fixed (no parallel manager, no UI-only masking):
   - Single `BrokerManager` per data_dir via `shared_manager()` — headless IPC and EC2 gateway share one worker/venue/snapshot (was two managers drifting).
   - DISCONNECT retains the encrypted token (was erasing it); separate explicit `revoke_session` is the logout.
   - FYERS HTTP 429 → `RATE_LIMITED` with Retry-After + bounded backoff/jitter (was unbounded generic retry); HTTP 400 classified (token-wording → expiry, else `BAD_REQUEST`).
   - Per-leg `rest/market_ws/order_ws` health with `DEGRADED` state (was CONNECTED while a leg was down); generation-counter + persisted desired-state intent kills late-reconnect races; `request_id` idempotency on CONNECT/DISCONNECT; disconnect disarms LIVE consent.
   - Unified snapshot (`DISCONNECTED/CHECKING/CONNECTING/LOGIN_REQUIRED/VERIFYING/CONNECTED/DEGRADED/RATE_LIMITED/RECONNECTING/ERROR/UNKNOWN`) with `next_action`/`version`, consumed by SYSTEM, LIVE, remote gateway, headless IPC; Rust `broker_connection.rs` maps it (UNKNOWN never healthy).
2. New files: `src/broker/session_state.py`, `src/broker/error_taxonomy.py` (+`redact`), `src/broker/audit.py`, `src/broker/tests/test_unified_session.py` (17 tests, all required scenarios with fakes only).
3. Verification: full `pytest` 1595 passed + 2 skipped (10:47); `cargo test -p vayren-domain` 286 passed; `ruff check` + `ruff format --check` clean; `pyright` 0 errors on touched files; all governance validators pass; `cargo check -p vayren-shell` passes.
4. NOT done (needs explicit permission): real FYERS login authorization, EC2 deploy/restart, live read-only gateway verification, production release rebuild.

**Latest update (live-controls-mode-simplification-and-production-binary, 2026-10-12):** SIMPLIFY LIVE TRADING MODE (KEEP LIVE ONLY) & UPDATE PRODUCTION RELEASE EXE.
1. Trading Mode Simplification:
   - Kept LIVE as the single visible trading mode on the LIVE execution workstation.
   - Removed PAPER and SANDBOX mode selector buttons from both wide and narrow top status/control bars in `crates/vayren-shell/ui/live.slint`.
   - Preserved all underlying backend and domain execution mode structures (`PAPER`, `SANDBOX`, `LIVE`) for future or headless use without deleting domain capability.
   - Preserved start prevention rule: selecting LIVE mode never automatically starts trading.
2. Control Responsiveness & Live Verification:
   - START/STOP/HALT button dispatch verified end-to-end: UI button click triggers callback -> `LiveState` pushes JSON action -> main UI loop drains into worker thread -> forwarded to local Python backend (`_live_action`) -> applied to `LiveTradingService` -> fresh snapshot projected back to UI.
   - Pending states (`STARTING...`, `STOPPING...`, `HALTING...`) provide immediate UI feedback while awaiting backend confirmation.
   - Runtime timing, FYERS broker status, and order tracking preserved accurately from authoritative sources.
3. Production Release Binary Built & Verified:
   - Output Path: `C:\Users\visha\Desktop\vayren-core\target\release\vayren-shell.exe`
   - Timestamp: `26/10/12 01:07:49 AM`
   - File Size: `37,605,888` bytes (~35.86 MB)
   - SHA-256: `EEBD26019FAB3F46DF7F6FCDFE5ADE6CD62355F6ECDD479C931B32333AADC178`
   - Desktop Shortcut `C:\Users\visha\Desktop\VAYREN.lnk` verified targeting `C:\Users\visha\Desktop\vayren-core\target\release\vayren-shell.exe` with arguments `--data-dir D:\ZerodhaTradingData --strategy-dir D:\ZerodhaTradingData\strategies`.
4. Verification & Test Suite:
   - Rust tests: `cargo test -p vayren-domain --lib live` (59 passed in 0.02s); `cargo test -p vayren-shell --lib` (50 passed in 0.18s).
   - Python tests: `pytest src/app/tests/test_live_broker_capital.py src/app/tests/test_live_strategy_sync_and_watchlist.py src/app/tests/test_lab_to_live_universe_e2e.py -q` (25 passed in 4.01s).

**Previous update (live-terminal-synchronization-and-production-binary, 2026-10-12):** RESOLVE LIVE TERMINAL SYNCHRONIZATION AND BUILD UPDATED PRODUCTION RELEASE EXE.
1. Live Action & Snapshot Unification in Packaged Remote Shell:
   - Initial Snapshot Ingestion: In `crates/vayren-shell/src/main.rs`, removed `if !is_remote` gate at startup so `GetLiveSnapshot` is always requested from the workstation's local `PythonBackend`, truthfully populating the persisted session (`OBR C1C4`, 22 saved stocks, FYERS selection and capital) regardless of launch mode.
   - Action Refusal Removed: Removed the control refusal drain that previously discarded START, STOP, HALT, and setup actions with `"remote UI: control '{name}' refused (read-only client, nothing sent)"`. All actions are now drained and forwarded to the local backend worker thread in order.
   - Live Polling & Strategy Lab Sync: Unified periodic live snapshot polling and Strategy Lab universe save triggers so saving stocks in Strategy Lab immediately updates the LIVE workspace and disk universe without restart.
   - Safeguarded Strategy and Broker Translation: In `crates/vayren-shell/src/remote_live.rs`, only insert strategy when name/id is non-empty (preventing empty frames from clearing `strategy_index` to item 0 `"EMA Crossover"`), and preserve current broker name when remote sections omit broker identity.
2. Backend Broker Resolution Hardening:
   - In `src/app/services/live_trading_service.py` (`_broker_view`), prioritize the active broker reported in manager snapshot (`fyers`) when the fallback selection is not present in the manager's registered venues.
   - In `src/app/headless.py`, immediately refresh `LiveTradingService` active configuration upon Strategy Lab universe save.
3. Production Release Binary Built & Verified:
   - Output Path: `C:\Users\visha\Desktop\vayren-core\target\release\vayren-shell.exe`
   - Timestamp: `26/10/12 12:41:47 AM`
   - File Size: `37,674,496` bytes (~35.93 MB)
   - SHA-256: `DBC91B334554AC2739B17F25369FBDCD73E6A04842D3723491F2857C240B3479`
   - Desktop Shortcut `C:\Users\visha\Desktop\VAYREN.lnk` verified targeting `C:\Users\visha\Desktop\vayren-core\target\release\vayren-shell.exe` with arguments `--data-dir D:\ZerodhaTradingData --strategy-dir D:\ZerodhaTradingData\strategies`.
4. Verification & Tests:
   - Python tests: `pytest src/app/tests/test_live_broker_capital.py src/app/tests/test_live_strategy_sync_and_watchlist.py src/app/tests/test_lab_to_live_universe_e2e.py -q` (25 passed in 2.80s).
   - Rust tests: `cargo test -p vayren-domain --lib live` (59 passed in 0.01s); `cargo test -p vayren-shell --lib` (50 passed in 0.12s).

**Previous update (documentation-and-coding-speed-optimization, 2026-10-12):** DOCUMENTATION & CODING SPEED OPTIMIZATION.
1. Active Documentation Overhead Pruned:
   - `docs/ai_memory.md` pruned from 363 KB (1,434 lines) down to 5 KB (52 lines), achieving a 98.6% reduction in context overhead. Full 1,389-line history preserved in `docs/archive/ai_memory_history.md`.
   - Historical static milestone reports & benchmark logs (9 files) consolidated from `docs/architecture/`, `docs/live/`, and `docs/performance/` into `docs/archive/reports/` with catalog index `docs/archive/README.md`.
2. Stale Documentation Paths Remediated:
   - Fixed obsolete `scripts/` -> `tools/`, `00_app`..`09_broker` -> `src/*` across `tools/README.md`, `tools/forensics/README.md`, `tools/speed/README.md`, `crates/vayren-core/README.md`, `src/broker/README.md`, `docs/module_contracts.md`, and `AI_ENTRY.md`.
3. Fast Speed Protocol Embedded:
   - AI entry workflow unified into a 7-step Fast Speed Protocol: routing via `route.py` (~1.9 ms), targeted context reading, target language selection (Rust core/perf, Python strategy/AI, Slint UI), surgical edits, sub-second inner loop test execution, and single full gate at completion.
4. Validation & Safeguard Verification:
   - Rebuilt `docs/repo_graph.json` with 0 unresolved entities/relations (canonical hash synchronized).
   - All CI governance and structure validators verified passing (398 Python files, 20 Slint files, 19 routes).
   - Formatted all Python files with `ruff format` (398/398 files cleanly formatted).
   - Cargo check verified on both `vayren-core` and `vayren-shell`.

**Previous update (live-core-problems-fixed-and-release-binary, 2026-10-11):** PERMANENTLY RESOLVE THREE CORE LIVE TRADING PROBLEMS & BUILD PRODUCTION RELEASE EXE.
1. Problem 1 Resolved (Saved Universe Synchronisation):
   - Canonical Strategy ID & Alias Mapping: In `src/strategy/universe_store.py` and `src/strategy/registry.py`, added canonicalization stripping file extensions (e.g., `.py`), case folding, whitespace/hyphen normalization, and resolving aliases (`"OBR"`, `"obr-c1c4"` -> `"obr-c1c4"`).
   - Authoritative Single Source: In `src/app/services/live_trading_service.py`, `configure()` now loads the authoritative saved universe from disk (`self._universes.symbols_for(...)`) whenever a strategy is set or switched.
   - Read-only LIVE Stock Ingestion: LIVE displays the full 22 stocks from `D:\ZerodhaTradingData\live\strategy_universes.json` without allowing UI setup payloads to overwrite disk stores.
2. Problem 2 Resolved (Broker Identity & Capital Synchronization):
   - Active Broker Discovery: In `src/app/services/live_trading_service.py` (`_broker_view`), removed silent fallback to unconfigured Zerodha; dynamic discovery prioritizes any connected broker first, then any configured broker (e.g. FYERS).
   - Selection Compatibility: `BrokerSelectionService._compatibility_default()` now inspects credentials store before fallback. `BrokerManager._promote_connected()` syncs selection.
   - Capital Ingestion: Mutual fallback between `equity` and `available_margin` in both `src/remote/snapshot.py` and `crates/vayren-domain/src/live.rs`. Returns `broker_name`, formatted INR figures, and live timestamp.
3. Problem 3 Resolved (Execution Controls, Timing & Order Blotter):
   - In-Flight Control Feedback: `crates/vayren-domain/src/live.rs` projects `pending_action` (`"start"`, `"stop"`, `"halt"`) to `ActiveStrategyView` status (`"STARTING"`, `"STOPPING"`, `"HALTED"`) without faking underlying backend state prematurely.
   - Pending Action Stability: `apply_snapshot` preserves optimistic pending action across asynchronous snapshot updates until conclusive backend state (`RUNNING`, `ERROR`, `HALTED`, or `STOPPED` with reason) arrives. Lingering action notes are cleared.
   - Truthful Order Blotter: `ExecutionEngine.transition(OrderState.SUBMITTED)` preserves `broker_order_id`, and `live.rs` formats client order ID with broker order ID (`format!("{cl_id} · {br_id}")`).
4. Production Release Executable Built & Verified:
   - Output Path: `C:\Users\visha\Desktop\vayren-core\target\release\vayren-shell.exe`
   - Timestamp: `26/10/11 11:39:38 PM`
   - File Size: `37,681,152` bytes
   - SHA-256: `9B5828AA4B6595B43D3EDC143525F32274CF5F1DCF8F8327183C2382D7442B74`
   - Desktop Shortcut `C:\Users\visha\Desktop\VAYREN.lnk` verified pointing to this release binary.
5. Verification & Tests:
   - Python tests: `pytest src/strategy/tests src/app/tests src/execution/tests -q` (558 passed in 30.45s).
   - Rust tests: `cargo test -p vayren-domain --lib live` (59 passed in 0.01s).
   - Linting: `ruff check` on all touched files (0 errors).


**Previous update (start-button-root-cause-fixed, 2026-10-12):** START BUTTON DEAD ON THE PRODUCTION EXE - ROOT CAUSE FOUND AND FIXED.
1. Root Cause (NOT a stale binary - the running EXE matched `target\release\vayren-shell.exe` exactly):
   - The EXE is built with `--features packaged-remote`, and the launch contract (`launch.rs`) IGNORES legacy `--data-dir`/`--strategy-dir` for packaged builds -> it ALWAYS launches in REMOTE mode.
   - `remote_live.rs::apply_remote_sections` hard-set `state.bridge_wired = false` after every snapshot.
   - `LiveState::can_start()` requires `bridge_wired`, and the Slint `VButton` gates its `TouchArea` on that property -> `clicked` NEVER fired. No callback, no pending state, no message: a literally inert button.
   - Second independent blocker: `RemoteClient::send_command` had NO call site anywhere; the pump logged incoming `command_result` as "unexpected". The Python gateway (`server.py::_on_command`) already supported commands, so the capability existed server-side but was unreachable from the client.
   - Third issue: in remote mode the local Python backend still polled LIVE every 1s and overwrote the gateway snapshot (split-brain; also flipped `bridge_wired` back to true, making the button flicker).
2. Fixes:
   - Command authority now comes from the gateway's granted role: `trade` -> controls live, `read` -> honestly inert with an explanatory note (`remote_live.rs::apply_role`, `LiveState::remote_commands_allowed`). Link loss fails controls closed and clears an undelivered pending action.
   - Real command path: new `RemoteCommand` channel -> pump thread -> `send_command`; `CommandResult`/`GatewayError` -> `CommandAck` -> clears pending and paints the real outcome (`start_blockers` -> `status_reason` -> `detail`).
   - `LiveState::start_clickable()` (weaker than `can_start`) so a BLOCKED START is pressable and returns the backend's real reason instead of being silently disabled.
   - `PENDING_ACTION_TIMEOUT` (10s) + `expire_pending_action()` so an unanswered request can never pin `STARTING…` forever.
   - In host mode `start()` ALWAYS forwards to the backend (the service re-validates every gate); readiness stays fail-closed in `can_start`/`start_blockers`.
   - Remote mode no longer polls/dispatches LIVE to the local backend (gateway is the single authority for LIVE truth).
   - New outcome/blocker banner on the LIVE screen - `start-verdict`, `note` and `blockers` were previously computed but NEVER RENDERED.
3. Contract change (deliberate): `vayren-live-view` test `blocked_start_emits_no_event_running_stop_does` asserted a blocked START emits NO action - that assertion WAS the dead button. Renamed to `blocked_start_reaches_the_backend_but_fakes_no_state`; the safety invariant (no fake session transition) is preserved verbatim.
4. New tests: `stopped_session_start_is_clickable`, `start_clickable_while_blocked_so_the_reason_can_be_asked_for`, `start_click_forwards_exactly_one_request_and_duplicates_are_ignored`, `pending_start_blocks_the_button_until_the_backend_answers`, `rejected_start_clears_pending_and_shows_the_backend_reason`, `unanswered_pending_action_expires_instead_of_stranding_the_button`, `stop_forwards_while_running_and_reports_the_confirmed_result`, `halt_invokes_the_emergency_stop_mechanism`, `start_without_a_backend_stays_inert_and_says_so`, plus `remote_live` role/link-loss tests.
5. Validation: `cargo check --workspace --all-targets` exit 0; domain 271 passed, shell 53 passed, remote-client 27 passed, live-view 4 passed; pytest src/remote src/app = 229 passed + 1 KNOWN FLAKY (`test_auth_timeout_closes_quiet_client`, passes in isolation; zero Python files changed).
6. Release binary rebuilt with `--features packaged-remote`:
   - Path: `C:\Users\visha\Desktop\vayren-core\target\release\vayren-shell.exe`
   - Built: 2026-10-12 02:15:55 | Size: 37,660,672 bytes
   - SHA-256: `095A1BF890C281CAF30543FE7EBE51F6EEBE673C5AD2C485E158BBFBD4179D54`
7. NOT YET VERIFIED: the actual START click against the live gateway. State machine + dispatch are proven by tests; end-to-end websocket delivery requires the manual GUI test.

**Update (global-broker-selection-authority, 2026-10-12):** SYSTEM IS NOW THE SINGLE SOURCE OF TRUTH FOR THE DEFAULT BROKER.
Root cause (four connected defects, all in the "substitute a broker at runtime" family):
1. `live_trading_service.py::_broker_view` promoted `next(e for e in brokers if e.connected)` over the SYSTEM selection - a connected Zerodha silently displaced a chosen FYERS for capital, order routing and market data.
2. `broker_manager.py::_activate` called `selection_svc.select(broker_id)` on EVERY successful connect, so connecting a second venue REPLACED the operator's persisted choice application-wide.
3. `broker_manager.snapshot()["active_broker"]` reported the first CONNECTED broker, contradicting the selection and pulling other surfaces onto the wrong venue.
4. `broker_selection_service._compatibility_default` hardcoded Zerodha and only looked elsewhere when Zerodha had no stored config, so a configured FYERS still got a Zerodha default.
Fixes:
- `_broker_view` now binds the selection HARD when it is authoritative (`reason == "user-selected"`); a disconnected selection is reported DISCONNECTED (fail-closed) and another venue's funds never leak into its view.
- New `_resolved_selection() -> (name, authoritative)` distinguishes a real user/persisted choice from the migration placeholder, so the compatibility default can never outrank an explicit selection.
- `_auto_select_if_unset` replaces the unconditional select: it seeds a first-run selection ONLY when none exists; an explicit choice is never stolen by a later connect.
- `active_broker` is now the authoritative selection, falling back to first-connected only when nothing is selected.
- `_compatibility_default` prefers the first CONFIGURED venue; Zerodha is used only when nothing is configured (provenance stays in `reason`).
- `select(..., session_running=...)` REFUSES a broker switch while a trading session runs (the venue is bound at `_build_sessions`), and `_system_snapshot` reports the refusal via `selection_error` while still showing the broker that is actually selected.
- `_live_session_running` fails CLOSED (unreachable service => treat as running => block the switch).
- `_system_snapshot` reports the AUTHORITATIVE selection, not the merely-requested id, so a refused switch is never displayed as applied.
No duplicate selection store was created (existing `FileSelectionStore` + `BrokerSelectionService` reused); no LIVE broker selector was added - selection stays SYSTEM-only. `market_download.py` broker_index is the documented per-surface historical-data override, untouched.
Validation: new `src/app/tests/test_broker_selection_authority.py` (17 tests) + full suite `pytest src/app/tests src/broker/tests src/remote/tests` = 444 passed (the only failure seen, `test_auth_timeout_closes_quiet_client`, is the known AGENTS.md flaky and passes in isolation). ruff check + format clean. Verified live in-process: selecting zerodha while FYERS is connected reports zerodha/disconnected and never substitutes.

**Update (broker-selection-verification-and-gateway, 2026-10-12):** VERIFICATION PASS - no further code changes were required.
1. Persisted selection confirmed on the real data dir: `D:\ZerodhaTradingData\broker_selection.json` = {"name":"fyers","environment":"paper","reason":"user-selected"}. `reason == user-selected` means the selection is AUTHORITATIVE and binds hard (the new `_resolved_selection` contract).
2. Runtime reality checked against the real data dir (`_trading_service_snapshot`, no mocks): session STOPPED, broker `fyers`, connected False, capital broker `FYERS`, capital status `NOT CONFIGURED`. It did NOT fall back to Zerodha. Per-broker state: zerodha LOGIN_REQUIRED/not connected, fyers LOGIN_REQUIRED/not connected, and `snapshot()["active_broker"] == "fyers"` - i.e. the SELECTION, not "first connected".
3. Architecture fact: `src/remote/` (the gateway) contains NO broker-selection code of its own. `HeadlessGateway` calls `app.headless._trading_service_snapshot`, so the gateway inherits the broker-selection fix ONLY through `live_trading_service._broker_view` - the gateway must be running a checkout that INCLUDES these edits. `src/remote/snapshot.py::build_snapshot` projects the `broker`/`capital` sections verbatim ("source": "SYSTEM broker manager view").
4. Gateway discovered: `wss://ip-172-31-12-198.taila678c5.ts.net/vayren/v1` -> tailscale IP 100.75.185.37 (the built-in `REMOTE_DEFAULT_URL`; confirmed via the running EXE holding ESTABLISHED TLS sockets to 100.75.185.37:443). Read-only empty-token probe: TLS upgrade OK, server replies `error` / `AUTH_FAILED` / "invalid remote token" - gateway process is LIVE and authenticating. No credential was sent, read, or logged. The gateway's exact git revision could NOT be determined remotely (schema v1, no version/revision in the welcome frame); it must be checked on the host.
5. DEPLOYMENT PENDING (cannot be done from this machine): the running packaged EXE is remote-mode, so the visible LIVE screen is fed by the GATEWAY's code, not by the local Python edits. The gateway must pull/receive this revision and restart its `remote.runner` service. Restart is safe right now: the live session is STOPPED, so no execution would be disrupted.
6. The auth-timeout test flake is PRE-EXISTING, proven by a pristine `git worktree` at HEAD 7513af3: it failed 5/8 runs there vs 2/5 in this tree. `git status -- src/remote` is empty - none of my changes touch that module. Not a regression; already documented as the known flake in AGENTS.md.
7. Tests: `src/app/tests/test_broker_selection_authority.py` = 22 passed (added the full acceptance matrix: explicit FYERS, explicit Zerodha, disconnected selection, multiple connected brokers, application restart, SYSTEM==LIVE consistency, capital isolation). Full suite `src/app + src/broker + src/remote` = 448 passed, 1 pre-existing flake.
8. No EXE rebuild performed and none required: every change is Python-side (backend behaviour); the Rust/Slint work was from the earlier START-button fix and is already built (SHA256 095A1BF8...).

**Update (gateway-broker-selection-deployed, 2026-10-12):** DEPLOYED to the real remote gateway. No EXE rebuild, no GitHub push.
1. Target positively identified (not guessed): host `ip-172-31-12-198` / 100.75.185.37 via the `vayren` SSH host alias; service `vayren-remote.service` (/etc/systemd/system/vayren-remote.service); checkout `/home/ubuntu/vayren-core`; data dir `/home/ubuntu/.vayren/data`; deployed revision `0a2d8cf` on `main` (2026-10-07), i.e. ~4 days BEHIND local HEAD `7513af3`.
2. Gateway carries SERVER-ONLY local edits that must never be overwritten: `src/app/services/live_trading_service.py` (symbol-universe fallback + timeframe checks skipped when the local store is empty - the gateway does not mirror the big historical store) and `src/remote/snapshot.py` (extra sections quotes/pnl/available_strategies/... the remote client needs). Both files also show a huge false diff that is ONLY CRLF vs LF (`git diff -w --ignore-blank-lines` reduces 4621 lines to 9 real ones).
3. Deployment scope was deliberately MINIMAL and evidence-based. The diff of `broker_selection_service.py` between the deployed revision and the local fixed file is EXACTLY the two verified fixes (configured-first compatibility default, `session_running` switch guard) and nothing else, and the gateway had no local edits to that file - so replacing it was safe. `broker_manager.py` and `live_trading_service.py` were deliberately NOT copied: a wholesale copy would destroy the server-only universe fallback and mix revisions.
4. Root cause of the live symptom found: the gateway had NO `broker_selection.json`, so it resolved the hardcoded compatibility default (ZERODHA) while the operator's SYSTEM choice (FYERS, on Windows) lived in a different data dir. Fixed by deploying the file AND seeding `/home/ubuntu/.vayren/data/broker_selection.json` = {name: fyers, reason: user-selected}.
5. Safety before restart: gateway session store carries config only (no `timing` block) and the 7-day journal has 0 "strategy started/order submitted/session start" events - no execution to disrupt. Restart done with those checks as a hard gate.
6. Backup: `/home/ubuntu/vayren-backups/brokerfix-20261011-211705/` (original broker_selection_service.py + DEPLOYMENT_CONTEXT.txt with revision/pid).
7. Restart result: `vayren-remote` PID 214054 -> 242019, active/running, listener still loopback-only `127.0.0.1:8765`, startup log shows FYERS auto-auth + `ORDER_WS_CONNECTING broker=FYERS`.
8. Health check (on-host, read-only, token never leaves the box): handshake OK, role=trade, schema v1 -> `broker.name='fyers'`, `broker.status='DISCONNECTED'`, blockers name fyers ("fyers is not authenticated (ACCOUNT_NOT_READY)", "no live trading venue for broker 'fyers'"). External TLS probe post-restart still answers AUTH_FAILED correctly.
   => ACCEPTANCE CRITERION MET: FYERS selected + disconnected => gateway reports FYERS disconnected, never Zerodha.
9. Known remaining gap (NOT fixed, needs a decision): in remote mode the EXE's SYSTEM panel writes the selection to the LOCAL data dir, while LIVE reads the GATEWAY's store - the two are separate files, so a SYSTEM change made on Windows does not automatically propagate to the gateway. Unifying them means routing the selection through the remote command path (client-side change).

**Update (set-as-default-button, 2026-10-12):** "SET AS DEFAULT" button added to SYSTEM -> BROKERS.
KEY ARCHITECTURAL FIX: opening a broker's detail panel NO LONGER changes the global default. `_system_snapshot` used to call `selection_service.select(selected_broker)` whenever a panel was opened, so merely LOOKING at Zerodha silently replaced the persisted default. That made a "SET AS DEFAULT" button meaningless (the default always equalled the open panel). The switch now happens ONLY through the explicit `select_broker` action.
- `headless.py::_system_snapshot` - removed the implicit select; now reports `selected_id` = authoritative persisted default and a NEW `panel_id` = which detail card is open.
- `broker_connection.rs::BrokerWorkspace` - new `default_id` (authoritative) and `panel_id` (view state) fields, both falling back to `selected_id` for legacy snapshots.
- `shell.rs::apply_connection` - binds `conn_broker_id` from `panel_id` and new `conn-default-id` from `default_id`.
- `main.rs` - `on_broker_select_requested` is now VIEW ONLY (no select, no remote command); new `on_broker_set_default_requested` sends the existing `select_broker` command over the authenticated remote channel and refreshes the snapshot. `conn-default-saving` is cleared when a fresh SYSTEM snapshot lands, so a failed save can never stick on SAVING.
- `broker_connection.slint` - compact outlined "★ SET AS DEFAULT" in the detail header immediately before the Connected/PAPER pills; shows "✓ DEFAULT BROKER" when the open panel IS the persisted default and "SAVING…" while pending. Reuses existing palette tokens (accent-dim, fs-label, sp-md, 26px/13px radius). Click disabled when already default or while saving (no duplicate submits).
Tests: new `src/app/tests/test_set_default.py` (10 tests) - FYERS default, Zerodha default, disconnected broker stays default with no substitution, refused/unknown broker reports no false success, gateway translation rejects malformed ids, restart persistence for both brokers, and opening a panel never changes the default. Existing obsolete test `test_system_snapshot_keeps_the_old_broker_when_a_switch_is_refused` rewritten as `test_opening_a_panel_never_changes_the_default_even_while_running`.

**ROOT CAUSE FOUND (2026-10-12, SYSTEM Connected vs LIVE Disconnected):** Gateway FYERS LIVE blocker was NOT a bug — it is a deliberate `auth_only` config. On the gateway checkout (HEAD 0a2d8cf) `src/broker/providers/fyers/spec.py` has:
- `venue_register=_register_session` (a stub that only returns `True, "authenticated FYERS session (auth-only — no trading venue in this phase)"`) instead of the real `register_fyers_live_instance`.
- `extra={"auth_only": "true"}`.

So `_activate` reported `registered=True` (truthy) and reason "all checks passed", but `fyers-live` was NEVER added to the registry. `_resolve_live_venue_id("fyers")` therefore returned `None` on every call -> LIVE blocker "no live trading venue for broker 'fyers'". This is intentional (no trading venue configured for FYERS on the gateway yet), not a defect. Enabling the real trading venue would require deploying the newer spec.py (which has register_fyers_live_instance) and would ADD live-order capability — deliberately NOT done, per "no real orders" instruction.

Fixes actually applied and verified (connection side): deployed `broker_manager.py` (market_data retry + non-fatal) and `live_trading_service.py` (`_ensure_live_venues` instance registration + venue id in the `validate()` cache key). After these, gateway reports broker `CONNECTED` / `all checks passed`, `MARKET_WS_CONNECTED` + `ORDER_WS_CONNECTED` both appear, and LIVE `status` is `CONNECTED` (was `DISCONNECTED`). The only remaining LIVE blocker is the intentional auth-only venue blocker above.

**Update (ci-stateful-shard-native-lib, 2026-10-13):** CI FAIL ON TAG/BRANCH WITH SAME GREEN SHA — ROOT CAUSE + LOCAL-ONLY FIX (NOT PUSHED).
1. Symptom: `5fd0def` green on `main` (38353351065) but red on `v1.35.0` tag (38353355365) and `h` branch (38360420428). Failing job `test-shard-stateful`: `test_bench_private_py_change`, `test_bench_public_py_change`, `test_bench_stale_cache_miss` — each reports `['pytest src/app/tests -q', 'pytest src/broker/tests -q', 'pytest src/execution/tests -q']` as FAIL.
2. Root cause: the shard's `cargo build --release -p vayren-core` was gated by `if: github.ref == 'refs/heads/main' || steps.native.outputs.changed == 'true'`, so non-main pushes with no native diff skipped the kernel cdylib build. Scope-bench then executed real module suites whose bridges call `load_vayren_core()` -> `NativeBridgeError: Rust native library not found`. Proven locally: clean worktree without `target/` fails the same way; with the DLL present, broker 197 + app 243 + execution 187 all pass, and all 3 bench tests pass.
3. Fix: `.github/workflows/ci.yml` build step made unconditional + comment. YAML parses; `test_shard_contract.py` 5 passed. PUSHED as `4387d79` to `main` (only that file; all other WIP left local/unpushed) with user approval; CI run 38379499462 ALL GREEN (stateful 3m38s, gate passed).
