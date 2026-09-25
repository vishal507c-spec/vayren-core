"""Measurement-driven self-optimizing AI development loop (Phase 8, FINAL).

Pipeline:

    TASK -> EXECUTE -> MEASURE -> ANALYZE -> OPTIMIZATION CANDIDATE
    -> SAFE CHANGE -> BENCHMARK -> ACCEPT / REJECT -> VERSIONED KNOWLEDGE

The optimizer NEVER modifies production source code. It optimizes
AI-development metadata/infrastructure only (packet level preference,
cache reuse, memoized scope/decision lookups), subject to explicit safety
gates. Every accepted optimization is versioned and reversible; every
rejected one records its reason. Learned behavior can only suggest,
accelerate, or prioritize — canonical routing, ownership, contracts,
architecture boundaries, and required governance validation remain
authoritative and are verified byte-for-byte on every acceptance.

Usage:
    python scripts/self_opt.py --telemetry
    python scripts/self_opt.py --baseline
    python scripts/self_opt.py --candidates
    python scripts/self_opt.py --evaluate <id>
    python scripts/self_opt.py --accept <id> | --reject <id> | --rollback <id>
    python scripts/self_opt.py --benchmark
    python scripts/self_opt.py --report
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import context_packet  # noqa: E402
import execute_surface  # noqa: E402
import failure_intel  # noqa: E402
import patch_surface  # noqa: E402
import repo_index  # noqa: E402
import route  # noqa: E402
import validate_scope  # noqa: E402

ROOT = SCRIPTS_DIR.parent
STORE_DIR = repo_index.INDEX_DIR / "optimization"

SCHEMA_TELEMETRY = "telemetry/v1"
SCHEMA_BASELINE = "opt-baseline/v1"
SCHEMA_STORE = "optimization-store/v1"
BASELINE_ID = "BASELINE_V1"
TELEMETRY_KEEP = 500
MIN_OBSERVATIONS = 3
SAMPLES = 5

GOLDEN_TASKS: tuple[dict, ...] = (
    {
        "name": "strategy-param",
        "task_class": "strategy",
        "task": "add strategy parameter",
        "expect_route": "strategy-change",
    },
    {
        "name": "broker-registry",
        "task_class": "broker",
        "task": "add validation to broker registry",
        "expect_route": "broker-ubl",
    },
    {
        "name": "market-bar",
        "task_class": "market",
        "file": "03_market/market/models/bar.py",
        "expect_route": "market-read",
    },
    {
        "name": "backtest-drawdown",
        "task_class": "backtest",
        "task": "backtest drawdown",
        "expect_route": "backtest",
    },
    {"name": "ui-screen", "task_class": "ui", "task": "market screen", "expect_route": "ui-screen"},
    {"name": "ai-boundary", "task_class": "ai", "task": "ai boundary", "expect_route": "core-ai"},
    {
        "name": "data-settings",
        "task_class": "data",
        "task": "download settings",
        "expect_route": "download-config",
    },
    {
        "name": "cross-module-provider",
        "task_class": "cross-module",
        "file": "02_data/data/provider/factory.py",
        "expect_route": "provider-sdk",
    },
    {
        "name": "cross-language-timeframe",
        "task_class": "cross-language",
        "task": "add timeframe",
        "expect_route": "market-read",
    },
    {
        "name": "failure-known-unavailable",
        "task_class": "failure",
        "failure_command": "python scripts/context.py --check",
        "expect_category": "INFRASTRUCTURE_FAILURE",
    },
)

SAFETY_INVARIANTS = (
    "one responsibility -> one owner",
    "Rust/Python/Slint ownership",
    "canonical routing",
    "canonical contracts",
    "architecture boundaries",
    "forbidden dependencies",
    "required governance validation",
    "stale-state protection",
    "execution hash protection",
    "no arbitrary commands",
    "no infinite retry",
    "no automatic source repair",
)

# Canonical fields an optimization result must preserve byte-for-byte.
PROTECTED_FIELDS = ("route", "owner", "language", "forbidden", "validation_commands")

# Learned parameters may only carry these keys — never canonical authority.
LEARNABLE_KEYS = ("packet_level", "use_cache", "scope_memo", "decision_memo")

LEVELS = ("L0", "L1", "L2", "L3")


class OptError(Exception):
    """Self-optimization failure — explicit, never a silent optimization."""


# --- store -------------------------------------------------------------------


def _store_path(name: str) -> Path:
    return STORE_DIR / name


def _load_json(name: str, default: object) -> object:
    path = _store_path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        quarantine = path.with_name(f"{path.name}.corrupt-{os.getpid()}")
        with contextlib.suppress(OSError):
            os.replace(path, quarantine)
        return default
    return data


def _store_json(name: str, payload: object) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _store_path(name)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _warm_index() -> None:
    """Serve-path warmup (noop when READY; heals STALE before measuring)."""
    with contextlib.suppress(
        repo_index.IndexError, repo_index.IndexLockedError, OSError, ValueError
    ):
        repo_index.ensure_fresh()


def _blank_store() -> dict:
    return {"schema": SCHEMA_STORE, "optimizations": {}, "memos": {"scope": {}, "decision": {}}}


def load_store() -> dict:
    data = _load_json("optimizations.json", None)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_STORE:
        return _blank_store()
    store = _blank_store()
    optimizations = data.get("optimizations")
    if isinstance(optimizations, dict):
        store["optimizations"] = optimizations
    memos = data.get("memos")
    if isinstance(memos, dict):
        for key in ("scope", "decision"):
            section = memos.get(key)
            if isinstance(section, dict):
                store["memos"][key] = section
    return store


def save_store(store: dict) -> None:
    _store_json("optimizations.json", store)


def active_candidates(store: dict) -> list[dict]:
    """Accepted, non-rolled-back optimizations created from the live baseline."""
    out = []
    for opt in store.get("optimizations", {}).values():
        if not isinstance(opt, dict):
            continue
        if opt.get("status") != "ACCEPTED":
            continue
        if opt.get("created_from") != BASELINE_ID:
            continue
        out.append(opt)
    return sorted(out, key=lambda o: str(o.get("id", "")))


# --- telemetry (§3, §4) --------------------------------------------------------


def fingerprint_task(spec: dict) -> str:
    """Deterministic identity of a task spec (no timings, no wall state)."""
    material = {
        "name": spec.get("name", ""),
        "task": spec.get("task", ""),
        "file": spec.get("file", ""),
        "symbol": spec.get("symbol", ""),
        "route_key": spec.get("route_key", ""),
        "failure_command": spec.get("failure_command", ""),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:32]


def _bytes_lines(payload: object) -> tuple[int, int]:
    text = json.dumps(payload, sort_keys=True)
    return len(text.encode("utf-8")), text.count("\n") + 1


def run_task(spec: dict, params: dict | None = None) -> dict:
    """Execute one representative task read-only and record measurements.

    Never edits production source: every stage uses the read-only Phase 1-7
    APIs (resolve / build / freshness probe / scope run=False / precheck).
    Validation commands are planned, never executed, here.
    """
    params = {"packet_level": "auto", "use_cache": True, **(params or {})}
    for key in params:
        if key not in LEARNABLE_KEYS:
            raise OptError(f"non-learnable parameter rejected: {key}")
    started = time.perf_counter()
    calls: dict[str, int] = {}
    record: dict = {
        "schema": SCHEMA_TELEMETRY,
        "task": spec.get("name", ""),
        "task_class": spec.get("task_class", ""),
        "task_fingerprint": fingerprint_task(spec),
        "params": {key: params[key] for key in sorted(params)},
        "searches": 0,
        "search_basis": "exact lookups only (route/surface/packet/manifest/scope APIs)",
        "tool_calls": calls,
        "unexpected_changes": [],
        "result": "FAIL",
        "reasons": [],
    }

    def _tick(name: str) -> None:
        calls[name] = calls.get(name, 0) + 1

    try:
        freshness_before = repo_index.freshness()
        if freshness_before.get("status") != "READY":
            record["reasons"].append(f"index not READY before run: {freshness_before}")
            record["unexpected_changes"].append("index_not_ready_before")
            return _finish(record, started)
        if spec.get("failure_command"):
            _tick("precheck")
            return _finish_failure_task(spec, record, started, calls)
        _tick("route_match")
        routes = route.load_routes()
        matched, how = route.match_route(str(spec.get("task", "")), routes)
        record["route_matched_by"] = how
        _tick("surface_resolve")
        surface = patch_surface.resolve(
            str(spec.get("task", "")),
            file=spec.get("file"),
            symbol=spec.get("symbol"),
            route_key=spec.get("route_key"),
        )
        record["surface_status"] = surface.get("status")
        if surface.get("status") != "RESOLVED":
            record["reasons"].append(f"surface {surface.get('status')}")
            return _finish(record, started)
        record["route"] = (surface.get("route", {}) or {}).get("key")
        _tick("packet_build")
        packet_started = time.perf_counter()
        packet, packet_status = context_packet.build_packet(
            str(spec.get("task", "")),
            level=str(params["packet_level"]),
            file=spec.get("file"),
            symbol=spec.get("symbol"),
            route_key=spec.get("route_key"),
            use_cache=bool(params["use_cache"]),
        )
        record["packet_ms"] = round((time.perf_counter() - packet_started) * 1000, 2)
        record["packet_cache"] = packet_status
        record["packet_status"] = packet.get("status")
        record["packet_levels"] = list(packet.get("levels", []))
        if packet.get("status") != "RESOLVED":
            record["reasons"].append(f"packet {packet.get('status')}")
            return _finish(record, started)
        packet_bytes, packet_lines = _bytes_lines(packet)
        record["context_bytes"] = packet_bytes
        record["context_lines"] = packet_lines
        record["target_files"] = sorted({t.get("file", "") for t in packet.get("targets", [])})
        record["target_symbols"] = sorted(
            {
                s.get("qualified", "")
                for t in packet.get("targets", [])
                for s in t.get("symbols", [])
            }
        )
        _tick("manifest_build")
        manifest_started = time.perf_counter()
        manifest, manifest_status = execute_surface.build_manifest(
            str(spec.get("task", "")),
            file=spec.get("file"),
            symbol=spec.get("symbol"),
            route_key=spec.get("route_key"),
            use_cache=bool(params["use_cache"]),
        )
        record["manifest_ms"] = round((time.perf_counter() - manifest_started) * 1000, 2)
        record["manifest_cache"] = manifest_status
        record["manifest_status"] = manifest.get("status")
        if manifest.get("status") != "READY":
            record["reasons"].append(f"manifest {manifest.get('status')}")
            return _finish(record, started)
        _tick("scope_compute")
        scope_started = time.perf_counter()
        scope = validate_scope.validate_manifest(manifest, run=False)
        record["scope_ms"] = round((time.perf_counter() - scope_started) * 1000, 2)
        record["validation_scope"] = scope.get("scope", "")
        record["validation_commands"] = list(scope.get("commands", []))
        record["scope_status"] = scope.get("status", "")
        if scope.get("status") not in ("VALID", "ESCALATED"):
            record["reasons"].append(f"scope {scope.get('status')}")
            return _finish(record, started)
        expected = spec.get("expect_route")
        if expected and record["route"] != expected:
            record["reasons"].append(f"route {record['route']} != expected {expected}")
            return _finish(record, started)
        freshness_after = repo_index.freshness()
        if freshness_after.get("status") != "READY":
            record["unexpected_changes"].append("index_not_ready_after")
            record["reasons"].append("index moved during read-only run")
            return _finish(record, started)
        record["result"] = "PASS"
        return _finish(record, started)
    except (OSError, ValueError) as exc:
        record["reasons"].append(f"telemetry error: {exc}")
        return _finish(record, started)


def _finish(record: dict, started: float) -> dict:
    record["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return record


def _finish_failure_task(spec: dict, record: dict, started: float, calls: dict) -> dict:
    """Deterministic failure/recovery probe: predict, never execute doomed work."""
    command = str(spec.get("failure_command", ""))
    record["failure_command"] = command
    predicted = failure_intel.precheck([command], {})
    record["precheck"] = predicted["predictions"][0] if predicted["predictions"] else {}
    if not record["precheck"].get("skip"):
        record["reasons"].append("doomed command was not predicted; refusing to run it")
        return _finish(record, started)
    _tick_recover(calls)
    decision = failure_intel.recover(
        {
            "kind": "command",
            "command": command,
            "rc": None,
            "tail": "",
            "note": "predicted doomed; not executed",
            "outcome": "UNAVAILABLE" if "context.py" not in command else "UNAVAILABLE",
        }
    )
    record["failure_category"] = decision.get("category")
    record["recovery_action"] = decision.get("next_action")
    record["recovery_final"] = decision.get("final")
    record["recovery_count"] = 0
    record["escalation_count"] = 0
    expected = spec.get("expect_category")
    if expected and decision.get("category") != expected:
        record["reasons"].append(f"category {decision.get('category')} != expected {expected}")
        return _finish(record, started)
    if decision.get("final") not in ("STOPPED", "ESCALATED", "ENVIRONMENT_FAILURE", "CODE_FAILURE"):
        record["reasons"].append(f"unsafe terminal state {decision.get('final')}")
        return _finish(record, started)
    record["result"] = "PASS"
    return _finish(record, started)


def _tick_recover(calls: dict) -> None:
    calls["recover"] = calls.get("recover", 0) + 1


def record_telemetry(record: dict) -> dict:
    """Append one telemetry record (bounded, atomic). Returns store stats."""
    started = time.perf_counter()
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _store_path("telemetry.jsonl")
    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as exc:
        raise OptError(f"telemetry unreadable: {exc}") from exc
    lines.append(json.dumps(record, sort_keys=True))
    lines = lines[-TELEMETRY_KEEP:]
    tmp = path.with_name(f"telemetry.jsonl.tmp-{os.getpid()}")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return {
        "records": len(lines),
        "overhead_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def load_telemetry() -> list[dict]:
    """Read bounded telemetry records (JSONL; corrupt lines skipped, file kept)."""
    path = _store_path("telemetry.jsonl")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    records = []
    for line in lines[-TELEMETRY_KEEP:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("schema") == SCHEMA_TELEMETRY:
            records.append(item)
    return records


# --- baseline (§5) -------------------------------------------------------------


def build_baseline(specs: tuple[dict, ...] | None = None) -> dict:
    """Measure the golden suite with default parameters -> BASELINE_V1."""
    started = time.perf_counter()
    specs = GOLDEN_TASKS if specs is None else specs
    tasks: dict[str, dict] = {}
    for spec in specs:
        record = run_task(spec)
        record_telemetry(record)
        tasks[spec["name"]] = _task_summary(record)
    baseline = {
        "schema": SCHEMA_BASELINE,
        "id": BASELINE_ID,
        "tasks": tasks,
        "aggregate": _aggregate(tasks),
    }
    _store_json(f"baseline_{BASELINE_ID}.json", baseline)
    baseline["benchmark_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return baseline


def load_baseline() -> dict | None:
    data = _load_json(f"baseline_{BASELINE_ID}.json", None)
    return data if isinstance(data, dict) else None


def _task_summary(record: dict) -> dict:
    return {
        "result": record.get("result"),
        "route": record.get("route", ""),
        "context_bytes": record.get("context_bytes", 0),
        "context_lines": record.get("context_lines", 0),
        "packet_ms": record.get("packet_ms", 0.0),
        "manifest_ms": record.get("manifest_ms", 0.0),
        "scope_ms": record.get("scope_ms", 0.0),
        "total_ms": record.get("total_ms", 0.0),
        "packet_cache": record.get("packet_cache", ""),
        "manifest_cache": record.get("manifest_cache", ""),
        "validation_scope": record.get("validation_scope", ""),
        "validation_commands": record.get("validation_commands", []),
        "tool_calls": record.get("tool_calls", {}),
        "searches": record.get("searches", 0),
        "failure_category": record.get("failure_category", ""),
        "recovery_action": record.get("recovery_action", ""),
        "target_files": record.get("target_files", []),
    }


def _aggregate(tasks: dict[str, dict]) -> dict:
    passed = sum(1 for task in tasks.values() if task.get("result") == "PASS")
    return {
        "tasks": len(tasks),
        "passed": passed,
        "context_bytes": sum(int(task.get("context_bytes", 0)) for task in tasks.values()),
        "total_ms": round(sum(float(task.get("total_ms", 0.0)) for task in tasks.values()), 1),
        "validation_commands": sum(
            len(task.get("validation_commands", [])) for task in tasks.values()
        ),
        "tool_calls": sum(
            sum((task.get("tool_calls", {}) or {}).values()) for task in tasks.values()
        ),
        "searches": sum(int(task.get("searches", 0)) for task in tasks.values()),
    }


# --- analysis + candidates (§6, §7) ---------------------------------------------


def analyze(records: list[dict] | None = None) -> dict:
    """Evidence-first bottleneck analysis over telemetry (no guessing)."""
    started = time.perf_counter()
    records = load_telemetry() if records is None else records
    by_class: dict[str, list[dict]] = {}
    for record in records:
        by_class.setdefault(str(record.get("task_class", "")), []).append(record)
    bottlenecks: list[dict] = []
    repeats: dict[str, int] = {}
    for record in records:
        key = (record.get("task_fingerprint", ""), str(record.get("params", {})))
        repeats[str(key)] = repeats.get(str(key), 0) + 1
    repeated = sum(1 for count in repeats.values() if count > 1)
    if repeated:
        bottlenecks.append(
            {
                "id": "repeated-identical-inputs",
                "evidence": f"{repeated} repeated identical task input(s)",
                "target": "G",
            }
        )
    for task_class, items in sorted(by_class.items()):
        passed = [item for item in items if item.get("result") == "PASS"]
        if not passed:
            continue
        median_bytes = statistics.median([int(item.get("context_bytes", 0)) for item in passed])
        if median_bytes > 20000:
            bottlenecks.append(
                {
                    "id": f"context-heavy-{task_class}",
                    "evidence": f"median context {median_bytes} bytes over {len(passed)} pass(es)",
                    "target": "A",
                }
            )
        scope_runs = sum(
            int((item.get("tool_calls", {}) or {}).get("scope_compute", 0)) for item in passed
        )
        if scope_runs >= MIN_OBSERVATIONS:
            bottlenecks.append(
                {
                    "id": f"repeated-scope-{task_class}",
                    "evidence": f"{scope_runs} scope computation(s)",
                    "target": "F",
                }
            )
    return {
        "records": len(records),
        "bottlenecks": bottlenecks,
        "analysis_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def propose_candidates(
    records: list[dict] | None = None, baseline: dict | None = None
) -> list[dict]:
    """Generate evidence-gated candidates (insufficient evidence -> no proposal)."""
    records = load_telemetry() if records is None else records
    baseline = load_baseline() if baseline is None else baseline
    if baseline is None:
        raise OptError("no baseline; run --baseline first")
    by_class: dict[str, list[dict]] = {}
    for record in records:
        if record.get("result") != "PASS" or record.get("failure_command"):
            continue
        by_class.setdefault(str(record.get("task_class", "")), []).append(record)
    candidates: list[dict] = []
    for task_class in sorted(by_class):
        items = by_class[task_class]
        auto = [
            item for item in items if (item.get("params", {}) or {}).get("packet_level") == "auto"
        ]
        if len(auto) < MIN_OBSERVATIONS:
            continue
        if any((item.get("params", {}) or {}).get("use_cache") is False for item in auto):
            continue
        candidates.append(
            {
                "id": f"CTX-L1-{task_class}",
                "type": "context-level",
                "scope": {"task_class": task_class, "packet_level": "L1"},
                "problem": f"{task_class} tasks repeatedly succeed with auto packets",
                "evidence": f"{len(auto)} passing auto-packet observations",
                "proposed_change": "prefer L1 packets for this task class",
                "expected_benefit": "fewer context bytes/lines, less packet latency",
                "safety_impact": "none if gates hold: manifest READY, validation unchanged",
                "acceptance_threshold": "context bytes -5%+, correctness unchanged",
                "rollback_condition": "any correctness mismatch or safety violation",
                "observations": len(auto),
                "confidence": 1.0,
                "status": "PROPOSED",
                "created_from": BASELINE_ID,
            }
        )
    scope_keys: dict[str, int] = {}
    for record in records:
        if record.get("result") != "PASS" or record.get("failure_command"):
            continue
        if int((record.get("tool_calls", {}) or {}).get("scope_compute", 0)) > 0:
            scope_keys[record.get("task_fingerprint", "")] = (
                scope_keys.get(record.get("task_fingerprint", ""), 0) + 1
            )
    repeated_scope_runs = sum(count for count in scope_keys.values() if count >= MIN_OBSERVATIONS)
    repeated_scope = sum(1 for count in scope_keys.values() if count >= MIN_OBSERVATIONS)
    if repeated_scope:
        candidates.append(
            {
                "id": "SCOPE-REUSE",
                "type": "scope-memo",
                "scope": {},
                "problem": "identical scope inputs recomputed repeatedly",
                "evidence": f"{repeated_scope} task input(s) with {MIN_OBSERVATIONS}+ scope runs",
                "proposed_change": "memoize scope by (manifest_key, index_fingerprint, hashes)",
                "expected_benefit": "less scope-compute latency",
                "safety_impact": "none if memo key matches and manifest still fresh",
                "acceptance_threshold": "identical scope, faster median",
                "rollback_condition": "any scope mismatch or stale serve",
                "observations": repeated_scope_runs,
                "confidence": 1.0,
                "status": "PROPOSED",
                "created_from": BASELINE_ID,
            }
        )
    failure_keys: dict[str, int] = {}
    for record in records:
        if record.get("result") != "PASS" or not record.get("failure_command"):
            continue
        key = str(record.get("failure_command", ""))
        failure_keys[key] = failure_keys.get(key, 0) + 1
    repeated_runs = sum(count for count in failure_keys.values() if count >= MIN_OBSERVATIONS)
    repeated_failures = sum(1 for count in failure_keys.values() if count >= MIN_OBSERVATIONS)
    if repeated_failures:
        candidates.append(
            {
                "id": "FAIL-FAST-known",
                "type": "decision-memo",
                "scope": {},
                "problem": "identical known failures reclassified every run",
                "evidence": f"{repeated_failures} failure command(s) with {MIN_OBSERVATIONS}+ runs",
                "proposed_change": "memoize recovery decisions, re-verified against live output",
                "expected_benefit": "less recovery-decision latency",
                "safety_impact": "none if live output still matches the memoized pattern",
                "acceptance_threshold": "identical decision, faster median",
                "rollback_condition": "any decision mismatch",
                "observations": repeated_runs,
                "confidence": 1.0,
                "status": "PROPOSED",
                "created_from": BASELINE_ID,
            }
        )
    return candidates


# --- candidate execution (learned params only) -----------------------------------


def apply_candidate_params(spec: dict, candidate: dict | None) -> dict:
    """Translate an accepted candidate into run parameters (learnable keys only)."""
    params: dict = {"packet_level": "auto", "use_cache": True}
    if candidate is None:
        return params
    scope = candidate.get("scope", {}) if isinstance(candidate.get("scope"), dict) else {}
    if candidate.get("type") == "context-level" and scope.get("task_class") == spec.get(
        "task_class"
    ):
        level = str(scope.get("packet_level", "auto"))
        if level not in LEVELS:
            raise OptError(f"unsafe learned level rejected: {level}")
        params["packet_level"] = level
    for key in params:
        if key not in LEARNABLE_KEYS:
            raise OptError(f"non-learnable parameter rejected: {key}")
    return params


def _scope_memo_key(manifest: dict) -> str:
    material = {
        "manifest_key": manifest.get("manifest_key", ""),
        "index_fingerprint": manifest.get("index_fingerprint", ""),
        "hashes": manifest.get("hashes", {}),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:32]


def compute_scope_memoized(manifest: dict, store: dict) -> tuple[dict, str]:
    """Scope computation with memoization (freshness-verified, never stale)."""
    key = _scope_memo_key(manifest)
    memo = store.get("memos", {}).get("scope", {}).get(key)
    if isinstance(memo, dict) and memo.get("key") == key:
        freshness = execute_surface.check_manifest(manifest)
        if freshness.get("status") == manifest.get("status", "READY"):
            scope = memo.get("scope")
            if isinstance(scope, dict):
                return scope, "hit"
    scope = validate_scope.validate_manifest(manifest, run=False)
    if scope.get("status") in ("VALID", "ESCALATED"):
        store.setdefault("memos", {}).setdefault("scope", {})[key] = {
            "key": key,
            "scope": {
                "status": scope.get("status", ""),
                "scope": scope.get("scope", ""),
                "commands": list(scope.get("commands", [])),
                "reason": scope.get("reason", ""),
            },
        }
    return scope, "miss"


def _decision_memo_key(command: str, rc: object, tail: str) -> str:
    material = {"command": command, "rc": rc, "tail": (tail or "")[-500:]}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:32]


def recover_memoized(raw: dict, store: dict) -> tuple[dict, str]:
    """Recovery decision with memoization (current evidence always re-verified)."""
    command = str(raw.get("command", ""))
    tail = str(raw.get("tail", ""))
    key = _decision_memo_key(command, raw.get("rc"), tail)
    memo = store.get("memos", {}).get("decision", {}).get(key)
    if isinstance(memo, dict) and memo.get("key") == key:
        pattern = str(memo.get("evidence_pattern", ""))
        if pattern and re.search(pattern, f"{command}\n{tail}", re.IGNORECASE | re.DOTALL):
            decision = memo.get("decision")
            if isinstance(decision, dict):
                served = dict(decision)
                served["served_from_memo"] = True
                return served, "hit"
    decision = failure_intel.recover(raw)
    if decision.get("final") not in ("RECOVERED", None):
        store.setdefault("memos", {}).setdefault("decision", {})[key] = {
            "key": key,
            "evidence_pattern": re.escape((tail or command)[-120:]) if (tail or command) else "",
            "decision": {
                "category": decision.get("category"),
                "subtype": decision.get("subtype"),
                "ownership": decision.get("ownership"),
                "next_action": decision.get("next_action"),
                "final": decision.get("final"),
                "escalation_target": decision.get("escalation_target"),
            },
        }
    return decision, "miss"


# --- safety (§10, §27) -----------------------------------------------------------


def snapshot_from_run(spec: dict, params: dict) -> dict:
    """Protected-field snapshot for one task under given params (read-only)."""
    record = run_task(spec, params)
    manifest_owner = ""
    manifest_language: list = []
    forbidden: list = []
    if not spec.get("failure_command"):
        try:
            manifest, _ = execute_surface.build_manifest(
                str(spec.get("task", "")),
                file=spec.get("file"),
                symbol=spec.get("symbol"),
                route_key=spec.get("route_key"),
            )
            manifest_owner = str(manifest.get("owner", ""))
            manifest_language = [str(manifest.get("language", ""))]
            forbidden = sorted((manifest.get("forbidden", {}) or {}).get("patterns", []))
        except (OSError, ValueError):
            manifest_owner = "UNKNOWN"
    return {
        "result": record.get("result"),
        "route": record.get("route", ""),
        "owner": manifest_owner,
        "language": manifest_language,
        "forbidden": forbidden,
        "validation_commands": record.get("validation_commands", []),
        "context_bytes": record.get("context_bytes", 0),
        "target_files": record.get("target_files", []),
        "reasons": record.get("reasons", []),
    }


def verify_safety(base: dict, candidate: dict) -> list[str]:
    """Protected canonical fields must match exactly; violations listed."""
    violations = []
    for field in PROTECTED_FIELDS:
        if base.get(field) != candidate.get(field):
            violations.append(f"{field}: {base.get(field)!r} != {candidate.get(field)!r}")
    if base.get("result") != "PASS" or candidate.get("result") != "PASS":
        violations.append(f"correctness: {base.get('result')} -> {candidate.get('result')}")
    base_files = set(base.get("target_files", []) or [])
    cand_files = set(candidate.get("target_files", []) or [])
    if not base_files:
        violations.append("correctness: baseline has no target files")
    elif not base_files <= cand_files:
        violations.append(f"coverage: lost targets {sorted(base_files - cand_files)}")
    return violations


# --- A/B verification + acceptance (§16, §17) -------------------------------------


def evaluate_candidate(candidate: dict, specs: tuple[dict, ...] | None = None) -> dict:
    """BASELINE vs CANDIDATE on the golden set (same tasks, same tree)."""
    started = time.perf_counter()
    specs = GOLDEN_TASKS if specs is None else specs
    kind = candidate.get("type")
    if kind == "context-level":
        result = _evaluate_context_level(candidate, specs)
    elif kind == "scope-memo":
        result = _evaluate_scope_memo(candidate, specs)
    elif kind == "decision-memo":
        result = _evaluate_decision_memo(candidate, specs)
    else:
        return {
            "candidate": candidate.get("id"),
            "verdict": "REJECT",
            "reason": f"unknown candidate type: {kind}",
            "comparisons": [],
        }
    result["benchmark_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def _evaluate_context_level(candidate: dict, specs: tuple[dict, ...]) -> dict:
    scoped = [s for s in specs if _candidate_applies(candidate, s)]
    if not scoped:
        return {
            "candidate": candidate.get("id"),
            "verdict": "REJECT",
            "reason": "candidate applies to no golden task",
            "comparisons": [],
        }
    comparisons = []
    for spec in scoped:
        base_params = apply_candidate_params(spec, None)
        cand_params = apply_candidate_params(spec, candidate)
        base = snapshot_from_run(spec, base_params)
        cand = snapshot_from_run(spec, cand_params)
        comparisons.append(
            {
                "task": spec["name"],
                "violations": verify_safety(base, cand),
                "base_bytes": base.get("context_bytes", 0),
                "cand_bytes": cand.get("context_bytes", 0),
                "base_result": base.get("result"),
                "cand_result": cand.get("result"),
            }
        )
    violations = sum(len(item["violations"]) for item in comparisons)
    base_bytes = sum(item["base_bytes"] for item in comparisons)
    cand_bytes = sum(item["cand_bytes"] for item in comparisons)
    verdict = "ACCEPT" if violations == 0 and cand_bytes < base_bytes else "REJECT"
    reason = (
        "all gates hold with smaller context"
        if verdict == "ACCEPT"
        else ("safety/correctness violations" if violations else "no measurable improvement")
    )
    return {
        "candidate": candidate.get("id"),
        "verdict": verdict,
        "reason": reason,
        "violations": violations,
        "metric_name": "context_bytes",
        "metric_base": base_bytes,
        "metric_cand": cand_bytes,
        "base_bytes": base_bytes,
        "cand_bytes": cand_bytes,
        "comparisons": comparisons,
    }


def _median_ms(samples: list[float]) -> float:
    return round(statistics.median(samples) * 1000, 2)


def _evaluate_scope_memo(candidate: dict, specs: tuple[dict, ...]) -> dict:
    scoped = [s for s in specs if not s.get("failure_command")]
    if not scoped:
        return {
            "candidate": candidate.get("id"),
            "verdict": "REJECT",
            "reason": "no validation tasks in scope",
            "comparisons": [],
        }
    store = load_store()
    comparisons = []
    for spec in scoped[:3]:
        manifest, _ = execute_surface.build_manifest(
            str(spec.get("task", "")),
            file=spec.get("file"),
            symbol=spec.get("symbol"),
            route_key=spec.get("route_key"),
        )
        if manifest.get("status") != "READY":
            comparisons.append({"task": spec["name"], "violations": ["manifest not READY"]})
            continue
        base_samples = []
        base: dict = {}
        for _ in range(SAMPLES):
            tick = time.perf_counter()
            base = validate_scope.validate_manifest(manifest, run=False)
            base_samples.append(time.perf_counter() - tick)
        compute_scope_memoized(manifest, store)
        cand_samples = []
        cand: dict = {}
        for _ in range(SAMPLES):
            tick = time.perf_counter()
            cand, _ = compute_scope_memoized(manifest, store)
            cand_samples.append(time.perf_counter() - tick)
        violations = []
        if base.get("status") != cand.get("status") or base.get("scope") != cand.get("scope"):
            violations.append("scope status/level mismatch")
        if list(base.get("commands", [])) != list(cand.get("commands", [])):
            violations.append("scope commands mismatch")
        comparisons.append(
            {
                "task": spec["name"],
                "violations": violations,
                "base_ms": _median_ms(base_samples),
                "cand_ms": _median_ms(cand_samples),
                "base_result": "PASS" if base.get("status") in ("VALID", "ESCALATED") else "FAIL",
                "cand_result": "PASS" if not violations else "FAIL",
            }
        )
    save_store(store)
    violations = sum(len(item["violations"]) for item in comparisons)
    base_ms = round(sum(item.get("base_ms", 0.0) for item in comparisons), 2)
    cand_ms = round(sum(item.get("cand_ms", 0.0) for item in comparisons), 2)
    verdict = "ACCEPT" if violations == 0 and comparisons and cand_ms < base_ms else "REJECT"
    reason = (
        "identical scope with less compute"
        if verdict == "ACCEPT"
        else ("safety/correctness violations" if violations else "no measurable improvement")
    )
    return {
        "candidate": candidate.get("id"),
        "verdict": verdict,
        "reason": reason,
        "violations": violations,
        "metric_name": "scope_ms",
        "metric_base": base_ms,
        "metric_cand": cand_ms,
        "comparisons": comparisons,
    }


def _evaluate_decision_memo(candidate: dict, specs: tuple[dict, ...]) -> dict:
    scoped = [s for s in specs if s.get("failure_command")]
    if not scoped:
        return {
            "candidate": candidate.get("id"),
            "verdict": "REJECT",
            "reason": "no failure tasks in scope",
            "comparisons": [],
        }
    store = load_store()
    comparisons = []
    for spec in scoped:
        raw = {
            "kind": "command",
            "command": str(spec.get("failure_command", "")),
            "rc": None,
            "tail": "",
            "note": "validator script missing: scripts/context.py",
            "outcome": "UNAVAILABLE",
        }
        base_samples = []
        base: dict = {}
        for _ in range(2 * SAMPLES):
            tick = time.perf_counter()
            base = failure_intel.recover(raw)
            base_samples.append(time.perf_counter() - tick)
        recover_memoized(raw, store)
        cand_samples = []
        cand: dict = {}
        for _ in range(2 * SAMPLES):
            tick = time.perf_counter()
            cand, _ = recover_memoized(raw, store)
            cand_samples.append(time.perf_counter() - tick)
        violations = []
        for field in ("category", "next_action", "final"):
            if base.get(field) != cand.get(field):
                violations.append(f"decision {field} mismatch")
        comparisons.append(
            {
                "task": spec["name"],
                "violations": violations,
                "base_ms": _median_ms(base_samples),
                "cand_ms": _median_ms(cand_samples),
                "base_result": "PASS",
                "cand_result": "PASS" if not violations else "FAIL",
            }
        )
    save_store(store)
    violations = sum(len(item["violations"]) for item in comparisons)
    base_ms = round(sum(item.get("base_ms", 0.0) for item in comparisons), 2)
    cand_ms = round(sum(item.get("cand_ms", 0.0) for item in comparisons), 2)
    verdict = "ACCEPT" if violations == 0 and comparisons and cand_ms < base_ms else "REJECT"
    reason = (
        "identical decision with less compute"
        if verdict == "ACCEPT"
        else ("safety/correctness violations" if violations else "no measurable improvement")
    )
    return {
        "candidate": candidate.get("id"),
        "verdict": verdict,
        "reason": reason,
        "violations": violations,
        "metric_name": "decision_ms",
        "metric_base": base_ms,
        "metric_cand": cand_ms,
        "comparisons": comparisons,
    }


def _candidate_applies(candidate: dict, spec: dict) -> bool:
    if candidate.get("type") != "context-level":
        return False
    scope = candidate.get("scope", {})
    return isinstance(scope, dict) and scope.get("task_class") == spec.get("task_class")


def store_candidate(store: dict, candidate: dict) -> dict:
    optimizations = store.setdefault("optimizations", {})
    key = str(candidate.get("id", ""))
    if not key:
        raise OptError("candidate needs an id")
    entry = optimizations.get(key)
    version = 1 if not isinstance(entry, dict) else int(entry.get("version", 0)) + 1
    stored = {
        **candidate,
        "version": version,
        "history": [
            *entry.get("history", []),
            {"version": version, "status": candidate.get("status", "PROPOSED")},
        ]
        if isinstance(entry, dict)
        else [{"version": 1, "status": candidate.get("status", "PROPOSED")}],
    }
    optimizations[key] = stored
    return stored


def accept_candidate(store: dict, candidate_id: str, evaluation: dict) -> dict:
    optimizations = store.get("optimizations", {})
    candidate = optimizations.get(candidate_id)
    if not isinstance(candidate, dict):
        raise OptError(f"unknown candidate: {candidate_id}")
    if evaluation.get("candidate") != candidate_id or evaluation.get("verdict") != "ACCEPT":
        raise OptError("acceptance requires a passing A/B evaluation for this candidate")
    candidate["status"] = "ACCEPTED"
    candidate["evaluation"] = {
        "verdict": evaluation.get("verdict"),
        "reason": evaluation.get("reason"),
        "violations": evaluation.get("violations", 0),
        "metric_name": evaluation.get("metric_name", ""),
        "metric_base": evaluation.get("metric_base", evaluation.get("base_bytes", 0)),
        "metric_cand": evaluation.get("metric_cand", evaluation.get("cand_bytes", 0)),
    }
    candidate["version"] = int(candidate.get("version", 1)) + 1
    history = candidate.get("history")
    if isinstance(history, list):
        history.append({"version": candidate["version"], "status": "ACCEPTED"})
    save_store(store)
    return candidate


def reject_candidate(store: dict, candidate_id: str, reason: str) -> dict:
    optimizations = store.get("optimizations", {})
    candidate = optimizations.get(candidate_id)
    if not isinstance(candidate, dict):
        raise OptError(f"unknown candidate: {candidate_id}")
    candidate["status"] = "REJECTED"
    candidate["reject_reason"] = reason
    candidate["version"] = int(candidate.get("version", 1)) + 1
    history = candidate.get("history")
    if isinstance(history, list):
        history.append({"version": candidate["version"], "status": "REJECTED"})
    save_store(store)
    return candidate


def rollback_candidate(store: dict, candidate_id: str, reason: str) -> dict:
    """Revert an accepted optimization; history is appended, never rewritten."""
    started = time.perf_counter()
    optimizations = store.get("optimizations", {})
    candidate = optimizations.get(candidate_id)
    if not isinstance(candidate, dict):
        raise OptError(f"unknown candidate: {candidate_id}")
    if candidate.get("status") != "ACCEPTED":
        raise OptError("only ACCEPTED optimizations can be rolled back")
    candidate["status"] = "ROLLED_BACK"
    candidate["rollback_reason"] = reason
    candidate["version"] = int(candidate.get("version", 1)) + 1
    history = candidate.get("history")
    if isinstance(history, list):
        history.append({"version": candidate["version"], "status": "ROLLED_BACK"})
    save_store(store)
    verification = run_task(
        {
            "name": "rollback-probe",
            "task_class": "probe",
            "task": "download settings",
            "expect_route": "download-config",
        }
    )
    return {
        "candidate": candidate_id,
        "status": candidate["status"],
        "verification_result": verification.get("result"),
        "rollback_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# --- benchmark suite (§25) ---------------------------------------------------------


def benchmark_suite(specs: tuple[dict, ...] | None = None, store: dict | None = None) -> dict:
    """Permanent AI-speed suite: correctness, context, latency, validation,
    recovery, searches, cache — baseline params plus accepted optimizations."""
    started = time.perf_counter()
    specs = GOLDEN_TASKS if specs is None else specs
    store = load_store() if store is None else store
    results: dict[str, dict] = {}
    for spec in specs:
        params = {"packet_level": "auto", "use_cache": True}
        for opt in active_candidates(store):
            if opt.get("type") == "context-level":
                scope = opt.get("scope", {})
                if isinstance(scope, dict) and scope.get("task_class") == spec.get("task_class"):
                    params = apply_candidate_params(spec, opt)
        record = run_task(spec, params)
        record_telemetry(record)
        results[spec["name"]] = _task_summary(record)
    aggregate = _aggregate(results)
    return {
        "schema": "opt-benchmark/v1",
        "tasks": results,
        "aggregate": aggregate,
        "active_optimizations": [o.get("id", "") for o in active_candidates(store)],
        "benchmark_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# --- diagnostic report (§19) ---------------------------------------------------------


def diagnostic_report() -> dict:
    started = time.perf_counter()
    baseline = load_baseline()
    store = load_store()
    suite = benchmark_suite(store=store)
    optimizations = store.get("optimizations", {})
    accepted = [
        o for o in optimizations.values() if isinstance(o, dict) and o.get("status") == "ACCEPTED"
    ]
    rejected = [
        o for o in optimizations.values() if isinstance(o, dict) and o.get("status") == "REJECTED"
    ]
    rolled = [
        o
        for o in optimizations.values()
        if isinstance(o, dict)
        and any(h.get("status") == "ROLLED_BACK" for h in o.get("history", []))
    ]
    base_bytes = ((baseline or {}).get("aggregate", {}) or {}).get("context_bytes", 0)
    now_bytes = suite["aggregate"]["context_bytes"]
    return {
        "baseline": BASELINE_ID if baseline else "MISSING",
        "baseline_context_bytes": base_bytes,
        "current_context_bytes": now_bytes,
        "context_delta_bytes": now_bytes - base_bytes if baseline else 0,
        "baseline_validation_commands": ((baseline or {}).get("aggregate", {}) or {}).get(
            "validation_commands", 0
        ),
        "current_validation_commands": suite["aggregate"]["validation_commands"],
        "optimizations": {key: _opt_summary(opt) for key, opt in optimizations.items()},
        "accepted": len(accepted),
        "rejected": len(rejected),
        "rollbacks": len(rolled),
        "regressions": [
            name for name, task in suite["tasks"].items() if task.get("result") != "PASS"
        ],
        "suite_passed": suite["aggregate"]["passed"],
        "suite_tasks": suite["aggregate"]["tasks"],
        "suite_searches": suite["aggregate"]["searches"],
        "report_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _opt_summary(opt: object) -> dict:
    if not isinstance(opt, dict):
        return {}
    return {
        "id": opt.get("id"),
        "type": opt.get("type"),
        "version": opt.get("version"),
        "status": opt.get("status"),
        "confidence": opt.get("confidence"),
    }


# --- CLI -------------------------------------------------------------------------


def render_text(report: dict) -> str:
    lines = [
        f"baseline: {report.get('baseline')} "
        f"({report.get('baseline_context_bytes', 0)} -> "
        f"{report.get('current_context_bytes', 0)} bytes)",
        f"validation commands: {report.get('baseline_validation_commands', 0)} -> "
        f"{report.get('current_validation_commands', 0)}",
        f"optimizations: accepted={report.get('accepted', 0)} rejected={report.get('rejected', 0)} "
        f"rollbacks={report.get('rollbacks', 0)}",
        f"suite: {report.get('suite_passed', 0)}/{report.get('suite_tasks', 0)} passed, "
        f"searches={report.get('suite_searches', 0)}",
    ]
    if report.get("regressions"):
        lines.append(f"regressions: {', '.join(report['regressions'])}")
    for key, opt in (report.get("optimizations", {}) or {}).items():
        lines.append(f"  - {key} v{opt.get('version')} [{opt.get('status')}] ({opt.get('type')})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measurement-driven self-optimizing loop")
    parser.add_argument("--telemetry", action="store_true", help="record one golden-suite pass")
    parser.add_argument("--baseline", action="store_true", help="measure and store BASELINE_V1")
    parser.add_argument(
        "--candidates", action="store_true", help="propose evidence-gated candidates"
    )
    parser.add_argument("--evaluate", default=None, help="A/B evaluate a candidate id")
    parser.add_argument("--accept", default=None, help="accept a candidate id")
    parser.add_argument("--reject", default=None, help="reject a candidate id")
    parser.add_argument("--rollback", default=None, help="roll back a candidate id")
    parser.add_argument("--benchmark", action="store_true", help="run the permanent suite")
    parser.add_argument("--report", action="store_true", help="self-diagnostic report")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    if args.telemetry or args.baseline or args.benchmark or args.report or args.evaluate:
        _warm_index()

    if args.telemetry:
        suite = benchmark_suite()
        payload: object = {
            "recorded": suite["aggregate"]["tasks"],
            "passed": suite["aggregate"]["passed"],
        }
        print(
            json.dumps(payload, indent=2, sort_keys=True)
            if args.json
            else json.dumps(payload, sort_keys=True)
        )
        return 0 if suite["aggregate"]["passed"] == suite["aggregate"]["tasks"] else 1
    if args.baseline:
        baseline = build_baseline()
        print(
            json.dumps(baseline, indent=2, sort_keys=True)
            if args.json
            else json.dumps(baseline["aggregate"], sort_keys=True)
        )
        return 0 if baseline["aggregate"]["passed"] == baseline["aggregate"]["tasks"] else 1
    if args.candidates:
        store = load_store()
        candidates = propose_candidates()
        for candidate in candidates:
            store_candidate(store, candidate)
        save_store(store)
        print(json.dumps(candidates, indent=2, sort_keys=True))
        return 0
    if args.evaluate:
        store = load_store()
        candidate = store.get("optimizations", {}).get(args.evaluate)
        if not isinstance(candidate, dict):
            print(json.dumps({"error": f"unknown candidate: {args.evaluate}"}))
            return 2
        print(json.dumps(evaluate_candidate(candidate), indent=2, sort_keys=True))
        return 0
    if args.accept or args.reject or args.rollback:
        store = load_store()
        try:
            if args.accept:
                evaluation = evaluate_candidate(store["optimizations"][args.accept])
                result = accept_candidate(store, args.accept, evaluation)
            elif args.reject:
                result = reject_candidate(store, args.reject, "operator rejection")
            else:
                result = rollback_candidate(store, args.rollback or "", "operator rollback")
        except (OptError, KeyError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.benchmark:
        print(json.dumps(benchmark_suite(), indent=2, sort_keys=True))
        return 0
    if args.report:
        report = diagnostic_report()
        print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_text(report))
        return 0 if not report.get("regressions") else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
