# Universal UI-Driven Strategy Platform Report

**Author:** Principal Software Architect & Quantitative Systems Engineer  
**Date:** 2026-10-10  
**Scope:** Strategy Layer Consolidation, UI-Driven Strategy Lifecycle, Central Registry, AST Safety Diagnostics, Versioning & Cross-Module Consumer Unification  
**Status:** Completed & Verified (Local Only — Zero Remote Push)

---

## Executive Summary

Vayren Core previously featured partially fragmented strategy catalogs and storage abstractions across Strategy Lab, Research, Backtesting, Live Trading, and Portfolio subsystems. New strategies frequently required duplicate registrations or manual coordination between runtime engines and storage catalogs.

This initiative delivers a **Universal UI-Driven Strategy Platform**:
1. **One Authoritative Central Strategy Layer**: `src/strategy/registry.py` and `StrategyRecord` now manage the canonical identity, parameter contracts, runtime specs, versions, execution modes, and lifecycle status (`ACTIVE`, `DRAFT`, `ARCHIVED`) across all subsystems.
2. **Dynamic Strategy Lifecycle & Builder APIs**: Seamless create, duplicate, update, archive, and rollback capabilities without restarting or recompiling the application.
3. **AI Strategy Assistant & AST Diagnostics**: Safe static AST code analysis in `src/strategy/assistant.py` that enforces strict sandbox security, detects lookahead bias risks, verifies execution contracts, and extracts parameter specifications.
4. **End-to-End Consumer Unification**: Strategy Lab, Backtest Service, Research, Portfolio, and Live execution resolve all strategy definitions and source code through the central `StrategyRegistry` and `StrategyRecord` contracts.

---

## 1. Architectural Architecture & Before vs. After State

### Before State
- Built-in strategies (`OBR`, `C1C4`, `SMA`, `EMA`, `RSI`) were hardcoded in `StrategyRegistry` with static dictionaries and no dynamic mutation API.
- Custom/Lab strategies were managed independently through `StrategyStorage` without versioning snapshots, rollback mechanisms, or lifecycle states (`ARCHIVED`, `DRAFT`).
- Backtest Service looked up strategies via ad-hoc file matching before consulting registry definitions.
- Code validation was split between generic runtime syntax errors and execution failures with no pre-run AST security sandbox or lookahead bias inspection.

### Target & Implemented State
```
+-----------------------------------------------------------------------------------------+
|                                    UI Layer (Slint)                                     |
|  - Strategy Lab Code Editor                                                             |
|  - Strategy Management (Create / Duplicate / Archive / Rollback / Validate)             |
|  - AI Assistant Panel (Template Generation / AST Diagnostics / Parameter Tuning)       |
+-----------------------------------------------------------------------------------------+
                                             |
                                 BackendCommand / Python Bridge
                                             v
+-----------------------------------------------------------------------------------------+
|                                  src/app/headless.py                                    |
|  - create_strategy, duplicate_strategy, archive_strategy, validate_strategy, ai_assist   |
+-----------------------------------------------------------------------------------------+
                                             |
                   +-------------------------+-------------------------+
                   v                                                   v
+--------------------------------------+             +------------------------------------+
|       src/strategy/assistant.py       |             |     src/strategy/registry.py       |
|  - AST Syntax & Safety Verification  |             |  - Canonical Registry (Singleton)  |
|  - Forbidden Import Detection        |             |  - Builtins + Custom Synced Store  |
|  - Lookahead Bias Guardrails         |             |  - create_or_update / archive API  |
|  - Parameter Spec Extraction         |             +------------------------------------+
|  - AI Template Generation            |                               |
+--------------------------------------+                               v
                                                     +------------------------------------+
                                                     |    src/strategy/language/storage   |
                                                     |  - StrategyRecord (status, version)|
                                                     |  - Version History Snapshots       |
                                                     |  - Atomic Rollback                 |
                                                     +------------------------------------+
```

---

## 2. Implementation Details

### A. Central Strategy Layer (`src/strategy/registry.py`)
- Added `create_or_update(definition: StrategyDefinition)` to atomically register custom strategies into the central store and persist their specifications.
- Added `archive(strategy_id: str)` to mark strategies as `ARCHIVED` and purge them from active execution engines.
- Added `sync_storage(storage)` to synchronize disk-stored custom strategies with in-memory definitions on startup and updates.
- Maintained 100% backward compatibility for all built-ins (`OBR`, `C1C4`, `SMA`, `EMA`, `RSI`).

### B. Storage, Versioning & Rollback (`src/strategy/language/storage.py`)
- Upgraded `StrategyRecord` to include `status: str = "ACTIVE"` and `versions: tuple[dict[str, Any], ...] = ()`.
- In `update_strategy(...)`, created automatic snapshotting of previous code, parameters, and metadata before auto-incrementing minor versions (e.g., `1.0.0` -> `1.1.0`).
- Implemented `archive_strategy(strategy_id)` setting `status = "ARCHIVED"`.
- Implemented `rollback_strategy(strategy_id, target_version)` restoring historical source code and parameter dictionaries atomically.

### C. AI Strategy Assistant & AST Diagnostics (`src/strategy/assistant.py`)
- **AST Safety & Sandbox Validator**: Inspects abstract syntax trees to block unsafe module imports (`os`, `sys`, `subprocess`, `socket`, `ctypes`, `requests`) and dangerous built-ins (`eval`, `exec`, `open`, `__import__`).
- **Lookahead Bias Detection**: Detects common lookahead bugs, such as negative indices into future series (`series[-1]`) or references to `.shift(-1)`.
- **Parameter Extraction**: Automatically extracts default parameters, types, and value constraints from strategy class definitions.
- **Template Generation & AI Prompt Formatting**: Generates clean, type-annotated Vayren strategy boilerplates for trend-following, breakout, and mean-reversion strategies.

### D. Consumer Unification (`src/app/services/backtest_service.py`, `src/app/headless.py`)
- Unfied backtest source resolution: `_strategy_source(...)` first queries `get_strategy_registry()` for canonical definitions before consulting storage records or built-ins.
- Extended headless command processing with `create_strategy`, `duplicate_strategy`, `archive_strategy`, `validate_strategy`, and `ai_assist_strategy`.

### E. Rust Python Bridge (`crates/vayren-shell/src/python_bridge.rs`)
- Added strongly typed `BackendCommand` variants:
  - `CreateStrategy { name, template_type }`
  - `DuplicateStrategy { strategy_id, new_name }`
  - `ArchiveStrategy { strategy_id }`
  - `ValidateStrategy { strategy_id, code }`
  - `AiAssistStrategy { prompt, strategy_id }`
- Added corresponding `BackendResponse` payload handlers.

---

## 3. Verification & Test Evidence

### Strategy Unit & Lifecycle Tests
Executed: `pytest src/strategy/tests/test_unified_platform.py`
```
src/strategy/tests/test_unified_platform.py::test_strategy_assistant_safety_diagnostics PASSED
src/strategy/tests/test_unified_platform.py::test_strategy_storage_versioning_and_rollback PASSED
src/strategy/tests/test_unified_platform.py::test_central_strategy_registry_lifecycle PASSED
3 passed in 0.75s
```

### Full Strategy Suite Regression
Executed: `pytest src/strategy/tests -v`
```
============================ 209 passed in 30.89s =============================
```
All 209 strategy tests (including router, provider mapping, universe, research pins, and eligibility) passed with zero regressions.

### Architectural Governance & Gate Checks
| Check | Command | Status |
|---|---|---|
| Import Boundaries | `python tools/validate_imports.py` | **PASSED** (0 violations) |
| Structure Rules | `python tools/validate_structure.py` | **PASSED** (9 domains validated) |
| Authority Boundaries | `python tools/validate_authority.py` | **PASSED** (236 Python, 19 Slint, 19 routes) |
| Route Integrity | `python tools/validate_routes.py` | **PASSED** (19 routes checked) |
| Language Ownership | `python tools/validate_language_ownership.py` | **PASSED** (395 files checked) |
| Knowledge Graph | `python tools/validate_repo_graph.py` | **PASSED** (5846 entities, 12543 rels, 0 unresolved) |
| Rust Domain Logic | `cargo test -p vayren-domain --lib` | **PASSED** (254 passed in 0.47s) |

---

## 4. Compliance & Security Commitments

1. **Local Isolation**: All changes are strictly maintained locally on the active branch. Zero commits or tags pushed to remote repositories.
2. **Parity Preserved**: Original OBR C1C4 and built-in trading models maintain 100% calculation parity and parameter integrity.
3. **Execution Safety**: Dynamic strategies are vetted through AST sandboxing prior to runner dispatch, preventing arbitrary host system access.
