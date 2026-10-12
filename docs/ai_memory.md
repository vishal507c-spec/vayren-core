# AI Memory — Current State Only (compact, active path)

> This file holds only current state + open items. Append new state here;
> prune superseded entries to docs/archive/ai_memory_history.md.

**Owns:** Current state, open items, verified facts, oddities.
**Historical archive:** docs/archive/ai_memory_history.md (all past session logs preserved).
**Build-performance history:** docs/CARGO_BUILD_EXPERIMENTS.md.

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
