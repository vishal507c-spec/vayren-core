# AI Memory — Current State Only (compact, active path)

> This file holds only current state + open items. Append new state here;
> prune superseded entries to docs/archive/ai_memory_history.md.

**Owns:** Current state, open items, verified facts, oddities.
**Historical archive:** docs/archive/ai_memory_history.md (all past session logs preserved).
**Build-performance history:** docs/CARGO_BUILD_EXPERIMENTS.md.

**Latest update (documentation-and-coding-speed-optimization, 2026-10-12):** DOCUMENTATION & CODING SPEED OPTIMIZATION.
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

