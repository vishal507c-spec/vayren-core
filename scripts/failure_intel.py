"""Predictive failure recovery + auto-escalation (Phase 7).

Pipeline:

    FAILURE -> NORMALIZE -> CLASSIFY (taxonomy A-R, evidence-backed, never
    exit-code-only) -> ATTRIBUTE (owning layer) -> DECIDE (bounded recovery
    policy, retry budget, deterministic escalation) -> SAFE RETRY / ESCALATE
    / STOP -> REVALIDATE -> FINAL RESULT.

Consumes (never redesigns) the Phase 1-6 surfaces: repo graph, warm index,
patch surface, context packets, execution surface, validation scope + result
cache. Phase 7 diagnoses and routes recovery only. It never edits source,
never weakens validators, never marks failures as passed, never retries
without bound, and never validates stale state as fresh.

Usage:
    python scripts/failure_intel.py --command "pytest x -q" --rc 1 --tail out.txt
    python scripts/failure_intel.py --result validation_result.json [--recover]
    python scripts/failure_intel.py --task "..." --precheck
    python scripts/failure_intel.py --bench
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import context_packet  # noqa: E402
import execute_surface  # noqa: E402
import repo_graph  # noqa: E402
import repo_index  # noqa: E402
import validate_scope  # noqa: E402

ROOT = SCRIPTS_DIR.parent
SIG_DIR = repo_index.INDEX_DIR / "failure_signatures"

SCHEMA_NORMALIZED = "failure-normalized/v1"
SCHEMA_DECISION = "failure-decision/v1"
SCHEMA_CHAIN = "failure-chain/v1"
SIG_SCHEMA = "failure-signatures/v1"
SIG_KEEP = 100
RAW_TAIL_CAP = 2000
EVIDENCE_CAP = 12
EVIDENCE_VALUE_CAP = 200
LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")

CATEGORIES = (
    "CODE_FAILURE",
    "TEST_FAILURE",
    "VALIDATION_FAILURE",
    "SYNTAX_FAILURE",
    "IMPORT_FAILURE",
    "CONTRACT_FAILURE",
    "OWNERSHIP_FAILURE",
    "LANGUAGE_FAILURE",
    "STALE_STATE",
    "CONCURRENT_MODIFICATION",
    "MISSING_TARGET",
    "MISSING_TEST",
    "VALIDATION_UNAVAILABLE",
    "ENVIRONMENT_FAILURE",
    "TOOLCHAIN_FAILURE",
    "TIMEOUT",
    "INFRASTRUCTURE_FAILURE",
    "UNKNOWN",
)

FINAL_STATES = (
    "RECOVERED",
    "RETRYING",
    "ESCALATED",
    "BLOCKED",
    "STOPPED",
    "ENVIRONMENT_FAILURE",
    "CODE_FAILURE",
    "UNKNOWN",
)

NEXT_ACTIONS = (
    "REFRESH_INDEX",
    "REFRESH_PACKET",
    "REBUILD_GRAPH",
    "RECOMPUTE_SCOPE",
    "RETRY_VALIDATION",
    "ESCALATE_VALIDATION",
    "REGENERATE_EXECUTION_SURFACE",
    "STOP",
    "REPORT_ENVIRONMENT",
    "REPORT_CODE_FAILURE",
)

OWNERSHIP = (
    "source_code",
    "test",
    "validation_rule",
    "graph_index",
    "route",
    "execution_surface",
    "environment",
    "toolchain",
    "repository_infrastructure",
)

# Terminal-state dominance for multi-command results (first match wins).
FINAL_PRIORITY = (
    "STOPPED",
    "BLOCKED",
    "ENVIRONMENT_FAILURE",
    "CODE_FAILURE",
    "UNKNOWN",
    "ESCALATED",
    "RETRYING",
    "RECOVERED",
)

# Validator script -> failure family (Phase 1-6 canonical command names).
VALIDATOR_FAMILY = {
    "validate_imports.py": "IMPORT_FAILURE",
    "validate_authority.py": "CONTRACT_FAILURE",
    "validate_routes.py": "CONTRACT_FAILURE",
    "validate_language_ownership.py": "LANGUAGE_FAILURE",
    "validate_structure.py": "VALIDATION_FAILURE",
    "validate_architecture_gate.py": "CONTRACT_FAILURE",
    "validate_repo_graph.py": "STALE_STATE",
    "context_engine.py": "CONTRACT_FAILURE",
}

# Pre-existing debt: commands known to fail at baseline on a clean tree.
# Classification only — Phase 7 never "fixes" these.
KNOWN_REPLACEMENTS = {
    "scripts/context.py": "scripts/context_engine.py",
}
_FYERS_COMMAND = (
    "pytest 02_data/data/tests/test_fyers_provider.py::"
    "test_reload_credentials_falls_back_to_layered_loader -q"
)

# --- output patterns (never classify from exit code alone) -----------------

_RE_FAILED = re.compile(r"^FAILED (\S+)", re.MULTILINE)
_RE_PYTEST_NODE = re.compile(r"^(\S+?\.py)::(\S+)", re.MULTILINE)
_RE_ASSERT = re.compile(r"AssertionError")
_RE_IMPORT_ERR = re.compile(r"ModuleNotFoundError|ImportError|ERROR collecting|ModuleNotFound")
_RE_NO_TESTS = re.compile(r"no tests ran|collected 0 items|exit code: 5|exit status 5")
_RE_PYRIGHT_LINE = re.compile(r"^(.+\.py):(\d+):(\d+)\s+-\s+error:", re.MULTILINE)
_RE_PYRIGHT_SUMMARY = re.compile(r"(\d+)\s+errors?,\s*(\d+)\s+warnings?")
_RE_RUFF_CODE = re.compile(r"\b([EF]\d{3})\b")
_RE_RUFF_LOCATION = re.compile(r"-->\s*(\S+?):(\d+):(\d+)")
_RE_RUFF_SYNTAX = re.compile(r"SyntaxError|invalid syntax|E999|Parse error")
_RE_CARGO_ERROR = re.compile(r"error\[E\d+\]")
_RE_CARGO_LOCATION = re.compile(r"-->\s*(\S+?):(\d+):(\d+)")
_RE_CARGO_TEST_FAIL = re.compile(r"^test (\S+) \.\.\. FAILED", re.MULTILINE)
_RE_CARGO_TEST_STDOUT = re.compile(r"---- (\S+) stdout ----")
_RE_RUSTFMT_DIFF = re.compile(r"^Diff in (\S+?):(\d+)?:?", re.MULTILINE)
_RE_NO_SUCH_FILE = re.compile(r"No such file or directory|can't open file|not found", re.IGNORECASE)
_RE_TIMEOUT_WORDS = re.compile(r"timed?\s*out|TimeoutExpired", re.IGNORECASE)
_RE_PERMISSION = re.compile(r"Permission denied|Access is denied|EACCES|EPERM")
_RE_NOT_ON_PATH = re.compile(r"not on PATH|not recognized as|command not found")
_RE_FYERS = re.compile(r"test_fyers_provider|reload_credentials|VAYREN_FYERS|vayren:fyers")
_RE_CONTEXT_PY = re.compile(r"scripts[/\\]context\.py")
_RE_STALE_INDEX_MOVED = re.compile(r"index moved on|index refresh failed|tree moved after scope")
_RE_STALE_TARGET_DELETED = re.compile(r"target deleted")
_RE_STALE_UNREADABLE = re.compile(r"\bunreadable\b")
_RE_CONCURRENT = re.compile(r"concurrent modification")
_RE_INPUTS_HASH = re.compile(r"inputs_hash mismatch|graph is stale|graph[/_]inputs_hash drift")
_RE_FILE_PATH = re.compile(
    r"((?:[A-Za-z]:)?[\\\w.\-]*[/\\][\w.\-]+\.(?:py|rs|slint|md|json|toml))\b"
)
_RE_TRACE_LINE = re.compile(r"(\S+\.py):(\d+):")


class IntelError(Exception):
    """Failure-intel failure — explicit, never a silent misclassification."""


# --- small helpers ----------------------------------------------------------


def _bounded(text: str, cap: int = RAW_TAIL_CAP) -> str:
    return text[-cap:] if len(text) > cap else text


def _short(value: str, cap: int = EVIDENCE_VALUE_CAP) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text[-cap:] if len(text) > cap else text


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _norm_relpath(raw: str) -> str | None:
    """Best-effort repo-relative path (forward slashes) or None."""
    cleaned = raw.replace("\\", "/").strip().strip("\"'")
    lowered = cleaned.lower()
    marker = "vayren-core/"
    if marker in lowered:
        cleaned = cleaned[lowered.index(marker) + len(marker) :]
    cleaned = cleaned.lstrip("./")
    if not cleaned or ".." in cleaned.split("/"):
        return None
    if (ROOT / cleaned).exists():
        return cleaned
    return None


def _command_family(command: str) -> str:
    first = (command.strip().split() or [""])[0].lower().rstrip(":")
    if first == "python" and "scripts/" in command.replace("\\", "/"):
        match = re.search(r"scripts[/\\]([\w.]+\.py)", command)
        if match:
            return f"python:{match.group(1)}"
    if first == "cargo":
        parts = command.strip().split()
        sub = parts[1] if len(parts) > 1 else ""
        return f"cargo:{sub}"
    return first or "unknown"


def _existing_paths(text: str, cap: int = 5) -> list[str]:
    found: list[str] = []
    for match in _RE_FILE_PATH.finditer(text):
        relpath = _norm_relpath(match.group(1))
        if relpath is not None and relpath not in found:
            found.append(relpath)
        if len(found) >= cap:
            break
    return found


def _is_success_raw(raw: dict) -> bool:
    kind = raw.get("kind", "command")
    if kind == "command":
        return raw.get("outcome") == "PASS" or raw.get("rc") == 0
    if kind == "validation":
        status = raw.get("status")
        return status == "VALID" or (status == "ESCALATED" and not raw.get("results"))
    if kind in ("surface", "freshness"):
        return raw.get("status") in ("READY", "RESOLVED", "manifest fresh")
    return False


# --- normalization (§4) ------------------------------------------------------


def normalize(raw: dict) -> dict:
    """Raw failure input -> stable skeleton (classification fills the rest).

    Accepted kinds: command | validation | surface | freshness | comparison.
    `raw` for kind=command carries: command, rc, stdout/stderr (or tail),
    note, outcome. Raw output is preserved bounded (`raw_tail`) for
    debugging; the machine classification stays compact.
    """
    if not isinstance(raw, dict):
        raise IntelError("failure input must be an object")
    kind = str(raw.get("kind", "command"))
    if kind == "command":
        return _normalize_command(raw)
    if kind == "validation":
        return _normalize_validation(raw)
    if kind == "surface":
        return _normalize_surface(raw)
    if kind == "freshness":
        return _normalize_freshness(raw)
    if kind == "comparison":
        return _normalize_comparison(raw)
    raise IntelError(f"unknown failure kind: {kind}")


def _skeleton(
    kind: str,
    command: str = "",
    exit_code: int | None = None,
    output: str = "",
    subfailures: list | None = None,
) -> dict:
    return {
        "schema": SCHEMA_NORMALIZED,
        "kind": kind,
        "command": command,
        "exit_code": exit_code,
        "output": _bounded(output),
        "raw_tail": _bounded(output),
        "subfailures": list(subfailures or []),
        "affected_files": [],
        "affected_symbols": [],
        "category": "UNKNOWN",
        "subtype": "unclassified",
        "ownership": None,
        "evidence": [],
        "confidence": 0.0,
        "retryable": False,
        "escalation_required": False,
    }


def _normalize_command(raw: dict) -> dict:
    command = str(raw.get("command", ""))
    rc = raw.get("rc")
    try:
        exit_code = int(rc) if rc is not None else None
    except (TypeError, ValueError):
        exit_code = None
    output = str(raw.get("tail") or raw.get("stderr") or "")
    stdout = str(raw.get("stdout") or "")
    if stdout and stdout not in output:
        output = f"{stdout}\n{output}"
    note = str(raw.get("note") or "")
    if note and note not in output:
        output = f"{output}\n{note}" if output else note
    record = _skeleton("command", command=command, exit_code=exit_code, output=output)
    record["phase_outcome"] = str(raw.get("outcome") or "")
    record["note"] = note
    return record


def _normalize_validation(raw: dict) -> dict:
    status = str(raw.get("status", "UNKNOWN"))
    reason = str(raw.get("reason", ""))
    record = _skeleton("validation", output=reason)
    record["validation_status"] = status
    record["scope"] = str(raw.get("scope", ""))
    record["reason_text"] = reason
    evidence = raw.get("evidence")
    record["scope_evidence"] = dict(evidence) if isinstance(evidence, dict) else {}
    impact = raw.get("impact")
    record["impact"] = dict(impact) if isinstance(impact, dict) else {}
    subfailures = []
    for entry in raw.get("results", []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("outcome") in ("FAIL", "TIMEOUT", "ENV_FAIL", "UNAVAILABLE"):
            subfailures.append(
                {
                    "command": str(entry.get("command", "")),
                    "outcome": str(entry.get("outcome", "")),
                    "rc": entry.get("rc"),
                    "duration_ms": entry.get("duration_ms", 0.0),
                }
            )
    record["subfailures"] = subfailures
    return record


def _normalize_surface(raw: dict) -> dict:
    status = str(raw.get("status", "UNKNOWN"))
    reason = str(raw.get("reason", "")) or str(raw.get("detail", ""))
    record = _skeleton("surface", output=reason)
    record["surface_status"] = status
    record["reason_text"] = reason
    return record


def _normalize_freshness(raw: dict) -> dict:
    status = str(raw.get("status", "UNKNOWN"))
    reason = str(raw.get("reason", ""))
    record = _skeleton("freshness", output=f"{status}: {reason}")
    record["freshness_status"] = status
    record["reason_text"] = reason
    return record


def _normalize_comparison(raw: dict) -> dict:
    mode = str(raw.get("mode", "incremental_full"))
    mismatches = raw.get("mismatches", []) or []
    output = "\n".join(str(m) for m in mismatches[:10])
    record = _skeleton("comparison", output=output or mode)
    record["comparison_mode"] = mode
    record["mismatches"] = [str(m) for m in mismatches]
    record["command"] = str(raw.get("command", ""))
    return record


def add_evidence(record: dict, etype: str, value: str) -> None:
    """Append one compact evidence item (bounded count and length)."""
    evidence = record.setdefault("evidence", [])
    if len(evidence) >= EVIDENCE_CAP:
        return
    evidence.append({"type": etype, "value": _short(value)})


# --- classification (§3, §5) -------------------------------------------------


def classify(record: dict, context: dict | None = None) -> dict:
    """Deterministic taxonomy A-R from command + output + scope evidence.

    Never exit-code-only: every rule requires corroborating evidence
    (pattern, state, or known signature). Known environment/baseline
    signatures are checked BEFORE any rule that could blame changed code.
    Aggregate validation results first check sub-failures for environment /
    timeout / availability states, which dominate code-blaming rules.
    """
    context = dict(context or {})
    if record.get("kind") == "validation":
        dominated = _dominant_subfailure(record, context)
        if dominated is not None:
            _apply_hit(record, dominated)
            return record
    for rule in _CLASSIFICATION_RULES:
        hit = rule(record, context)
        if hit is not None:
            _apply_hit(record, hit)
            return record
    cached = signature_lookup(record.get("output", ""), record.get("command", ""))
    if cached is not None:
        _apply_hit(
            record,
            {
                "category": cached.get("category", "UNKNOWN"),
                "subtype": cached.get("subtype", ""),
                "ownership": cached.get("ownership"),
                "confidence": 0.55,
                "evidence": [("signature", f"{cached.get('source')}:{cached.get('key')}")],
                "note": "past signature used only because no live rule matched",
            },
        )
        return record
    _apply_hit(
        record,
        {
            "category": "UNKNOWN",
            "subtype": "unmatched_output",
            "ownership": None,
            "confidence": 0.1,
            "evidence": [("exit_code", str(record.get("exit_code")))],
            "note": "no rule matched; escalate for human diagnosis",
        },
    )
    return record


def _dominant_subfailure(record: dict, context: dict) -> dict | None:
    """Environment/timeout/availability sub-states dominate aggregate results.

    A FAILED validation carrying a TIMEOUT or ambient-environment sub-failure
    must never be misclassified as a code failure of the whole change.
    """
    for sub in record.get("subfailures", []) or []:
        probe = _skeleton("command", command=str(sub.get("command", "")), exit_code=sub.get("rc"))
        probe["phase_outcome"] = str(sub.get("outcome", ""))
        probe["note"] = ""
        hit = _rule_outcome(probe, context)
        if hit is not None and hit["category"] in (
            "TIMEOUT",
            "ENVIRONMENT_FAILURE",
            "VALIDATION_UNAVAILABLE",
            "INFRASTRUCTURE_FAILURE",
        ):
            add_evidence(record, "failing_command", str(sub.get("command", "")))
            return hit
    return None


def _apply_hit(record: dict, hit: dict) -> None:
    record["category"] = hit["category"]
    record["subtype"] = hit.get("subtype", "")
    record["ownership"] = hit.get("ownership")
    record["confidence"] = float(hit.get("confidence", 0.0))
    record["retryable"] = hit["category"] in (
        "STALE_STATE",
        "MISSING_TARGET",
        "MISSING_TEST",
    ) or (hit["category"] == "ENVIRONMENT_FAILURE" and hit.get("subtype") == "transient_service")
    for etype, value in hit.get("evidence", []):
        add_evidence(record, etype, value)
    if hit.get("note"):
        add_evidence(record, "note", str(hit["note"]))
    for path in hit.get("files", []):
        if path not in record["affected_files"]:
            record["affected_files"].append(path)
    for symbol in hit.get("symbols", []):
        if symbol not in record["affected_symbols"]:
            record["affected_symbols"].append(symbol)


def _changed_set(context: dict) -> set[str]:
    changed = context.get("changed") or []
    return {str(p).replace("\\", "/") for p in changed if isinstance(p, str)}


def _rule_outcome(record: dict, _context: dict) -> dict | None:
    """Phase-6 outcome states beat output parsing (strongest evidence)."""
    outcome = str(record.get("phase_outcome") or "")
    output = record.get("output", "")
    command = record.get("command", "")
    if outcome == "TIMEOUT" or (
        record.get("exit_code") is None and _RE_TIMEOUT_WORDS.search(output)
    ):
        return {
            "category": "TIMEOUT",
            "subtype": "command_timeout",
            "ownership": "environment",
            "confidence": 0.9,
            "evidence": [("outcome", outcome or "timeout-note"), ("command", command)],
            "note": "bounded: never blindly repeated; escalate or stop",
        }
    if outcome == "ENV_FAIL":
        note = record.get("note", "")
        if _RE_PERMISSION.search(output) or _RE_PERMISSION.search(note):
            subtype, blocker = "permission_denied", "OS permission failure; retry unsafe"
        elif _RE_NOT_ON_PATH.search(output) or "runner error" in note:
            subtype, blocker = "runner_unavailable", "command runner failed at OS level"
        else:
            subtype, blocker = "runner_env", "execution environment refused the command"
        return {
            "category": "ENVIRONMENT_FAILURE",
            "subtype": subtype,
            "ownership": "environment",
            "confidence": 0.85,
            "evidence": [
                ("outcome", "ENV_FAIL"),
                ("command", command),
                ("note", note or output[:120]),
            ],
            "note": blocker,
        }
    if outcome == "UNAVAILABLE":
        return _classify_unavailable(record, command, output)
    if outcome == "INTERRUPTED":
        return {
            "category": "INFRASTRUCTURE_FAILURE",
            "subtype": "interrupted",
            "ownership": "environment",
            "confidence": 0.8,
            "evidence": [("outcome", "INTERRUPTED"), ("command", command)],
            "note": "interrupted execution is never auto-retried",
        }
    return None


def _classify_unavailable(record: dict, command: str, output: str) -> dict:
    note = record.get("note", "")
    haystack = f"{note}\n{output}"
    if _RE_CONTEXT_PY.search(haystack) or "scripts/context.py" in command:
        return {
            "category": "INFRASTRUCTURE_FAILURE",
            "subtype": "stale_context_reference",
            "ownership": "repository_infrastructure",
            "confidence": 0.95,
            "evidence": [
                ("validator", "scripts/context.py"),
                ("reason", "validator script missing: scripts/context.py"),
                ("replacement", KNOWN_REPLACEMENTS["scripts/context.py"]),
            ],
            "note": "pre-existing stale Makefile/CI reference; must not be fixed silently",
        }
    missing = re.search(r"validator script missing: (\S+)", haystack)
    if missing:
        return {
            "category": "VALIDATION_UNAVAILABLE",
            "subtype": "validator_script_missing",
            "ownership": "repository_infrastructure",
            "confidence": 0.9,
            "evidence": [("validator", missing.group(1)), ("command", command)],
        }
    binary = re.search(r"binary not on PATH: (\S+)", haystack)
    if binary:
        return {
            "category": "ENVIRONMENT_FAILURE",
            "subtype": "toolchain_binary_missing",
            "ownership": "environment",
            "confidence": 0.9,
            "evidence": [("binary", binary.group(1)), ("command", command)],
            "note": "toolchain binary absent from PATH",
        }
    return {
        "category": "VALIDATION_UNAVAILABLE",
        "subtype": "unavailable_command",
        "ownership": "toolchain",
        "confidence": 0.7,
        "evidence": [("command", command), ("note", note or "unavailable")],
    }


def _rule_surface_status(record: dict, _context: dict) -> dict | None:
    if record.get("kind") not in ("surface", "freshness", "validation"):
        return None
    status = record.get("surface_status") or record.get("freshness_status") or ""
    reason = record.get("reason_text", "")
    validation_status = record.get("validation_status", "")
    if record.get("kind") == "freshness":
        return _classify_freshness(status, reason)
    if record.get("kind") == "surface":
        return _classify_surface(status, reason)
    if validation_status == "STALE":
        if _RE_STALE_INDEX_MOVED.search(reason):
            return _stale("stale_scope", reason, "execution_surface", 0.85)
        return _stale("stale_scope", reason, "execution_surface", 0.7)
    if validation_status == "UNKNOWN":
        if _RE_STALE_INDEX_MOVED.search(reason) or "index refresh failed" in reason:
            return _stale("stale_index", reason, "graph_index", 0.85)
        if _RE_INPUTS_HASH.search(reason):
            return _stale("stale_graph", reason, "graph_index", 0.9)
        if "manifest" in reason.lower():
            return {
                "category": "UNKNOWN",
                "subtype": "manifest_unknown",
                "ownership": "execution_surface",
                "confidence": 0.5,
                "evidence": [("reason", reason)],
            }
    return None


def _stale(subtype: str, reason: str, ownership: str, confidence: float) -> dict:
    return {
        "category": "STALE_STATE",
        "subtype": subtype,
        "ownership": ownership,
        "confidence": confidence,
        "evidence": [("reason", reason)],
        "note": "refresh once, recompute, retry once — never validate as fresh",
    }


def _classify_freshness(status: str, reason: str) -> dict | None:
    if status == "STALE":
        return _stale("stale_index", reason, "graph_index", 0.9)
    if status == "REBUILD_REQUIRED":
        if "canonical" in reason or "inputs_hash" in reason or "module roots" in reason:
            return _stale("stale_graph", reason, "graph_index", 0.9)
        return _stale("stale_index", reason, "graph_index", 0.8)
    if status == "INVALID":
        return {
            "category": "VALIDATION_FAILURE",
            "subtype": "index_invalid",
            "ownership": "graph_index",
            "confidence": 0.8,
            "evidence": [("reason", reason)],
            "note": "corrupt index state is not self-healed; escalate",
        }
    return None


def _classify_surface(status: str, reason: str) -> dict | None:
    if status == "STALE":
        if _RE_CONCURRENT.search(reason):
            return {
                "category": "CONCURRENT_MODIFICATION",
                "subtype": "target_hash_mismatch",
                "ownership": "execution_surface",
                "confidence": 0.95,
                "evidence": [("reason", reason)],
                "note": "stop; regenerate from fresh state; never overwrite",
            }
        if _RE_STALE_TARGET_DELETED.search(reason):
            return {
                "category": "MISSING_TARGET",
                "subtype": "target_deleted",
                "ownership": "graph_index",
                "confidence": 0.9,
                "evidence": [("reason", reason)],
                "note": "refresh index, re-resolve once, then stop if still missing",
            }
        if _RE_STALE_UNREADABLE.search(reason):
            return {
                "category": "ENVIRONMENT_FAILURE",
                "subtype": "unreadable_target",
                "ownership": "environment",
                "confidence": 0.7,
                "evidence": [("reason", reason)],
            }
        if "index moved on" in reason:
            return _stale("stale_manifest", reason, "execution_surface", 0.9)
        if "packet build" in reason:
            return _stale("stale_packet", reason, "execution_surface", 0.85)
        return _stale("stale_surface", reason, "execution_surface", 0.7)
    if status == "BLOCKED":
        if "ownership" in reason.lower() or "must_not" in reason or "forbidden" in reason.lower():
            return {
                "category": "OWNERSHIP_FAILURE",
                "subtype": "ownership_gate_blocked",
                "ownership": "route",
                "confidence": 0.9,
                "evidence": [("reason", reason)],
                "note": "safety gate verdict; never bypassed or retried",
            }
        return {
            "category": "CONTRACT_FAILURE",
            "subtype": "safety_contract_blocked",
            "ownership": "execution_surface",
            "confidence": 0.85,
            "evidence": [("reason", reason)],
            "note": "guard verdict; never bypassed or retried",
        }
    if status == "TEST_TARGET_MISSING":
        return {
            "category": "MISSING_TEST",
            "subtype": "test_target_missing",
            "ownership": "test",
            "confidence": 0.9,
            "evidence": [("reason", reason)],
        }
    if status == "VALIDATION_UNAVAILABLE":
        return {
            "category": "VALIDATION_UNAVAILABLE",
            "subtype": "surface_unavailable",
            "ownership": "toolchain",
            "confidence": 0.8,
            "evidence": [("reason", reason)],
        }
    return None


def _rule_known_signatures(record: dict, context: dict) -> dict | None:
    """Baseline/environment signatures — checked before code-blaming rules."""
    if record.get("kind") != "command":
        # Aggregate environment dominance is handled before rule dispatch.
        return None
    command = record.get("command", "")
    output = record.get("output", "")
    exit_code = record.get("exit_code")
    if exit_code == 0:
        return None
    fyers = _match_fyers(command, output)
    if fyers is not None:
        return fyers
    if _RE_CONTEXT_PY.search(command) and (
        _RE_NO_SUCH_FILE.search(output) or "validator script missing" in output
    ):
        return _classify_unavailable(record, command, output)
    family = _command_family(command)
    if family == "pyright" and command.strip() == "pyright":
        return _match_pyright_baseline(output, context)
    if family.startswith("cargo:fmt"):
        return _match_rustfmt(output, context)
    if family.startswith("cargo") and "not on PATH" in output:
        return {
            "category": "ENVIRONMENT_FAILURE",
            "subtype": "cargo_missing",
            "ownership": "environment",
            "confidence": 0.95,
            "evidence": [("command", command), ("reason", "cargo not on PATH")],
        }
    return None


def _match_fyers(command: str, output: str) -> dict | None:
    if "test_fyers_provider" not in command.replace("\\", "/"):
        return None
    if not (_RE_ASSERT.search(output) or "FAILED" in output or "AssertionError" in output):
        return None
    if not _RE_FYERS.search(output):
        return None
    ambient = re.search(r"assert '([^']+)' == 'A-NEW'|([A-Z0-9]{4,}-[A-Z0-9-]+)", output)
    ambient_value = ""
    if ambient:
        ambient_value = ambient.group(1) or ambient.group(2) or ""
    return {
        "category": "ENVIRONMENT_FAILURE",
        "subtype": "fyers_ambient_credentials",
        "ownership": "environment",
        "confidence": 0.95,
        "evidence": [
            ("test", "test_reload_credentials_falls_back_to_layered_loader"),
            ("stderr_pattern", "AssertionError: layered loader returned ambient value"),
            (
                "environment_signature",
                f"OS vault vayren:fyers leaks ({ambient_value or 'unknown'})",
            ),
            ("file", "02_data/data/tests/test_fyers_provider.py"),
        ],
        "files": ["02_data/data/tests/test_fyers_provider.py"],
        "note": "ambient OS credential vault overrides the layered loader; "
        "changed code is not blamed; source must not be modified",
    }


def _match_pyright_baseline(output: str, context: dict) -> dict | None:
    errors: list[tuple[str, str, str]] = []
    for match in _RE_PYRIGHT_LINE.finditer(output):
        relpath = _norm_relpath(match.group(1))
        if relpath is not None:
            errors.append((relpath, match.group(2), match.group(3)))
    summary = _RE_PYRIGHT_SUMMARY.search(output)
    changed = _changed_set(context)
    blamed = sorted({path for path, _, _ in errors if path in changed})
    if blamed:
        line = next((ln for path, ln, _ in errors if path == blamed[0]), "")
        return {
            "category": "CODE_FAILURE",
            "subtype": "pyright_type_error",
            "ownership": "source_code",
            "confidence": 0.9,
            "evidence": [
                ("validator", "pyright"),
                ("failing_file", f"{blamed[0]}:{line}"),
            ],
            "files": blamed,
            "note": "type error inside the changed set; report, never auto-edit",
        }
    if errors or (summary and int(summary.group(1)) > 0):
        count = summary.group(1) if summary else str(len(errors))
        return {
            "category": "VALIDATION_FAILURE",
            "subtype": "pyright_baseline_debt",
            "ownership": "repository_infrastructure",
            "confidence": 0.85 if errors else 0.6,
            "evidence": [
                ("validator", "pyright"),
                ("baseline", f"{count} pre-existing error(s) outside the changed set"),
            ],
            "note": "baseline debt is never blamed on the current change",
        }
    return None


def _match_rustfmt(output: str, context: dict) -> dict | None:
    if re.search(r"rustfmt (failed|crashed|panicked)|error: .*rustfmt", output, re.IGNORECASE):
        return {
            "category": "TOOLCHAIN_FAILURE",
            "subtype": "rustfmt_internal_error",
            "ownership": "toolchain",
            "confidence": 0.85,
            "evidence": [
                ("validator", "cargo fmt"),
                ("reason", output.strip().splitlines()[-1] if output.strip() else ""),
            ],
            "note": "rustfmt itself errored; code is not blamed",
        }
    diffs = []
    for match in _RE_RUSTFMT_DIFF.finditer(output):
        relpath = _norm_relpath(match.group(1))
        if relpath is not None:
            diffs.append(relpath)
    if not diffs and "Diff in" not in output:
        return None
    changed = _changed_set(context)
    blamed = sorted({p for p in diffs if p in changed})
    if blamed:
        return {
            "category": "CODE_FAILURE",
            "subtype": "rustfmt_format",
            "ownership": "source_code",
            "confidence": 0.85,
            "evidence": [("validator", "cargo fmt --check"), ("failing_file", blamed[0])],
            "files": blamed,
        }
    if "could not find `Cargo.toml`" in output:
        return {
            "category": "ENVIRONMENT_FAILURE",
            "subtype": "cargo_wrong_directory",
            "ownership": "environment",
            "confidence": 0.9,
            "evidence": [
                ("validator", "cargo fmt"),
                ("reason", "Cargo.toml not found from the invocation directory"),
            ],
            "note": "cargo commands canonically run under rust/; do not blame code",
        }
    if not diffs and "Diff in" not in output:
        return None
    return {
        "category": "VALIDATION_FAILURE",
        "subtype": "rustfmt_baseline_drift",
        "ownership": "repository_infrastructure",
        "confidence": 0.8 if diffs else 0.55,
        "evidence": [
            ("validator", "cargo fmt --check"),
            ("baseline", f"format drift in {len(diffs)} file(s) outside the changed set"),
        ],
        "files": diffs,
        "note": "pre-existing rustfmt drift is never blamed on the current change",
    }


def _rule_pytest(record: dict, context: dict) -> dict | None:
    if record.get("kind") != "command":
        return None
    family = _command_family(record.get("command", ""))
    if not family.startswith("pytest"):
        return None
    output = record.get("output", "")
    command = record.get("command", "")
    exit_code = record.get("exit_code")
    if exit_code == 0:
        return None
    if exit_code == 5 or _RE_NO_TESTS.search(output):
        target = _pytest_target(command)
        return {
            "category": "MISSING_TEST",
            "subtype": "no_tests_collected",
            "ownership": "test",
            "confidence": 0.85,
            "evidence": [("command", command), ("exit_code", "5"), ("target", target or "?")],
            "files": [target] if target else [],
            "note": "refresh index, re-resolve once, then stop if still missing",
        }
    if _RE_IMPORT_ERR.search(output):
        files = _existing_paths(output)
        changed = _changed_set(context)
        blamed = sorted({p for p in files if p in changed})
        return {
            "category": "IMPORT_FAILURE",
            "subtype": "collection_import_error",
            "ownership": "test" if not blamed else "source_code",
            "confidence": 0.8,
            "evidence": [("stderr_pattern", "ModuleNotFoundError/ImportError/collection error")],
            "files": blamed or files[:3],
        }
    failed = _RE_FAILED.findall(output)
    if failed or "AssertionError" in output or "assert" in output.lower():
        files: list[str] = []
        symbols: list[str] = []
        for node in failed:
            match = _RE_PYTEST_NODE.match(node)
            if match:
                relpath = _norm_relpath(match.group(1))
                if relpath is not None and relpath not in files:
                    files.append(relpath)
                if match.group(2) not in symbols:
                    symbols.append(match.group(2))
        for path in _existing_paths(output):
            if path not in files:
                files.append(path)
        trace_lines: dict[str, str] = {}
        for match in _RE_TRACE_LINE.finditer(output):
            relpath = _norm_relpath(match.group(1))
            if relpath is not None and relpath not in trace_lines:
                trace_lines[relpath] = match.group(2)
        detail = next(iter(trace_lines.items()), None)
        evidence: list[tuple[str, str]] = [("runner", "pytest")]
        if failed:
            evidence.append(("failing_test", failed[0][:160]))
        if detail:
            evidence.append(("failing_file", f"{detail[0]}:{detail[1]}"))
        elif files:
            evidence.append(("failing_file", files[0]))
        return {
            "category": "TEST_FAILURE",
            "subtype": "assertion_failed",
            "ownership": "test",
            "confidence": 0.85 if failed else 0.6,
            "evidence": evidence,
            "files": files[:5],
            "symbols": symbols[:5],
            "note": "exact test/file/line reported; source is never auto-edited",
        }
    return None


def _command_path_token(command: str) -> str | None:
    """Explicit `.py` target named on the command line (whether or not it exists)."""
    for token in command.replace("\\", "/").split():
        cleaned = token.strip().strip("\"'").split("::", 1)[0].lstrip("./")
        if (
            cleaned.endswith(".py")
            and not cleaned.startswith("-")
            and ".." not in cleaned.split("/")
        ):
            return cleaned
    return None


def _pytest_target(command: str) -> str | None:
    candidate = _command_path_token(command)
    if candidate is None:
        return None
    return _norm_relpath(candidate)


def _rule_lint_type(record: dict, _context: dict) -> dict | None:
    if record.get("kind") != "command":
        return None
    family = _command_family(record.get("command", ""))
    output = record.get("output", "")
    if record.get("exit_code") == 0:
        return None
    if family == "ruff":
        if _RE_RUFF_SYNTAX.search(output):
            location = _RE_RUFF_LOCATION.search(output)
            path = _norm_relpath(location.group(1)) if location else None
            return {
                "category": "SYNTAX_FAILURE",
                "subtype": "ruff_parse_error",
                "ownership": "source_code",
                "confidence": 0.9,
                "evidence": [("validator", "ruff"), ("failing_file", path or "?")],
                "files": [path] if path else [],
            }
        codes = sorted(set(_RE_RUFF_CODE.findall(output)))
        location = _RE_RUFF_LOCATION.search(output)
        path = _norm_relpath(location.group(1)) if location else None
        files = _existing_paths(output)
        if codes or path or files:
            return {
                "category": "CODE_FAILURE",
                "subtype": "ruff_lint",
                "ownership": "source_code",
                "confidence": 0.85,
                "evidence": [
                    ("validator", "ruff"),
                    ("rule", ",".join(codes[:5]) or "?"),
                    ("failing_file", path or (files[0] if files else "?")),
                ],
                "files": ([path] if path else files[:3]),
            }
        return None
    if family == "pyright":
        errors = []
        for match in _RE_PYRIGHT_LINE.finditer(output):
            relpath = _norm_relpath(match.group(1))
            if relpath is not None:
                errors.append(f"{relpath}:{match.group(2)}")
        if errors:
            return {
                "category": "CODE_FAILURE",
                "subtype": "pyright_type_error",
                "ownership": "source_code",
                "confidence": 0.8,
                "evidence": [("validator", "pyright"), ("failing_file", errors[0])],
                "files": sorted({e.rsplit(":", 1)[0] for e in errors})[:5],
            }
    return None


def _rule_cargo(record: dict, _context: dict) -> dict | None:
    if record.get("kind") != "command":
        return None
    family = _command_family(record.get("command", ""))
    if not family.startswith("cargo"):
        return None
    output = record.get("output", "")
    command = record.get("command", "")
    if record.get("exit_code") == 0:
        return None
    if family == "cargo:fmt":
        return None  # handled by the known-signature rule
    if _RE_CARGO_ERROR.search(output):
        files = []
        for match in _RE_CARGO_LOCATION.finditer(output):
            relpath = _norm_relpath(match.group(1))
            if relpath is not None and relpath not in files:
                files.append(relpath)
        code = re.search(r"(error\[E\d+\])", output)
        return {
            "category": "CODE_FAILURE",
            "subtype": "cargo_compile_error",
            "ownership": "source_code",
            "confidence": 0.9 if files else 0.65,
            "evidence": [
                ("compiler", code.group(1) if code else "rustc error"),
                ("command", command),
                ("failing_file", files[0] if files else "?"),
            ],
            "files": files[:5],
        }
    failed_tests = _RE_CARGO_TEST_FAIL.findall(output)
    if failed_tests:
        scopes = _RE_CARGO_TEST_STDOUT.findall(output)
        return {
            "category": "TEST_FAILURE",
            "subtype": "cargo_test_failed",
            "ownership": "test",
            "confidence": 0.85,
            "evidence": [
                ("runner", "cargo test"),
                ("command", command),
                ("failing_test", failed_tests[0][:160]),
            ],
            "symbols": (scopes or failed_tests)[:5],
        }
    if "warning: unused" in output and record.get("exit_code") not in (None, 0):
        return {
            "category": "CODE_FAILURE",
            "subtype": "cargo_lint",
            "ownership": "source_code",
            "confidence": 0.6,
            "evidence": [("runner", "cargo"), ("command", command)],
        }
    return None


def _rule_validators(record: dict, context: dict) -> dict | None:
    if record.get("kind") != "command":
        return None
    family = _command_family(record.get("command", ""))
    if not family.startswith("python:"):
        return None
    script = family.split(":", 1)[1]
    output = record.get("output", "")
    command = record.get("command", "")
    if record.get("exit_code") == 0:
        return None
    changed = _changed_set(context)
    files = _existing_paths(output)
    blamed = sorted({p for p in files if p in changed})
    base_family = VALIDATOR_FAMILY.get(script)
    if base_family is None:
        return None
    if "Traceback (most recent call last)" in output:
        owner: str | None = (
            "graph_index"
            if script == "validate_repo_graph.py"
            else ("route" if script == "validate_routes.py" else "validation_rule")
        )
        return {
            "category": "VALIDATION_FAILURE",
            "subtype": f"{script}_crashed",
            "ownership": owner,
            "confidence": 0.85,
            "evidence": [("validator", script), ("stderr_pattern", "traceback: validator crashed")],
            "note": "the validator itself errored; the change is not blamed",
        }
    if script == "validate_repo_graph.py" and (
        _RE_INPUTS_HASH.search(output) or "graph is stale" in output
    ):
        return _stale(
            "stale_graph",
            output.strip().splitlines()[-1] if output.strip() else script,
            "graph_index",
            0.9,
        )
    if script == "validate_imports.py" and "Import validation FAILED" in output:
        owner = "source_code" if blamed else "test"
        return {
            "category": "IMPORT_FAILURE",
            "subtype": "import_rule_violation",
            "ownership": owner,
            "confidence": 0.8,
            "evidence": [("validator", script)],
            "files": blamed or files[:3],
        }
    if script == "validate_language_ownership.py" and "FAILED" in output:
        return {
            "category": "LANGUAGE_FAILURE",
            "subtype": "language_ownership_violation",
            "ownership": "source_code" if blamed else "repository_infrastructure",
            "confidence": 0.8,
            "evidence": [("validator", script)],
            "files": blamed or files[:3],
        }
    if script == "validate_authority.py" and "Authority validation FAILED" in output:
        return {
            "category": "CONTRACT_FAILURE",
            "subtype": "authority_violation",
            "ownership": "source_code" if blamed else "repository_infrastructure",
            "confidence": 0.8,
            "evidence": [("validator", script)],
            "files": blamed or files[:3],
        }
    if script == "validate_routes.py" and "Routes validation FAILED" in output:
        return {
            "category": "CONTRACT_FAILURE",
            "subtype": "routes_violation",
            "ownership": "route" if blamed else "repository_infrastructure",
            "confidence": 0.8,
            "evidence": [("validator", script)],
            "files": blamed or files[:3],
        }
    if script == "validate_structure.py" and "FAILED" in output:
        return {
            "category": "VALIDATION_FAILURE",
            "subtype": "structure_violation",
            "ownership": "source_code" if blamed else "validation_rule",
            "confidence": 0.75,
            "evidence": [("validator", script)],
            "files": blamed or files[:3],
        }
    if script == "validate_architecture_gate.py" and re.search(r"fail", output, re.IGNORECASE):
        return {
            "category": "CONTRACT_FAILURE",
            "subtype": "architecture_gate_violation",
            "ownership": "source_code" if blamed else "repository_infrastructure",
            "confidence": 0.75,
            "evidence": [("validator", script)],
            "files": blamed or files[:3],
        }
    if script == "context_engine.py" and ("CONTEXT FAIL" in output or "FAILED" in output):
        return {
            "category": "CONTRACT_FAILURE",
            "subtype": "context_check_failed",
            "ownership": "route" if blamed else "repository_infrastructure",
            "confidence": 0.7,
            "evidence": [("validator", script)],
            "files": blamed or files[:3],
        }
    return {
        "category": base_family,
        "subtype": f"{script}_nonzero",
        "ownership": "validation_rule",
        "confidence": 0.5,
        "evidence": [
            ("validator", script),
            ("command", command),
            ("exit_code", str(record.get("exit_code"))),
        ],
        "files": blamed or files[:3],
    }


def _rule_environment(record: dict, _context: dict) -> dict | None:
    if record.get("kind") != "command":
        return None
    output = record.get("output", "")
    note = record.get("note", "")
    command = record.get("command", "")
    haystack = f"{output}\n{note}"
    if record.get("exit_code") == 0 and record.get("phase_outcome") != "ENV_FAIL":
        return None
    if _RE_PERMISSION.search(haystack):
        return {
            "category": "ENVIRONMENT_FAILURE",
            "subtype": "permission_denied",
            "ownership": "environment",
            "confidence": 0.9,
            "evidence": [("stderr_pattern", "permission denied"), ("command", command)],
            "note": "OS-level refusal; source must not be modified",
        }
    if _RE_NOT_ON_PATH.search(haystack):
        binary = re.search(r"binary not on PATH: (\S+)|not recognized as[^`]*`([^`]+)`", haystack)
        return {
            "category": "ENVIRONMENT_FAILURE",
            "subtype": "binary_missing",
            "ownership": "environment",
            "confidence": 0.9,
            "evidence": [
                ("binary", binary.group(1) or binary.group(2) if binary else "?"),
                ("command", command),
            ],
        }
    if re.search(
        r"Connection (reset|refused|aborted)|Temporary failure|Network is unreachable", haystack
    ):
        return {
            "category": "ENVIRONMENT_FAILURE",
            "subtype": "transient_service",
            "ownership": "environment",
            "confidence": 0.75,
            "evidence": [("stderr_pattern", "transient network/service error")],
            "note": "only transient environment failure is retryable, exactly once",
        }
    if _RE_NO_SUCH_FILE.search(haystack) and "scripts/context.py" not in command:
        candidate = _command_path_token(command)
        if candidate is not None and not (ROOT / candidate).is_file():
            return {
                "category": "MISSING_TARGET",
                "subtype": "target_missing_on_disk",
                "ownership": "test" if "/tests/" in candidate else "source_code",
                "confidence": 0.85,
                "evidence": [
                    ("stderr_pattern", "No such file or directory"),
                    ("target", candidate),
                ],
                "files": [candidate],
            }
    return None


def _rule_comparison(record: dict, _context: dict) -> dict | None:
    if record.get("kind") != "comparison":
        return None
    mode = record.get("comparison_mode", "incremental_full")
    mismatches = record.get("mismatches", []) or []
    if mode == "repeat":
        return {
            "category": "VALIDATION_FAILURE",
            "subtype": "validator_inconsistent",
            "ownership": "validation_rule",
            "confidence": 0.85,
            "evidence": [
                ("mismatches", str(len(mismatches))),
                ("detail", "; ".join(mismatches[:3]) or "same command disagreed with itself"),
            ],
            "note": "same command, different outcomes: trust neither, escalate to L5",
        }
    return {
        "category": "VALIDATION_FAILURE",
        "subtype": "incremental_full_mismatch",
        "ownership": "validation_rule",
        "confidence": 0.9,
        "evidence": [
            ("mismatches", str(len(mismatches))),
            ("detail", "; ".join(mismatches[:3]) or "incremental differed from full"),
        ],
        "note": "incremental result is untrusted while it disagrees with full",
    }


_CLASSIFICATION_RULES = (
    _rule_outcome,
    _rule_surface_status,
    _rule_known_signatures,
    _rule_pytest,
    _rule_lint_type,
    _rule_cargo,
    _rule_validators,
    _rule_environment,
    _rule_comparison,
)


# --- attribution (§6) --------------------------------------------------------


def attribute(record: dict, context: dict | None = None) -> dict:
    """Adjust the owning layer with the fault-vs-tool rule (evidence-noted).

    A validator that crashed owns its own failure (validation_rule /
    graph_index / route). A rule that correctly fired on changed files
    attributes the fault to the change's layer. Ambient signatures keep
    their environment/toolchain owner regardless of what changed.
    """
    context = dict(context or {})
    owner = record.get("ownership")
    category = record.get("category", "UNKNOWN")
    subtype = str(record.get("subtype", ""))
    output = record.get("output", "")
    changed = _changed_set(context)
    files = [str(p) for p in record.get("affected_files", []) or []]
    blamed = [p for p in files if p in changed]

    if owner is None:
        owner = _default_owner(category, files)
        add_evidence(record, "ownership_rule", f"default owner for {category}: {owner}")

    if owner in ("environment", "toolchain", "repository_infrastructure") and (
        category in ("ENVIRONMENT_FAILURE", "TOOLCHAIN_FAILURE")
        or subtype.endswith(
            ("baseline_debt", "baseline_drift", "ambient_credentials", "stale_context_reference")
        )
    ):
        add_evidence(record, "ownership_rule", "ambient signature keeps its owner")
        record["ownership"] = owner
        return record

    if "crashed" in subtype or "Traceback (most recent call last)" in output:
        if record.get("kind") == "command" and "validate_repo_graph" in record.get("command", ""):
            record["ownership"] = "graph_index"
        elif record.get("kind") == "command" and "validate_routes" in record.get("command", ""):
            record["ownership"] = "route"
        else:
            record["ownership"] = "validation_rule"
        add_evidence(record, "ownership_rule", "crashed tool owns its own failure")
        return record

    if category in ("CODE_FAILURE", "CONTRACT_FAILURE", "LANGUAGE_FAILURE", "IMPORT_FAILURE"):
        if changed and not blamed and category != "IMPORT_FAILURE":
            record["ownership"] = "repository_infrastructure"
            add_evidence(
                record, "ownership_rule", "violation outside the changed set: pre-existing debt"
            )
        elif blamed:
            record["ownership"] = "test" if all("/tests/" in p for p in blamed) else "source_code"
            add_evidence(record, "ownership_rule", "violation inside the changed set")
        return record

    if category == "TEST_FAILURE" and owner == "test":
        add_evidence(
            record, "ownership_rule", "failing test owns the failure until proven otherwise"
        )
    record["ownership"] = owner
    return record


def _default_owner(category: str, files: list[str]) -> str:
    if category in ("STALE_STATE",):
        return "graph_index"
    if category in ("CONCURRENT_MODIFICATION", "MISSING_TARGET", "VALIDATION_UNAVAILABLE"):
        return "execution_surface"
    if category == "MISSING_TEST":
        return "test"
    if category == "TIMEOUT":
        return "environment"
    if files and all("/tests/" in p for p in files):
        return "test"
    if files:
        return "source_code"
    return "repository_infrastructure"


# --- recovery policy (§7), budgets (§8), escalation (§10) --------------------


def retry_budget(record: dict) -> int:
    """Bounded retries: every retryable path gets exactly one execution."""
    if not record.get("retryable", False):
        return 0
    category = record.get("category", "UNKNOWN")
    subtype = str(record.get("subtype", ""))
    if category in ("STALE_STATE", "MISSING_TARGET", "MISSING_TEST"):
        return 1
    if category == "ENVIRONMENT_FAILURE" and subtype == "transient_service":
        return 1
    return 0


def _policy_next_action(record: dict) -> str | None:
    category = record.get("category", "UNKNOWN")
    subtype = str(record.get("subtype", ""))
    if category == "STALE_STATE":
        return {
            "stale_index": "REFRESH_INDEX",
            "stale_graph": "REBUILD_GRAPH",
            "stale_packet": "REFRESH_PACKET",
            "stale_scope": "RECOMPUTE_SCOPE",
            "stale_manifest": "REGENERATE_EXECUTION_SURFACE",
            "stale_surface": "REFRESH_INDEX",
        }.get(subtype, "REFRESH_INDEX")
    if category == "CONCURRENT_MODIFICATION":
        return "REGENERATE_EXECUTION_SURFACE"
    if category in ("MISSING_TARGET", "MISSING_TEST"):
        return "REFRESH_INDEX"
    if category == "VALIDATION_UNAVAILABLE":
        return "ESCALATE_VALIDATION"
    if category == "TIMEOUT":
        return "ESCALATE_VALIDATION"
    if category == "ENVIRONMENT_FAILURE":
        if subtype == "transient_service":
            return "RETRY_VALIDATION"
        return "REPORT_ENVIRONMENT"
    if category in ("CODE_FAILURE", "TEST_FAILURE", "SYNTAX_FAILURE", "IMPORT_FAILURE"):
        return "REPORT_CODE_FAILURE"
    if category in ("CONTRACT_FAILURE", "OWNERSHIP_FAILURE", "LANGUAGE_FAILURE"):
        if category == "OWNERSHIP_FAILURE":
            return "STOP"
        if category == "CONTRACT_FAILURE" and record.get("subtype") == "safety_contract_blocked":
            return "STOP"
        if record.get("ownership") in ("source_code", "test", "route"):
            return "REPORT_CODE_FAILURE"
        return "STOP"
    if category == "VALIDATION_FAILURE":
        if subtype in ("index_invalid", "validator_inconsistent", "incremental_full_mismatch"):
            return "ESCALATE_VALIDATION"
        if subtype.endswith(("baseline_debt", "baseline_drift")):
            return "STOP"
        return "ESCALATE_VALIDATION"
    if category == "TOOLCHAIN_FAILURE":
        return "STOP"
    if category == "INFRASTRUCTURE_FAILURE":
        return "STOP"
    return "ESCALATE_VALIDATION"


def _policy_final(record: dict, next_action: str | None) -> str:
    category = record.get("category", "UNKNOWN")
    if next_action is None:
        return "RECOVERED"
    if next_action in ("REFRESH_INDEX", "REFRESH_PACKET", "REBUILD_GRAPH", "RECOMPUTE_SCOPE"):
        return "RETRYING"
    if next_action == "RETRY_VALIDATION":
        return "RETRYING"
    if next_action == "REGENERATE_EXECUTION_SURFACE":
        return "STOPPED" if category == "CONCURRENT_MODIFICATION" else "RETRYING"
    if next_action == "ESCALATE_VALIDATION":
        if category == "UNKNOWN":
            return "UNKNOWN"
        return "ESCALATED"
    if next_action == "REPORT_ENVIRONMENT":
        return "ENVIRONMENT_FAILURE"
    if next_action == "REPORT_CODE_FAILURE":
        return "CODE_FAILURE"
    if next_action == "STOP":
        if category in ("CONTRACT_FAILURE", "OWNERSHIP_FAILURE"):
            return "BLOCKED"
        return "STOPPED"
    return "UNKNOWN"


def _uncertainty_flags(record: dict, context: dict) -> dict:
    impact = record.get("impact", {}) or context.get("impact", {}) or {}
    if not isinstance(impact, dict):
        impact = {}
    affected = impact.get("affected_modules", []) or []
    languages = impact.get("languages", []) or []
    public = impact.get("public_changed", []) or []
    changed_raw = context.get("changed") or record.get("affected_files") or []
    changed = [str(p).replace("\\", "/") for p in changed_raw if isinstance(p, str)]
    extensions = {p.rsplit(".", 1)[-1] for p in changed if "." in p.rsplit("/", 1)[-1]}
    cross_language = len(languages) > 1 or ({"py"} & extensions and ({"rs", "slint"} & extensions))
    return {
        "cross_module": len(affected) > 1,
        "cross_language": bool(cross_language),
        "public": bool(public),
        "contract_gap": bool(context.get("contract_incomplete", False)),
        "graph_gap": bool(context.get("graph_inconsistent", False)),
        "impact_gap": bool(context.get("impact_incomplete", False))
        or bool((record.get("impact") or {}).get("incomplete")),
        "inconsistent": record.get("subtype")
        in ("validator_inconsistent", "incremental_full_mismatch"),
        "unclassified": record.get("category") == "UNKNOWN",
    }


def escalation_target(current: str | None, record: dict, context: dict | None = None) -> str | None:
    """Deterministic escalation level (never a downgrade; None = no escalation).

    L1 failure -> safe local stays; uncertain deps -> L2/L3; cross-module
    uncertainty -> L4; authority/graph inconsistency -> L5. Incremental/full
    disagreement and inconsistent validators always go L5.
    """
    context = dict(context or {})
    flags = _uncertainty_flags(record, context)
    subtype = str(record.get("subtype", ""))
    category = record.get("category", "UNKNOWN")
    if subtype in ("incremental_full_mismatch", "validator_inconsistent"):
        return "L5"
    if flags["contract_gap"] or flags["graph_gap"] or flags["inconsistent"]:
        return "L5"
    if flags["cross_module"] or flags["cross_language"] or flags["public"]:
        return "L4"
    if flags["impact_gap"]:
        if current in ("L0", None):
            return "L2"
        return "L3"
    if category in ("CODE_FAILURE", "TEST_FAILURE", "SYNTAX_FAILURE", "IMPORT_FAILURE"):
        return None
    if category in ("ENVIRONMENT_FAILURE", "TOOLCHAIN_FAILURE", "INFRASTRUCTURE_FAILURE"):
        return None
    if category in ("CONTRACT_FAILURE", "OWNERSHIP_FAILURE") and record.get("ownership") in (
        "source_code",
        "test",
        "route",
    ):
        return None
    if category == "TIMEOUT":
        return _bump(current, 1)
    if category == "VALIDATION_UNAVAILABLE":
        return current if current in LEVELS else "L2"
    if category == "STALE_STATE":
        return None
    if category in ("MISSING_TARGET", "MISSING_TEST"):
        return None
    if category == "UNKNOWN":
        return _bump(current, 1)
    if category == "VALIDATION_FAILURE":
        return _bump(current, 1)
    if category == "CONCURRENT_MODIFICATION":
        return None
    return _bump(current, 1)


def _bump(current: str | None, steps: int) -> str:
    if current not in LEVELS:
        return "L2"
    return LEVELS[min(len(LEVELS) - 1, LEVELS.index(current) + steps)]


def decide(record: dict, context: dict | None = None, chain: RecoveryChain | None = None) -> dict:
    """Classification -> exactly one NEXT_ACTION + provisional final state."""
    context = dict(context or {})
    current = context.get("scope") if context.get("scope") in LEVELS else None
    category = record.get("category", "UNKNOWN")

    retryable = category in ("STALE_STATE", "MISSING_TARGET", "MISSING_TEST") or (
        category == "ENVIRONMENT_FAILURE" and str(record.get("subtype")) == "transient_service"
    )
    record["retryable"] = retryable
    budget = retry_budget(record)
    used = chain.attempts_for(evidence_fingerprint(record)) if chain is not None else 0

    repeat = chain.has_executed(record.get("command", ""), category) if chain is not None else False
    if repeat:
        return _repeat_decision(record, context, current, used)

    next_action = _policy_next_action(record)
    if (
        retryable
        and used >= budget
        and next_action
        in (
            "REFRESH_INDEX",
            "REFRESH_PACKET",
            "REBUILD_GRAPH",
            "RECOMPUTE_SCOPE",
            "RETRY_VALIDATION",
        )
    ):
        return _repeat_decision(record, context, current, used)

    target = escalation_target(current, record, context)
    escalation_required = target is not None and target != current
    if next_action == "ESCALATE_VALIDATION" and target is None:
        target = _bump(current, 1)
        escalation_required = True
    record["escalation_required"] = escalation_required
    final = _policy_final(record, next_action)

    return {
        "schema": SCHEMA_DECISION,
        "category": category,
        "subtype": record.get("subtype", ""),
        "ownership": record.get("ownership"),
        "confidence": record.get("confidence", 0.0),
        "evidence": list(record.get("evidence", [])),
        "affected_files": list(record.get("affected_files", [])),
        "affected_symbols": list(record.get("affected_symbols", [])),
        "retryable": retryable,
        "retry_budget": budget,
        "retries_used": used,
        "escalation_required": escalation_required,
        "escalation_target": target,
        "next_action": next_action,
        "replacement": _replacement_hint(record),
        "blocker": _blocker_text(record),
        "diagnostic": _diagnostic(record, context),
        "final": final,
        "reason": _decision_reason(record, next_action, target, used, budget),
    }


def _repeat_decision(record: dict, context: dict, current: str | None, used: int) -> dict:
    """Same (command, category) already went through recovery: stop or escalate."""
    category = record.get("category", "UNKNOWN")
    if category == "STALE_STATE":
        target = _bump(current, 1)
        record["escalation_required"] = True
        record["retryable"] = False
        return {
            "schema": SCHEMA_DECISION,
            "category": category,
            "subtype": record.get("subtype", ""),
            "ownership": record.get("ownership"),
            "confidence": record.get("confidence", 0.0),
            "evidence": list(record.get("evidence", [])),
            "affected_files": list(record.get("affected_files", [])),
            "affected_symbols": list(record.get("affected_symbols", [])),
            "retryable": False,
            "retry_budget": 1,
            "retries_used": used,
            "escalation_required": True,
            "escalation_target": target,
            "next_action": "ESCALATE_VALIDATION",
            "replacement": None,
            "blocker": None,
            "diagnostic": _diagnostic(record, context),
            "final": "ESCALATED",
            "reason": "stale again after refresh: budget spent, escalate one level",
        }
    if category in ("MISSING_TARGET", "MISSING_TEST"):
        record["retryable"] = False
        record["escalation_required"] = False
        return {
            "schema": SCHEMA_DECISION,
            "category": category,
            "subtype": record.get("subtype", ""),
            "ownership": record.get("ownership"),
            "confidence": record.get("confidence", 0.0),
            "evidence": list(record.get("evidence", [])),
            "affected_files": list(record.get("affected_files", [])),
            "affected_symbols": list(record.get("affected_symbols", [])),
            "retryable": False,
            "retry_budget": 1,
            "retries_used": used,
            "escalation_required": False,
            "escalation_target": None,
            "next_action": "STOP",
            "replacement": None,
            "blocker": None,
            "diagnostic": _diagnostic(record, context),
            "final": "STOPPED",
            "reason": "target still missing after refresh + re-resolve: stop",
        }
    record["retryable"] = False
    record["escalation_required"] = False
    final = _policy_final(record, "STOP")
    return {
        "schema": SCHEMA_DECISION,
        "category": category,
        "subtype": record.get("subtype", ""),
        "ownership": record.get("ownership"),
        "confidence": record.get("confidence", 0.0),
        "evidence": list(record.get("evidence", [])),
        "affected_files": list(record.get("affected_files", [])),
        "affected_symbols": list(record.get("affected_symbols", [])),
        "retryable": False,
        "retry_budget": retry_budget(record),
        "retries_used": used,
        "escalation_required": False,
        "escalation_target": None,
        "next_action": "STOP",
        "replacement": None,
        "blocker": _blocker_text(record),
        "diagnostic": _diagnostic(record, context),
        "final": final,
        "reason": "recovery already attempted for this failure: stop, do not loop",
    }


def _replacement_hint(record: dict) -> dict | None:
    if record.get("subtype") != "stale_context_reference":
        return None
    return {
        "instead_of": "scripts/context.py",
        "candidate": KNOWN_REPLACEMENTS["scripts/context.py"],
        "source": "repository evidence: context_engine.py --check exists and passes",
        "auto_applied": False,
    }


def _blocker_text(record: dict) -> str | None:
    if record.get("category") not in ("ENVIRONMENT_FAILURE", "INFRASTRUCTURE_FAILURE"):
        return None
    notes = [e["value"] for e in record.get("evidence", []) if e.get("type") == "note"]
    base = notes[0] if notes else str(record.get("subtype", ""))
    return f"{record.get('subtype', '')}: {base}"


def _diagnostic(record: dict, context: dict) -> dict:
    diag: dict = {
        "command": record.get("command", ""),
        "exit_code": record.get("exit_code"),
        "category": record.get("category", "UNKNOWN"),
        "ownership": record.get("ownership"),
    }
    if record.get("affected_files"):
        diag["files"] = list(record["affected_files"][:5])
    if record.get("affected_symbols"):
        diag["symbols"] = list(record["affected_symbols"][:5])
    failing = [e["value"] for e in record.get("evidence", []) if e.get("type") == "failing_test"]
    if failing:
        diag["failing_test"] = failing[0]
    scope = context.get("scope") or record.get("scope", "")
    if scope:
        diag["scope"] = scope
    return diag


def _decision_reason(
    record: dict, next_action: str | None, target: str | None, used: int, budget: int
) -> str:
    parts = [f"{record.get('category')}/{record.get('subtype')}"]
    if record.get("ownership"):
        parts.append(f"owner={record['ownership']}")
    parts.append(f"confidence={record.get('confidence', 0.0):.2f}")
    if next_action:
        parts.append(f"next={next_action}")
    if target:
        parts.append(f"escalate_to={target}")
    if record.get("retryable"):
        parts.append(f"budget={used}/{budget}")
    return "; ".join(parts)


# --- failure chain (§11) + loop protection (§9) ------------------------------


def evidence_fingerprint(record: dict) -> str:
    """Semantic identity of one failure (no timestamps anywhere)."""
    material = {
        "category": record.get("category", "UNKNOWN"),
        "subtype": record.get("subtype", ""),
        "command": record.get("command", ""),
        "exit_code": record.get("exit_code"),
        "evidence": sorted(
            f"{e.get('type', '')}={e.get('value', '')}" for e in record.get("evidence", [])
        ),
    }
    return _sha_text(json.dumps(material, sort_keys=True))[:32]


class RecoveryChain:
    """Compact machine-readable recovery chain with loop protection."""

    def __init__(self) -> None:
        self.steps: list[dict] = []

    def record(
        self,
        *,
        attempt: int,
        command: str,
        category: str,
        subtype: str,
        evidence_fp: str,
        recovery: str,
        result: str,
        note: str = "",
    ) -> dict:
        step = {
            "attempt": attempt,
            "command": command,
            "category": category,
            "subtype": subtype,
            "evidence_fp": evidence_fp,
            "recovery": recovery,
            "result": result,
            "note": _short(note, 300),
        }
        self.steps.append(step)
        return step

    def loop_detected(self, evidence_fp: str) -> bool:
        """Same failure fingerprint seen before with no new evidence: STOP."""
        return any(step.get("evidence_fp") == evidence_fp for step in self.steps)

    def attempts_for(self, evidence_fp: str) -> int:
        return sum(1 for step in self.steps if step.get("evidence_fp") == evidence_fp)

    def has_executed(self, command: str, category: str) -> bool:
        return any(
            step.get("command") == command
            and step.get("category") == category
            and step.get("recovery") not in ("NONE", "DIAGNOSE")
            for step in self.steps
        )

    def escalations_for(self, command: str) -> int:
        return sum(
            1
            for step in self.steps
            if step.get("command") == command and step.get("recovery") == "ESCALATE_VALIDATION"
        )

    def chain_id(self) -> str:
        return _sha_text(json.dumps(self.steps, sort_keys=True))[:32]

    def to_dict(self) -> dict:
        return {"schema": SCHEMA_CHAIN, "chain_id": self.chain_id(), "steps": list(self.steps)}


# --- predictive precheck (§13) ------------------------------------------------


def _windows_cred_exists(target: str) -> bool | None:
    """OS-vault probe: True present / False absent / None unreadable.

    Self-contained ctypes probe — no chapter imports (scripts must never
    import chapter packages). Only positive evidence counts; unreadable
    means unknown, never absent.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        advapi = ctypes.windll.advapi32
        cred_ptr = ctypes.c_void_p()
        ok = advapi.CredReadW(target, 1, 0, ctypes.byref(cred_ptr))
        if not ok:
            return False
        with contextlib.suppress(OSError, AttributeError):
            advapi.CredFree(cred_ptr)
        return True
    except (OSError, AttributeError, ValueError):
        return None


def _fyers_ambient_present() -> tuple[bool, str]:
    """Deterministic ambient-credential proof for the known FYERS failure."""
    for key in ("VAYREN_FYERS_APP_ID", "VAYREN_FYERS_SECRET"):
        if os.environ.get(key, "").strip():
            return True, f"env:{key}"
    vault = _windows_cred_exists("vayren:fyers")
    if vault is True:
        return True, "os-vault:vayren:fyers"
    return False, ""


def precheck(commands: list[str], context: dict | None = None) -> dict:
    """Predict deterministic failures from current state (read-only).

    Only skips execution when the precheck is deterministic (unavailable
    command, stale manifest, stale index, missing target, missing binary,
    proven ambient credentials). Anything else stays runnable.
    """
    context = dict(context or {})
    predictions: list[dict] = []
    for command in commands:
        predictions.append(_precheck_one(command, context))
    skipped = [p for p in predictions if p["skip"]]
    runnable = [p["command"] for p in predictions if not p["skip"]]
    return {
        "predictions": predictions,
        "runnable": runnable,
        "skipped": [p["command"] for p in skipped],
        "precheck_skipped": len(skipped) > 0,
    }


def _precheck_one(command: str, context: dict) -> dict:
    verified = execute_surface._verify_command(command)
    if not verified.get("available", False):
        reason = str(verified.get("reason", ""))
        if "scripts/context.py" in command or "scripts/context.py" in reason:
            category, subtype, action = (
                "INFRASTRUCTURE_FAILURE",
                "stale_context_reference",
                "STOP",
            )
        elif reason.startswith("validator script missing"):
            category, subtype, action = (
                "VALIDATION_UNAVAILABLE",
                "validator_script_missing",
                "ESCALATE_VALIDATION",
            )
        elif reason.startswith("binary not on PATH"):
            category, subtype, action = (
                "ENVIRONMENT_FAILURE",
                "toolchain_binary_missing",
                "REPORT_ENVIRONMENT",
            )
        else:
            category, subtype, action = (
                "VALIDATION_UNAVAILABLE",
                "unavailable_command",
                "ESCALATE_VALIDATION",
            )
        return {
            "command": command,
            "predicted_category": category,
            "predicted_subtype": subtype,
            "skip": True,
            "next_action": action,
            "evidence": [reason],
        }
    if "test_fyers_provider" in command.replace("\\", "/"):
        present, source = _fyers_ambient_present()
        if present:
            return {
                "command": command,
                "predicted_category": "ENVIRONMENT_FAILURE",
                "predicted_subtype": "fyers_ambient_credentials",
                "skip": True,
                "next_action": "REPORT_ENVIRONMENT",
                "evidence": [f"ambient FYERS credentials proven present ({source})"],
            }
    target = _command_path_token(command)
    if target is not None and not (ROOT / target).is_file():
        return {
            "command": command,
            "predicted_category": "MISSING_TARGET",
            "predicted_subtype": "target_missing_on_disk",
            "skip": True,
            "next_action": "REFRESH_INDEX",
            "evidence": [f"target absent from tree: {target}"],
        }
    if command.strip().startswith("cargo "):
        binary = shutil.which("cargo")
        if binary is None:
            return {
                "command": command,
                "predicted_category": "ENVIRONMENT_FAILURE",
                "predicted_subtype": "cargo_missing",
                "skip": True,
                "next_action": "REPORT_ENVIRONMENT",
                "evidence": ["cargo not on PATH"],
            }
    manifest = context.get("manifest")
    if isinstance(manifest, dict) and manifest.get("hashes"):
        freshness = execute_surface.check_manifest(manifest)
        if freshness["status"] != manifest.get("status", "READY") or freshness["status"] in (
            "STALE",
            "BLOCKED",
        ):
            reason = str(freshness.get("reason", ""))
            if "concurrent modification" in reason:
                category, subtype, action = (
                    "CONCURRENT_MODIFICATION",
                    "target_hash_mismatch",
                    "REGENERATE_EXECUTION_SURFACE",
                )
            elif "target deleted" in reason:
                category, subtype, action = (
                    "MISSING_TARGET",
                    "target_deleted",
                    "REFRESH_INDEX",
                )
            else:
                category, subtype, action = (
                    "STALE_STATE",
                    "stale_manifest",
                    "REGENERATE_EXECUTION_SURFACE",
                )
            return {
                "command": command,
                "predicted_category": category,
                "predicted_subtype": subtype,
                "skip": True,
                "next_action": action,
                "evidence": [reason],
            }
    return {
        "command": command,
        "predicted_category": "",
        "predicted_subtype": "",
        "skip": False,
        "next_action": "",
        "evidence": [],
    }


# --- recovery execution -------------------------------------------------------


def _exec_refresh_index(_decision: dict, _context: dict) -> dict:
    try:
        repo_index.ensure_fresh()
    except (
        repo_index.IndexError,
        repo_index.IndexLockedError,
        repo_graph.GraphError,
        OSError,
        ValueError,
    ) as exc:
        return {"ok": False, "reason": f"refresh failed: {exc}"}
    probe = repo_index.freshness()
    if probe.get("status") != "READY":
        return {"ok": False, "reason": f"still {probe.get('status')}: {probe.get('reason')}"}
    result: dict = {"ok": True, "index_fingerprint": probe.get("fingerprint")}
    return result


def _exec_refresh_packet(_decision: dict, context: dict) -> dict:
    args = context.get("packet_args") or {}
    if not isinstance(args, dict) or not (
        args.get("task") or args.get("file") or args.get("symbol")
    ):
        return {"ok": False, "reason": "no packet targeting in context"}
    try:
        packet, _ = context_packet.build_packet(
            str(args.get("task", "")),
            use_cache=False,
            file=args.get("file"),
            symbol=args.get("symbol"),
            module=args.get("module"),
            route_key=args.get("route_key"),
            domain=args.get("domain"),
            modify=args.get("modify"),
        )
    except (context_packet.PacketError, KeyError, ValueError, OSError) as exc:
        return {"ok": False, "reason": f"packet rebuild failed: {exc}"}
    if packet.get("status") != "RESOLVED":
        return {"ok": False, "reason": f"packet {packet.get('status')}"}
    return {"ok": True, "packet_status": "RESOLVED"}


def _exec_rebuild_graph(_decision: dict, _context: dict) -> dict:
    try:
        graph, errors = repo_graph.build_graph()
    except (repo_graph.GraphError, OSError, ValueError) as exc:
        return {"ok": False, "reason": f"graph rebuild failed: {exc}"}
    if errors:
        return {"ok": False, "reason": f"graph rebuild errors: {errors[0]}"}
    try:
        repo_graph.write_graph(graph)
        repo_index.refresh()
    except (repo_graph.GraphError, repo_index.IndexError, OSError, ValueError) as exc:
        return {"ok": False, "reason": f"graph publish failed: {exc}"}
    probe = repo_index.freshness()
    if probe.get("status") != "READY":
        return {"ok": False, "reason": f"still {probe.get('status')}: {probe.get('reason')}"}
    return {"ok": True, "graph_inputs_hash": graph.get("inputs_hash", "")}


def _exec_recompute_scope(_decision: dict, context: dict) -> dict:
    manifest = context.get("manifest")
    if not isinstance(manifest, dict):
        return {"ok": False, "reason": "no manifest in context"}
    try:
        result = validate_scope.validate_manifest(manifest, run=False)
    except (validate_scope.ScopeError, ValueError, OSError) as exc:
        return {"ok": False, "reason": f"scope recompute failed: {exc}"}
    if result.get("status") not in ("VALID", "ESCALATED"):
        return {"ok": False, "reason": f"scope {result.get('status')}: {result.get('reason')}"}
    return {"ok": True, "scope": result.get("scope", ""), "commands": result.get("commands", [])}


def _exec_retry_validation(decision: dict, context: dict) -> dict:
    commands = list(decision.get("retry_commands") or [])
    if not commands and decision.get("diagnostic", {}).get("command"):
        commands = [str(decision["diagnostic"]["command"])]
    if not commands:
        return {"ok": False, "reason": "no command to retry"}
    timeout_s = int(context.get("timeout_s", execute_surface.VALIDATE_TIMEOUT_S))
    try:
        result = execute_surface.run_validation(
            {"validate": {"commands": commands}}, timeout_s=timeout_s
        )
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": f"retry runner error: {exc}"}
    failed = [r for r in result.get("results", []) if r.get("rc") not in (0, None)]
    non_skip_none = [
        r
        for r in result.get("results", [])
        if r.get("rc") is None and "skipped" not in str(r.get("note", ""))
    ]
    if failed or non_skip_none:
        detail = (failed or non_skip_none)[0]
        return {"ok": False, "reason": f"retry failed: {detail.get('command')}"}
    return {"ok": True, "retried": commands}


def _exec_escalate(decision: dict, context: dict) -> dict:
    manifest = context.get("manifest")
    target = decision.get("escalation_target")
    if not isinstance(manifest, dict) or target not in LEVELS:
        return {"ok": False, "reason": "escalation needs a manifest and a target level"}
    run = bool(context.get("revalidate", False))
    timeout_s = int(context.get("timeout_s", validate_scope.RUN_TIMEOUT_S))
    try:
        result = validate_scope.validate_manifest(
            manifest, force_level=target, run=run, timeout_s=timeout_s
        )
    except (validate_scope.ScopeError, ValueError, OSError) as exc:
        return {"ok": False, "reason": f"escalated validation failed: {exc}"}
    if run:
        if result.get("status") == "VALID":
            return {"ok": True, "recovered_at": target, "status": "VALID"}
        return {
            "ok": False,
            "reason": f"escalated run {result.get('status')}",
            "status": result.get("status"),
        }
    if result.get("status") in ("VALID", "ESCALATED"):
        return {"ok": True, "escalated_to": target, "commands": result.get("commands", [])}
    return {"ok": False, "reason": f"escalated scope {result.get('status')}"}


def _exec_regenerate_surface(_decision: dict, context: dict) -> dict:
    args = context.get("surface_args") or {}
    task = str((args.get("task") if isinstance(args, dict) else "") or context.get("task", ""))
    if not task:
        return {"ok": False, "reason": "no task in context to regenerate from"}
    try:
        manifest, _ = execute_surface.build_manifest(task, use_cache=False)
    except (ValueError, OSError) as exc:
        return {"ok": False, "reason": f"regeneration failed: {exc}"}
    if manifest.get("status") != "READY":
        return {"ok": False, "reason": f"regenerated manifest {manifest.get('status')}"}
    freshness = execute_surface.check_manifest(manifest)
    if freshness["status"] != "READY":
        return {"ok": False, "reason": f"regenerated surface not fresh: {freshness.get('reason')}"}
    return {"ok": True, "manifest_key": manifest.get("manifest_key", "")}


ACTION_EXECUTORS = {
    "REFRESH_INDEX": _exec_refresh_index,
    "REFRESH_PACKET": _exec_refresh_packet,
    "REBUILD_GRAPH": _exec_rebuild_graph,
    "RECOMPUTE_SCOPE": _exec_recompute_scope,
    "RETRY_VALIDATION": _exec_retry_validation,
    "ESCALATE_VALIDATION": _exec_escalate,
    "REGENERATE_EXECUTION_SURFACE": _exec_regenerate_surface,
}


# --- full pipeline (§2) --------------------------------------------------------


def _execute_action(
    record: dict,
    decision: dict,
    evidence_fp: str,
    context: dict,
    chain: RecoveryChain,
    revalidate: bool,
    timing: dict,
    max_attempts: int = 3,
) -> dict:
    """Execute one safe action, then verify its postcondition (bounded)."""
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        action = decision.get("next_action")
        executor = ACTION_EXECUTORS.get(str(action))
        if executor is None:
            break
        exec_started = time.perf_counter()
        outcome = executor(decision, context)
        timing[f"exec_{str(action).lower()}"] = round(
            (time.perf_counter() - exec_started) * 1000, 1
        )
        step = chain.record(
            attempt=len(chain.steps) + 1,
            command=record.get("command", ""),
            category=record.get("category", "UNKNOWN"),
            subtype=record.get("subtype", ""),
            evidence_fp=evidence_fp,
            recovery=str(action),
            result="RECOVERED" if outcome.get("ok") else "FAILED",
            note=str(outcome.get("reason", outcome.get("retried", outcome.get("scope", "")))),
        )
        decision["chain"] = chain.to_dict()
        decision["recovery_chain_step"] = step
        if outcome.get("ok"):
            if action in ("REFRESH_INDEX", "REFRESH_PACKET", "REBUILD_GRAPH"):
                decision.update(_follow_up_refresh(decision, context, revalidate))
                break
            if action == "REGENERATE_EXECUTION_SURFACE":
                decision["final"] = "RETRYING"
                decision["reason"] += "; surface regenerated — fresh execution required"
                break
            if action == "RECOMPUTE_SCOPE":
                decision["final"] = "RETRYING"
                decision["reason"] += "; scope recomputed — retry the required commands"
                break
            if action == "RETRY_VALIDATION":
                decision["final"] = "RECOVERED"
                decision["reason"] += "; retry passed"
                break
            if action == "ESCALATE_VALIDATION":
                if outcome.get("recovered_at"):
                    decision["final"] = "RECOVERED"
                    decision["reason"] += f"; escalated run passed at {outcome['recovered_at']}"
                else:
                    decision["final"] = "ESCALATED"
                    decision["reason"] += "; escalated scope computed — routed upward"
                break
            break
        decision = decide(record, context, chain)
        decision["chain"] = chain.to_dict()
        if decision.get("next_action") in (
            None,
            "STOP",
            "REPORT_ENVIRONMENT",
            "REPORT_CODE_FAILURE",
        ):
            break
        if decision.get("next_action") == action:
            decision["next_action"] = "STOP"
            decision["final"] = "STOPPED"
            decision["reason"] += "; same action repeated without new evidence"
            break
    return decision


def recover(
    raw: dict,
    *,
    context: dict | None = None,
    chain: RecoveryChain | None = None,
    apply: bool = False,
    revalidate: bool = False,
    max_attempts: int = 3,
) -> dict:
    """FAILURE -> ... -> FINAL RESULT.

    apply=False diagnoses and routes only. apply=True executes the single
    safe action (budget-checked, loop-guarded); revalidate=True additionally
    runs the required scope to verify. Terminates in at most max_attempts
    passes — recovery never recurses and never loops forever.
    """
    started = time.perf_counter()
    context = dict(context or {})
    if revalidate:
        context = {**context, "revalidate": True}
    chain = chain if chain is not None else RecoveryChain()
    timing: dict[str, float] = {}

    record = normalize(raw)
    if _is_success_raw(raw):
        return {
            "schema": SCHEMA_DECISION,
            "category": None,
            "subtype": None,
            "ownership": None,
            "confidence": 1.0,
            "evidence": [],
            "affected_files": [],
            "affected_symbols": [],
            "retryable": False,
            "retry_budget": 0,
            "retries_used": 0,
            "escalation_required": False,
            "escalation_target": None,
            "next_action": None,
            "replacement": None,
            "blocker": None,
            "diagnostic": {},
            "final": "RECOVERED",
            "reason": "no failure present in input",
            "chain": chain.to_dict(),
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 1)},
        }

    if record.get("kind") == "validation" and record.get("validation_status") == "FAILED":
        return _recover_validation(
            record, context, chain, apply, revalidate, max_attempts, started, timing
        )

    classify(record, context)
    attribute(record, context)
    evidence_fp = evidence_fingerprint(record)

    if chain.loop_detected(evidence_fp):
        step = chain.record(
            attempt=len(chain.steps) + 1,
            command=record.get("command", ""),
            category=record.get("category", "UNKNOWN"),
            subtype=record.get("subtype", ""),
            evidence_fp=evidence_fp,
            recovery="STOP",
            result="STOPPED",
            note="RECOVERY_LOOP_DETECTED",
        )
        decision = decide(record, context, chain)
        decision["next_action"] = "STOP"
        decision["final"] = "STOPPED"
        decision["reason"] = "RECOVERY_LOOP_DETECTED: same failure without new evidence"
        decision["chain"] = chain.to_dict()
        decision["recovery_chain_step"] = step
        decision["timing_ms"] = {
            **timing,
            "total": round((time.perf_counter() - started) * 1000, 1),
        }
        return decision

    decision = decide(record, context, chain)
    decision["chain"] = chain.to_dict()

    if not apply or decision.get("next_action") in (
        None,
        "STOP",
        "REPORT_ENVIRONMENT",
        "REPORT_CODE_FAILURE",
    ):
        decision["timing_ms"] = {
            **timing,
            "total": round((time.perf_counter() - started) * 1000, 1),
        }
        return decision

    decision = _execute_action(
        record, decision, evidence_fp, context, chain, revalidate, timing, max_attempts
    )
    decision["timing_ms"] = {**timing, "total": round((time.perf_counter() - started) * 1000, 1)}
    return decision


def _follow_up_refresh(decision: dict, context: dict, revalidate: bool) -> dict:
    """After a successful refresh: recompute scope, optionally retry commands."""
    if "manifest" not in context:
        return {
            "final": "RETRYING",
            "reason": decision.get("reason", "") + "; refreshed — recompute scope next",
        }
    recompute = _exec_recompute_scope(decision, context)
    if not recompute.get("ok"):
        return {
            "final": "ESCALATED",
            "reason": decision.get("reason", "") + f"; refreshed but {recompute.get('reason')}",
        }
    if not revalidate:
        return {
            "final": "RETRYING",
            "reason": decision.get("reason", "") + "; refreshed + scope recomputed — retry pending",
        }
    retry_decision = dict(decision)
    retry_decision["retry_commands"] = _failed_commands(context)
    retry = _exec_retry_validation(retry_decision, context)
    if retry.get("ok"):
        return {
            "final": "RECOVERED",
            "reason": decision.get("reason", "") + "; refreshed, rescoped, retry passed",
        }
    return {
        "final": "ESCALATED",
        "reason": decision.get("reason", "") + "; refreshed but retry still failing — escalate",
    }


def _failed_commands(context: dict) -> list[str]:
    commands = context.get("failed_commands")
    if isinstance(commands, list):
        return [str(c) for c in commands if isinstance(c, str)]
    return []


def _recover_validation(
    record: dict,
    context: dict,
    chain: RecoveryChain,
    apply: bool,
    revalidate: bool,
    max_attempts: int,
    started: float,
    timing: dict,
) -> dict:
    """FAILED aggregate result: per-command diagnosis, single combined action."""
    diagnosed: list[tuple[dict, dict]] = []
    for sub in record.get("subfailures", []) or []:
        raw = {
            "kind": "command",
            "command": sub.get("command", ""),
            "rc": sub.get("rc"),
            "tail": "",
            "note": "",
            "outcome": sub.get("outcome", ""),
        }
        hydrated = _hydrate_from_cache(raw, context)
        sub_record = normalize(hydrated)
        classify(sub_record, context)
        attribute(sub_record, context)
        diagnosed.append((sub_record, decide(sub_record, context, chain)))
    if not diagnosed:
        decision = decide(record, context, chain)
        decision["chain"] = chain.to_dict()
        decision["timing_ms"] = {
            **timing,
            "total": round((time.perf_counter() - started) * 1000, 1),
        }
        return decision
    winner_record, winner = min(
        diagnosed, key=lambda pair: FINAL_PRIORITY.index(pair[1].get("final", "UNKNOWN"))
    )
    combined = dict(winner)
    combined["subdecisions"] = [
        {
            "command": decision.get("diagnostic", {}).get("command", ""),
            "category": decision.get("category"),
            "next_action": decision.get("next_action"),
            "final": decision.get("final"),
        }
        for _, decision in diagnosed
    ]
    retry_cmds = [
        str(decision.get("diagnostic", {}).get("command", ""))
        for _, decision in diagnosed
        if decision.get("next_action") == "RETRY_VALIDATION"
        and decision.get("diagnostic", {}).get("command")
    ]
    if retry_cmds:
        combined["retry_commands"] = retry_cmds
    if not apply or combined.get("next_action") in (
        None,
        "STOP",
        "REPORT_ENVIRONMENT",
        "REPORT_CODE_FAILURE",
    ):
        combined["chain"] = chain.to_dict()
        combined["timing_ms"] = {
            **timing,
            "total": round((time.perf_counter() - started) * 1000, 1),
        }
        return combined
    evidence_fp = evidence_fingerprint(winner_record)
    if chain.loop_detected(evidence_fp):
        combined["next_action"] = "STOP"
        combined["final"] = "STOPPED"
        combined["reason"] = "RECOVERY_LOOP_DETECTED: same failure without new evidence"
        combined["chain"] = chain.to_dict()
        combined["timing_ms"] = {
            **timing,
            "total": round((time.perf_counter() - started) * 1000, 1),
        }
        return combined
    combined = _execute_action(
        winner_record, combined, evidence_fp, context, chain, revalidate, timing, max_attempts
    )
    combined["timing_ms"] = {**timing, "total": round((time.perf_counter() - started) * 1000, 1)}
    return combined


def _hydrate_from_cache(raw: dict, context: dict) -> dict:
    """Pull bounded tails/notes for failed commands from the Phase-6 cache.

    validate_manifest results strip tails; the cache records keep the last
    2000 chars. Best effort — a cache miss leaves classification to command
    family + exit code (medium confidence), never invented output.
    """
    scope = context.get("scope_dict")
    graph = context.get("graph")
    if not isinstance(scope, dict) or not isinstance(graph, dict):
        return raw
    try:
        hit = validate_scope.cache_lookup(str(raw.get("command", "")), scope, graph)
    except (OSError, ValueError):
        return raw
    if not isinstance(hit, dict):
        return raw
    enriched = dict(raw)
    if hit.get("tail"):
        enriched["tail"] = str(hit["tail"])
    if hit.get("note"):
        enriched["note"] = str(hit["note"])
    return enriched


# --- signature cache (§18) ----------------------------------------------------

_SIG_MEMO: dict[str, tuple[float, dict]] = {}

KNOWN_SIGNATURES: list[dict] = [
    {
        "key": "fyers_ambient",
        "pattern": r"test_fyers_provider.*AssertionError|AssertionError.*layered loader",
        "category": "ENVIRONMENT_FAILURE",
        "subtype": "fyers_ambient_credentials",
        "ownership": "environment",
        "retry_policy": "never",
        "escalation_policy": "none",
    },
    {
        "key": "stale_context_reference",
        "pattern": r"scripts[/\\]context\.py.*(?:missing|No such file|can't open)",
        "category": "INFRASTRUCTURE_FAILURE",
        "subtype": "stale_context_reference",
        "ownership": "repository_infrastructure",
        "retry_policy": "never",
        "escalation_policy": "none",
    },
    {
        "key": "concurrent_modification",
        "pattern": r"concurrent modification",
        "category": "CONCURRENT_MODIFICATION",
        "subtype": "target_hash_mismatch",
        "ownership": "execution_surface",
        "retry_policy": "never",
        "escalation_policy": "none",
    },
    {
        "key": "stale_index_moved",
        "pattern": r"index moved on|index refresh failed",
        "category": "STALE_STATE",
        "subtype": "stale_index",
        "ownership": "graph_index",
        "retry_policy": "once",
        "escalation_policy": "plus-one",
    },
    {
        "key": "graph_inputs_drift",
        "pattern": r"inputs_hash mismatch|graph is stale",
        "category": "STALE_STATE",
        "subtype": "stale_graph",
        "ownership": "graph_index",
        "retry_policy": "once",
        "escalation_policy": "plus-one",
    },
    {
        "key": "command_timeout",
        "pattern": r"timed?\s*out|TimeoutExpired",
        "category": "TIMEOUT",
        "subtype": "command_timeout",
        "ownership": "environment",
        "retry_policy": "never",
        "escalation_policy": "plus-one",
    },
]


def _sig_cache_path() -> Path:
    return SIG_DIR / "signatures.json"


def _sig_cache_load() -> dict:
    try:
        mtime = _sig_cache_path().stat().st_mtime
    except OSError:
        return {}
    memo = _SIG_MEMO.get("file")
    if memo is not None and memo[0] == mtime:
        return memo[1]
    try:
        data = json.loads(_sig_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    _SIG_MEMO["file"] = (mtime, entries if isinstance(entries, dict) else {})
    return _SIG_MEMO["file"][1]


def signature_lookup(text: str, command: str = "") -> dict | None:
    """Static signatures first, then cached past observations.

    The persistent cache never overrides stronger current evidence: it is
    consulted only when no static signature matches, and only fills gaps.
    """
    haystack = f"{command}\n{text}"
    for entry in KNOWN_SIGNATURES:
        if re.search(entry["pattern"], haystack, re.IGNORECASE | re.DOTALL):
            return {**entry, "source": "static"}
    for key, entry in _sig_cache_load().items():
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("evidence_pattern", ""))
        if pattern and re.search(pattern, haystack, re.IGNORECASE | re.DOTALL):
            return {**entry, "key": key, "source": "cache"}
    return None


def signature_record(entry: dict) -> dict:
    """Store one observed signature (bounded, atomic, no timestamps)."""
    required = ("key", "evidence_pattern", "category")
    if any(not entry.get(field) for field in required):
        raise IntelError("signature entry needs key, evidence_pattern, category")
    if entry.get("category") not in CATEGORIES:
        raise IntelError(f"unknown category: {entry.get('category')}")
    stored = {
        "key": str(entry["key"]),
        "evidence_pattern": str(entry["evidence_pattern"]),
        "category": entry["category"],
        "subtype": str(entry.get("subtype", "")),
        "ownership": entry.get("ownership") if entry.get("ownership") in OWNERSHIP else None,
        "retry_policy": str(entry.get("retry_policy", "never")),
        "escalation_policy": str(entry.get("escalation_policy", "none")),
    }
    SIG_DIR.mkdir(parents=True, exist_ok=True)
    entries = dict(_sig_cache_load())
    entries[stored["key"]] = stored
    while len(entries) > SIG_KEEP:
        entries.pop(next(iter(entries)))
    payload = {"schema": SIG_SCHEMA, "entries": entries}
    tmp = _sig_cache_path().with_name(f"signatures.json.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, _sig_cache_path())
    _SIG_MEMO.pop("file", None)
    return {"stored": True, "key": stored["key"]}


# --- benchmark (§20) ----------------------------------------------------------


def _bench_case(
    name: str,
    raw: dict,
    context: dict | None = None,
    *,
    apply: bool = False,
    chain: RecoveryChain | None = None,
) -> dict:
    started = time.perf_counter()
    result = recover(raw, context=context, chain=chain or RecoveryChain(), apply=apply)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "case": name,
        "category": result.get("category"),
        "subtype": result.get("subtype"),
        "ownership": result.get("ownership"),
        "evidence_items": len(result.get("evidence", [])),
        "retryable": result.get("retryable"),
        "retry_budget": result.get("retry_budget"),
        "next_action": result.get("next_action"),
        "escalation_target": result.get("escalation_target"),
        "final": result.get("final"),
        "confidence": result.get("confidence"),
        "duration_ms": duration_ms,
    }


def benchmark() -> dict:
    """Deterministic 15-case matrix (synthetic inputs, no tree mutation)."""
    out: dict = {"cases": {}, "timing_ms": {}, "counts": {}}
    timed: dict[str, float] = {}

    started = time.perf_counter()
    out["cases"]["01_environment_fyers"] = _bench_case(
        "01_environment_fyers",
        {
            "kind": "command",
            "command": _FYERS_COMMAND,
            "rc": 1,
            "tail": "E       AssertionError: assert 'H8TZYVITK2-200' == 'A-NEW'\n"
            "FAILED 02_data/data/tests/test_fyers_provider.py::"
            "test_reload_credentials_falls_back_to_layered_loader",
            "outcome": "FAIL",
        },
    )
    timed["classify_cold"] = round((time.perf_counter() - started) * 1000, 1)

    out["cases"]["02_code_test"] = _bench_case(
        "02_code_test",
        {
            "kind": "command",
            "command": "pytest 09_broker/broker/tests/test_registry.py::test_register -q",
            "rc": 1,
            "tail": "FAILED 09_broker/broker/tests/test_registry.py::test_register - "
            "AssertionError: assert None is not None\n"
            "09_broker/broker/tests/test_registry.py:42: AssertionError",
            "outcome": "FAIL",
        },
        {"changed": ["09_broker/broker/registry.py"]},
    )

    out["cases"]["03_stale_packet"] = _bench_case(
        "03_stale_packet",
        {"kind": "surface", "status": "STALE", "reason": "target changed during packet build"},
    )

    out["cases"]["04_stale_index"] = _bench_case(
        "04_stale_index",
        {"kind": "freshness", "status": "STALE", "reason": "3 source file(s) changed"},
    )

    out["cases"]["05_concurrent"] = _bench_case(
        "05_concurrent",
        {
            "kind": "surface",
            "status": "STALE",
            "reason": "STALE_EXECUTION_SURFACE: concurrent modification",
        },
    )

    out["cases"]["06_missing_target"] = _bench_case(
        "06_missing_target",
        {"kind": "surface", "status": "STALE", "reason": "STALE_EXECUTION_SURFACE: target deleted"},
    )

    out["cases"]["07_missing_test"] = _bench_case(
        "07_missing_test",
        {
            "kind": "command",
            "command": "pytest 09_broker/broker/tests/test_ghost.py -q",
            "rc": 5,
            "tail": "collected 0 items / 1 error\nno tests ran",
            "outcome": "FAIL",
        },
    )

    out["cases"]["08_unavailable"] = _bench_case(
        "08_unavailable",
        {
            "kind": "command",
            "command": "python scripts/context.py --check",
            "rc": None,
            "tail": "",
            "note": "validator script missing: scripts/context.py",
            "outcome": "UNAVAILABLE",
        },
    )

    out["cases"]["09_timeout"] = _bench_case(
        "09_timeout",
        {
            "kind": "command",
            "command": "pytest 02_data/data/tests -q",
            "rc": None,
            "tail": "",
            "note": "timed out after 240s",
            "outcome": "TIMEOUT",
        },
        {"scope": "L2"},
    )

    out["cases"]["10_cross_module"] = _bench_case(
        "10_cross_module",
        {
            "kind": "command",
            "command": "pytest 09_broker/broker/tests/test_registry.py -q",
            "rc": 1,
            "tail": "FAILED 09_broker/broker/tests/test_registry.py::test_x - assert 1 == 2",
            "outcome": "FAIL",
        },
        {"impact": {"affected_modules": ["broker", "data"], "languages": ["python"]}},
    )

    out["cases"]["11_cross_language"] = _bench_case(
        "11_cross_language",
        {
            "kind": "command",
            "command": "cargo test -p vayren-core --lib aggregate",
            "rc": 101,
            "tail": "test aggregate::tests::sums ... FAILED\nfailures:\n    aggregate::tests::sums",
            "outcome": "FAIL",
        },
        {"impact": {"affected_modules": ["core"], "languages": ["python", "rust"]}},
    )

    loop_chain = RecoveryChain()
    loop_raw = {
        "kind": "command",
        "command": "pytest x -q",
        "rc": 1,
        "tail": "FAILED x.py::test_y - assert False",
        "outcome": "FAIL",
    }
    loop_started = time.perf_counter()
    first_loop = recover(loop_raw, chain=loop_chain)
    loop_record = normalize(loop_raw)
    classify(loop_record)
    attribute(loop_record)
    # Simulate the completed first attempt in the machine chain. The second
    # identical failure therefore arrives with no new evidence.
    loop_chain.record(
        attempt=len(loop_chain.steps) + 1,
        command=loop_raw["command"],
        category=str(first_loop.get("category")),
        subtype=str(first_loop.get("subtype")),
        evidence_fp=evidence_fingerprint(loop_record),
        recovery=str(first_loop.get("next_action")),
        result=str(first_loop.get("final")),
    )
    second_loop = recover(loop_raw, chain=loop_chain)
    out["cases"]["12_repeated"] = {
        "case": "12_repeated",
        "category": second_loop.get("category"),
        "subtype": second_loop.get("subtype"),
        "ownership": second_loop.get("ownership"),
        "evidence_items": len(second_loop.get("evidence", [])),
        "retryable": second_loop.get("retryable"),
        "retry_budget": second_loop.get("retry_budget"),
        "next_action": second_loop.get("next_action"),
        "escalation_target": second_loop.get("escalation_target"),
        "final": second_loop.get("final"),
        "confidence": second_loop.get("confidence"),
        "attempts": len(loop_chain.steps),
        "first_final": first_loop.get("final"),
        "reason": second_loop.get("reason"),
        "duration_ms": round((time.perf_counter() - loop_started) * 1000, 1),
    }

    out["cases"]["13_validator_inconsistent"] = _bench_case(
        "13_validator_inconsistent",
        {"kind": "comparison", "mode": "repeat", "mismatches": ["ruff: pass then fail"]},
    )

    out["cases"]["14_incremental_full_mismatch"] = _bench_case(
        "14_incremental_full_mismatch",
        {
            "kind": "comparison",
            "mode": "incremental_full",
            "mismatches": ["pytest broker: incr=PASS full=FAIL"],
        },
    )

    out["cases"]["15_unknown"] = _bench_case(
        "15_unknown",
        {
            "kind": "command",
            "command": "frobnicate --warp 9",
            "rc": 42,
            "tail": "wibbly wobbly drive disengaged",
            "outcome": "FAIL",
        },
    )

    started = time.perf_counter()
    recover(
        {
            "kind": "command",
            "command": "pytest x -q",
            "rc": 1,
            "tail": "FAILED x.py::test_y",
            "outcome": "FAIL",
        }
    )
    timed["classify_warm"] = round((time.perf_counter() - started) * 1000, 1)

    started = time.perf_counter()
    decide(
        classify(
            normalize(
                {
                    "kind": "command",
                    "command": "pytest x -q",
                    "rc": 1,
                    "tail": "FAILED x.py::test_y",
                    "outcome": "FAIL",
                }
            )
        )
    )
    timed["decide"] = round((time.perf_counter() - started) * 1000, 1)

    started = time.perf_counter()
    precheck(["pytest 09_broker/broker/tests -q", "python scripts/context.py --check"])
    timed["precheck"] = round((time.perf_counter() - started) * 1000, 1)

    started = time.perf_counter()
    escalation_target(
        "L1",
        {
            "category": "TEST_FAILURE",
            "subtype": "assertion_failed",
            "impact": {"affected_modules": ["a", "b"]},
        },
        {"impact": {"affected_modules": ["a", "b"]}},
    )
    timed["escalate"] = round((time.perf_counter() - started) * 1000, 1)

    started = time.perf_counter()
    signature_lookup(
        "AssertionError: layered loader returned ambient value", "pytest test_fyers_provider -q"
    )
    timed["sig_cache_cold"] = round((time.perf_counter() - started) * 1000, 1)
    started = time.perf_counter()
    signature_lookup(
        "AssertionError: layered loader returned ambient value", "pytest test_fyers_provider -q"
    )
    timed["sig_cache_hit"] = round((time.perf_counter() - started) * 1000, 1)

    finals: dict[str, int] = {}
    for case in out["cases"].values():
        finals[str(case.get("final"))] = finals.get(str(case.get("final")), 0) + 1
    out["timing_ms"] = timed
    out["counts"] = {"cases": len(out["cases"]), "finals": finals}
    return out


# --- CLI ---------------------------------------------------------------------


def render_text(result: dict) -> str:
    lines = [
        f"category: {result.get('category')}/{result.get('subtype')}",
        f"ownership: {result.get('ownership')} (confidence {result.get('confidence', 0.0):.2f})",
        f"next: {result.get('next_action')} -> final {result.get('final')}",
    ]
    if result.get("escalation_target"):
        lines.append(f"escalate to: {result['escalation_target']}")
    if result.get("replacement"):
        lines.append(
            f"replacement hint: {result['replacement'].get('candidate')} (not auto-applied)"
        )
    if result.get("blocker"):
        lines.append(f"blocker: {result['blocker']}")
    for sub in result.get("subdecisions", []) or []:
        lines.append(f"  - {sub.get('command')}: {sub.get('category')} -> {sub.get('final')}")
    if result.get("reason"):
        lines.append(f"reason: {result['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Predictive failure recovery + auto-escalation")
    parser.add_argument("--command", default=None, help="failed command text")
    parser.add_argument("--rc", default=None, help="exit code (empty = none)")
    parser.add_argument("--tail", default="", help="inline output tail")
    parser.add_argument("--tail-file", default=None, help="file holding the output tail")
    parser.add_argument("--outcome", default="", help="Phase-6 outcome (FAIL/TIMEOUT/ENV_FAIL/...)")
    parser.add_argument("--note", default="", help="runner note")
    parser.add_argument("--result", default=None, help="validate_scope result JSON file")
    parser.add_argument("--recover", action="store_true", help="execute the safe recovery action")
    parser.add_argument(
        "--revalidate", action="store_true", help="run the required scope to verify"
    )
    parser.add_argument("--task", default="", help="task for predictive precheck")
    parser.add_argument("--precheck", action="store_true", help="predict failures before running")
    parser.add_argument("--commands", default="", help="semicolon-separated commands to precheck")
    parser.add_argument("--timeout-s", default=240, type=int, help="retry/escalation timeout")
    parser.add_argument("--bench", action="store_true", help="15-case benchmark matrix")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    if args.bench:
        print(json.dumps(benchmark(), indent=2, sort_keys=True))
        return 0

    if args.precheck:
        commands = [c.strip() for c in args.commands.split(";") if c.strip()]
        context: dict = {"timeout_s": args.timeout_s}
        if args.task and not commands:
            try:
                manifest, _ = execute_surface.build_manifest(args.task)
                scoped = validate_scope.validate_manifest(manifest, run=False)
                commands = list(scoped.get("commands", []))
                context["manifest"] = manifest
            except (ValueError, OSError) as exc:
                print(json.dumps({"error": f"precheck manifest failed: {exc}"}))
                return 2
        result = precheck(commands, context)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            for pred in result["predictions"]:
                mark = "SKIP" if pred["skip"] else "RUN "
                print(f"{mark} {pred['command']}")
                if pred["skip"]:
                    print(f"     predicted {pred['predicted_category']} -> {pred['next_action']}")
        return 0

    if args.result:
        try:
            raw = json.loads(Path(args.result).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(json.dumps({"final": "UNKNOWN", "reason": f"cannot load result: {exc}"}))
            return 2
        if not isinstance(raw, dict):
            print(json.dumps({"final": "UNKNOWN", "reason": "result is not an object"}))
            return 2
        result = recover(
            raw,
            context={"timeout_s": args.timeout_s},
            apply=args.recover,
            revalidate=args.revalidate,
        )
    elif args.command:
        tail = args.tail
        if args.tail_file:
            try:
                tail = Path(args.tail_file).read_text(encoding="utf-8")
            except OSError as exc:
                print(json.dumps({"final": "UNKNOWN", "reason": f"cannot read tail: {exc}"}))
                return 2
        try:
            rc = int(args.rc) if args.rc not in (None, "") else None
        except ValueError:
            print(json.dumps({"final": "UNKNOWN", "reason": f"bad --rc: {args.rc}"}))
            return 2
        result = recover(
            {
                "kind": "command",
                "command": args.command,
                "rc": rc,
                "tail": tail,
                "note": args.note,
                "outcome": args.outcome,
            },
            context={"timeout_s": args.timeout_s},
            apply=args.recover,
            revalidate=args.revalidate,
        )
    else:
        parser.print_help()
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_text(result))
    return {
        "RECOVERED": 0,
        "RETRYING": 0,
        "ESCALATED": 0,
        "BLOCKED": 1,
        "STOPPED": 1,
        "ENVIRONMENT_FAILURE": 1,
        "CODE_FAILURE": 1,
        "UNKNOWN": 2,
    }.get(str(result.get("final")), 2)


if __name__ == "__main__":
    raise SystemExit(main())
