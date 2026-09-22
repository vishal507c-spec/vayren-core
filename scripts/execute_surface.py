"""Zero-discovery execution surface (Phase 5).

READ/EXECUTION-SURFACE layer over Phase-4 packets (no codegen, no patching):
  TASK -> packet -> execution manifest -> exact READ -> gated CHECK_EDIT
  -> exact TESTS -> exact VALIDATE (list or --run).

Every path/symbol/command must resolve through canonical Phase-1-4 data.
No repository search, no target/test/validation rediscovery: unknown paths,
symbols, and commands fail explicitly (BLOCKED/STALE/UNKNOWN/...).

Usage:
    python scripts/execute_surface.py --task "add validation to broker registry"
    python scripts/execute_surface.py --task "..." --json
    python scripts/execute_surface.py --task "..." --read 09_broker/broker/registry.py
    python scripts/execute_surface.py --task "..." --tests
    python scripts/execute_surface.py --task "..." --validate [--run]
    python scripts/execute_surface.py --task "..." --check-edit 09_broker/broker/registry.py
    python scripts/execute_surface.py --check-manifest <file>
    python scripts/execute_surface.py --task "..." --plan
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import context_packet  # noqa: E402
import repo_graph  # noqa: E402
import repo_index  # noqa: E402

ROOT = SCRIPTS_DIR.parent
EXEC_DIR = repo_index.INDEX_DIR / "exec"

MANIFEST_SCHEMA = "execution-manifest/v1"
VALIDATE_TIMEOUT_S = 240
KNOWN_RUNNERS = ("python", "pyright", "cargo", "pytest", "ruff", "make")


class SurfaceError(Exception):
    """Execution-surface failure — explicit status, never a search fallback."""


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_relpath(raw: str) -> str:
    """Capability boundary: repo-relative posix paths only (no traversal)."""
    if not raw or raw.startswith(("/", "\\")) or ".." in Path(raw).parts:
        raise SurfaceError(f"rejected path (outside repository): {raw}")
    if len(raw) > 1 and raw[1] == ":":
        raise SurfaceError(f"rejected path (outside repository): {raw}")
    return raw.replace("\\", "/")


def _manifest_key(packet_key: str, read_only: bool) -> str:
    flag = "ro" if read_only else "rw"
    return hashlib.sha256(f"exec/{MANIFEST_SCHEMA}/{packet_key}/{flag}".encode()).hexdigest()[:32]


def _manifest_load(key: str) -> dict | None:
    try:
        data = json.loads((EXEC_DIR / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _manifest_store(key: str, manifest: dict) -> None:
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    path = EXEC_DIR / f"{key}.json"
    tmp = path.with_name(f"{key}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _verify_command(command: str) -> dict:
    """Availability check for one resolved validation command (no invention)."""
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return {"command": command, "available": False, "reason": f"unparsable: {exc}"}
    if not parts:
        return {"command": command, "available": False, "reason": "empty command"}
    runner, args = parts[0], parts[1:]
    if runner not in KNOWN_RUNNERS:
        return {"command": command, "available": False, "reason": f"unknown runner {runner!r}"}
    if runner == "python":
        binary = sys.executable
    elif shutil.which(runner) is None:
        return {"command": command, "available": False, "reason": f"binary not on PATH: {runner}"}
    else:
        binary = runner
    if "<" in command and ">" in command:
        return {
            "command": command,
            "available": True,
            "binary": binary,
            "template": True,
            "note": "template: fill <placeholders> first",
        }
    for token in args:
        if token.startswith("scripts/") and token.endswith(".py") and not (ROOT / token).is_file():
            return {
                "command": command,
                "available": False,
                "reason": f"validator script missing: {token}",
            }
    return {"command": command, "available": True, "binary": binary or runner}


def build_manifest(
    task: str = "",
    *,
    file: str | None = None,
    symbol: str | None = None,
    module: str | None = None,
    route_key: str | None = None,
    domain: str | None = None,
    modify: list[str] | None = None,
    read_only: bool = False,
    use_cache: bool = True,
) -> tuple[dict, str]:
    """TASK -> packet -> execution manifest. Returns (manifest, cache_status)."""
    try:
        packet, _packet_status = context_packet.build_packet(
            task,
            file=file,
            symbol=symbol,
            module=module,
            route_key=route_key,
            domain=domain,
            modify=modify or [],
        )
    except context_packet.PacketError as exc:
        return _status_manifest(task, "STALE", str(exc)), "miss"
    if packet.get("status") != "RESOLVED":
        return _passthrough_manifest(packet, task), "miss"

    key = _manifest_key(str(packet.get("cache", {}).get("key", "")), read_only)
    if use_cache:
        cached = _manifest_load(key)
        if cached is not None and cached.get("manifest_key") == key:
            cached["cache"] = {"status": "hit", "key": key}
            return cached, "hit"

    safety = packet.get("safety", {}) or {}
    must_not = safety.get("must_not_change", {}) or {}
    must = sorted(safety.get("must_change", []) or [])
    may = sorted(safety.get("may_change", []) or [])
    test_entries = packet.get("tests", []) or []
    test_paths = sorted({t["path"] for t in test_entries if t.get("path")})
    caller_files = sorted({c["file"] for c in packet.get("callers", []) if c.get("file")})
    read_files = sorted(set(must) | set(may) | set(test_paths) | set(caller_files))

    hashes: dict[str, str] = {}
    regions: dict[str, list[dict]] = {}
    for relpath in read_files:
        try:
            safe = _safe_relpath(relpath)
        except SurfaceError:
            continue
        path = ROOT / safe
        if not path.is_file():
            continue
        try:
            hashes[safe] = _sha_file(path)
        except OSError:
            continue
    for target in packet.get("targets", []) or []:
        relpath = target.get("file", "")
        if relpath not in hashes:
            continue
        regions[relpath] = [
            {
                "qualified": s["qualified"],
                "line": s["line"],
                "end": s.get("body", {}).get("end", s["line"]),
            }
            for s in target.get("symbols", [])
            if "body" in s
        ]
    for entry in test_entries:
        relpath = entry.get("path", "")
        if relpath and relpath in hashes and relpath not in regions:
            regions[relpath] = [
                {
                    "qualified": s["qualified"],
                    "line": s["line"],
                    "end": s.get("snippet", {}).get("end", s["line"]),
                }
                for s in entry.get("symbols", [])
            ]

    missing_tests = [p for p in test_paths if p not in hashes]
    tests_note = ""
    for entry in test_entries:
        if entry.get("relevance") == "missing" and entry.get("note"):
            tests_note = str(entry["note"])
            break
    if not packet.get("tests") and not packet.get("validation", {}).get("tests"):
        plan_tests = (packet.get("plan", {}) or {}).get("test", []) or []
        validation_tests = (packet.get("validation", {}) or {}).get("tests", []) or []
        if not plan_tests and not validation_tests and not tests_note:
            manifest = _base_manifest(packet, task, key, read_only)
            manifest.update(
                {
                    "status": "TEST_TARGET_MISSING",
                    "reason": "no tests resolved and no route test command/note",
                    "hashes": hashes,
                    "regions": regions,
                }
            )
            return manifest, "miss"

    validation = packet.get("validation", {}) or {}
    commands = list(validation.get("commands", []) or []) + [
        c
        for c in (packet.get("plan", {}) or {}).get("validate", []) or []
        if c not in (validation.get("commands", []) or [])
    ]
    checked = [_verify_command(command) for command in commands]
    unavailable = [c for c in checked if not c["available"]]

    manifest = _base_manifest(packet, task, key, read_only)
    manifest.update(
        {
            "read": sorted(read_files),
            "modify": {"must": must, "may": may},
            "forbidden": {
                "patterns": must_not.get("patterns", []),
                "files": sorted(must_not.get("files", [])),
            },
            "tests": [
                {
                    "path": t.get("path", ""),
                    "relevance": t.get("relevance", ""),
                    "symbols": [s.get("qualified", "") for s in t.get("symbols", [])],
                    "note": t.get("note", ""),
                }
                for t in test_entries
            ],
            "validate": {
                "scope": validation.get("scope", ""),
                "commands": checked,
            },
            "hashes": hashes,
            "regions": regions,
        }
    )
    if missing_tests:
        manifest["status"] = "TEST_TARGET_MISSING"
        manifest["reason"] = f"resolved test files missing on disk: {missing_tests}"
        return manifest, "miss"
    if unavailable:
        manifest["status"] = "VALIDATION_UNAVAILABLE"
        manifest["reason"] = "; ".join(
            f"{c['command']} ({c.get('reason', 'unavailable')})" for c in unavailable
        )
        return manifest, "miss"
    manifest["status"] = "READ_ONLY" if read_only else "READY"
    if use_cache:
        _manifest_store(key, manifest)
    return manifest, "miss"


def _base_manifest(packet: dict, task: str, key: str, read_only: bool) -> dict:
    route = packet.get("route", {}) or {}
    return {
        "schema": MANIFEST_SCHEMA,
        "status": "READY",
        "task": " ".join(task.split()),
        "route": route.get("key"),
        "domain": route.get("domain"),
        "owner": route.get("owner"),
        "language": route.get("language"),
        "read_only": read_only,
        "packet_key": packet.get("cache", {}).get("key", ""),
        "index_fingerprint": (packet.get("evidence", {}) or {}).get("index_fingerprint"),
        "graph_inputs_hash": (packet.get("evidence", {}) or {}).get("graph_inputs_hash"),
        "manifest_key": key,
        "cache": {"status": "miss", "key": key},
    }


def _status_manifest(task: str, status: str, reason: str) -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "status": status,
        "task": " ".join(task.split()),
        "reason": reason,
        "read": [],
        "modify": {"must": [], "may": []},
        "forbidden": {"patterns": [], "files": []},
        "tests": [],
        "validate": {"scope": "", "commands": []},
        "hashes": {},
        "regions": {},
        "manifest_key": "",
        "cache": {"status": "miss", "key": ""},
    }


def _passthrough_manifest(packet: dict, task: str) -> dict:
    manifest = _status_manifest(task, str(packet.get("status", "UNKNOWN")), "")
    manifest["reason"] = (packet.get("unknown", {}) or {}).get(
        "reason", ""
    ) or f"patch surface status: {packet.get('status')}"
    manifest["needed"] = (packet.get("unknown", {}) or {}).get("needed", [])
    manifest["route"] = (packet.get("route", {}) or {}).get("key")
    manifest["safety"] = packet.get("safety", {})
    return manifest


def check_manifest(manifest: dict) -> dict:
    """Re-verify a manifest against the live tree (hashes, symbols, freshness)."""
    if manifest.get("schema") != MANIFEST_SCHEMA:
        return {"status": "UNKNOWN", "reason": "not an execution manifest"}
    state = repo_index._read_json(repo_index.STATE_FILE)
    fingerprint = state.get("fingerprint") if isinstance(state, dict) else None
    if fingerprint != manifest.get("index_fingerprint"):
        return {
            "status": "STALE",
            "reason": "STALE_EXECUTION_SURFACE: index moved on",
            "detail": "regenerate the manifest",
        }
    for relpath, recorded in (manifest.get("hashes", {}) or {}).items():
        try:
            safe = _safe_relpath(relpath)
        except SurfaceError as exc:
            return {"status": "BLOCKED", "reason": str(exc)}
        path = ROOT / safe
        if not path.is_file():
            return {
                "status": "STALE",
                "reason": "STALE_EXECUTION_SURFACE: target deleted",
                "detail": safe,
            }
        try:
            current = _sha_file(path)
        except OSError:
            return {
                "status": "STALE",
                "reason": "STALE_EXECUTION_SURFACE: unreadable",
                "detail": safe,
            }
        if current != recorded:
            return {
                "status": "STALE",
                "reason": "STALE_EXECUTION_SURFACE: concurrent modification",
                "detail": safe,
                "recorded_sha": recorded[:12],
                "current_sha": current[:12],
            }
    return {"status": manifest.get("status", "READY"), "reason": "manifest fresh"}


def read_entry(manifest: dict, path: str) -> dict:
    """Exact read of manifest-recorded regions (capability-checked, hash-checked)."""
    try:
        safe = _safe_relpath(path)
    except SurfaceError as exc:
        return {"status": "BLOCKED", "reason": str(exc)}
    if safe not in (manifest.get("hashes", {}) or {}):
        return {
            "status": "BLOCKED",
            "reason": f"path outside manifest read set: {safe}",
            "allowed": sorted(manifest.get("hashes", {}) or {})[:10],
        }
    freshness = check_manifest(manifest)
    if freshness["status"] not in (manifest.get("status", "READY"), "READY", "READ_ONLY"):
        return freshness
    try:
        lines = (ROOT / safe).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"status": "STALE", "reason": f"cannot read target: {exc}"}
    regions = (manifest.get("regions", {}) or {}).get(safe, [])
    if not regions:
        total = len(lines)
        regions = [{"qualified": "", "line": 1, "end": min(total, 60)}]
    out = []
    for region in regions:
        start, end = max(1, region["line"]), max(region["line"], region["end"])
        out.append(
            {
                "qualified": region.get("qualified", ""),
                "line": start,
                "end": min(end, len(lines)),
                "text": "\n".join(lines[start - 1 : min(end, len(lines))]),
            }
        )
    return {"status": "READY", "file": safe, "sha": manifest["hashes"][safe], "regions": out}


def check_edit(manifest: dict, path: str, symbol: str | None = None) -> dict:
    """Edit gate: MUST/MAY allow (when fresh), MUST_NOT + violations block."""
    try:
        safe = _safe_relpath(path)
    except SurfaceError as exc:
        return {"verdict": "BLOCKED", "reason": str(exc)}
    if manifest.get("read_only"):
        return {"verdict": "BLOCKED", "reason": "manifest is read-only"}
    forbidden = manifest.get("forbidden", {}) or {}
    for pattern in forbidden.get("patterns", []) or []:
        if fnmatch.fnmatch(safe, str(pattern)):
            return {
                "verdict": "BLOCKED",
                "reason": f"path matches forbidden pattern {pattern}",
                "requested": safe,
                "allowed": (manifest.get("modify", {}) or {}).get("must", []),
            }
    if safe in (forbidden.get("files", []) or []):
        return {"verdict": "BLOCKED", "reason": "path is a forbidden file", "requested": safe}
    must = (manifest.get("modify", {}) or {}).get("must", []) or []
    may = (manifest.get("modify", {}) or {}).get("may", []) or []
    if safe not in must and safe not in may:
        return {
            "verdict": "BLOCKED",
            "reason": "path outside MUST/MAY change boundary",
            "requested": safe,
            "allowed": must,
        }
    freshness = check_manifest(manifest)
    if freshness["status"] not in ("READY", "READ_ONLY"):
        result = dict(freshness)
        result["verdict"] = "BLOCKED" if freshness["status"] == "BLOCKED" else "STALE"
        return result
    scope = "MUST_CHANGE" if safe in must else "MAY_CHANGE (within packet boundary)"
    if symbol is not None:
        regions = (manifest.get("regions", {}) or {}).get(safe, [])
        match = [r for r in regions if r.get("qualified") == symbol]
        if not match:
            try:
                payload = repo_index.ensure_fresh()
            except (
                repo_index.IndexError,
                repo_index.IndexLockedError,
                repo_graph.GraphError,
                OSError,
                ValueError,
            ):
                payload = None
            detail = ""
            if payload is not None:
                hits = repo_index.query_with_maps(payload, "symbol", symbol)
                if isinstance(hits, list) and hits:
                    detail = f"now at {hits[0]['file']}:{hits[0]['line']}"
                else:
                    detail = "symbol gone from index"
            return {
                "verdict": "STALE",
                "reason": "STALE_EXECUTION_SURFACE: symbol moved/deleted",
                "requested": symbol,
                "detail": detail,
            }
        return {"verdict": "ALLOW", "scope": scope, "symbol": symbol, "line": match[0]["line"]}
    return {"verdict": "ALLOW", "scope": scope}


def run_validation(
    manifest: dict, *, timeout_s: int = VALIDATE_TIMEOUT_S, cwd: str | Path | None = None
) -> dict:
    """Execute resolved validation commands (literal commands only).

    `cwd` overrides the working directory for commands that canonically run
    elsewhere (cargo commands run under rust/, like Makefile/CI); default
    preserves historical behavior (repository root).
    """
    commands = (manifest.get("validate", {}) or {}).get("commands", []) or []
    results: list[dict] = []
    for entry in commands:
        command = entry["command"] if isinstance(entry, dict) else str(entry)
        available = entry.get("available", True) if isinstance(entry, dict) else True
        if isinstance(entry, dict) and entry.get("template"):
            results.append({"command": command, "rc": None, "note": "template skipped"})
            continue
        if not available:
            results.append({"command": command, "rc": None, "note": "unavailable"})
            continue
        try:
            parts = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            results.append({"command": command, "rc": None, "note": f"unparsable: {exc}"})
            continue
        if parts and parts[0] == "python":
            parts = [sys.executable, *parts[1:]]
        try:
            proc = subprocess.run(
                parts,
                cwd=str(cwd or ROOT),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            tail = (proc.stdout + proc.stderr)[-2000:]
            results.append(
                {"command": command, "rc": proc.returncode, "tail": tail, "cwd": str(cwd or ROOT)}
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({"command": command, "rc": None, "note": f"failed to run: {exc}"})
    success = all(
        r.get("rc", 1) == 0 or r.get("rc") is None and "skipped" in str(r.get("note", ""))
        for r in results
    )
    return {"status": "EXECUTION_COMPLETE", "success": success, "results": results}


def workflow_plan(manifest: dict) -> list[str]:
    """The obvious READ -> MODIFY -> TEST workflow with exact values filled."""
    modify = manifest.get("modify", {}) or {}
    tests = manifest.get("tests", []) or []
    validate = manifest.get("validate", {}) or {}
    forbidden = manifest.get("forbidden", {}) or {}
    commands = validate.get("commands", []) or []
    steps = [
        f"1. READ: {list(manifest.get('regions', {}) or {})}",
        f"2. MODIFY (gated): must={modify.get('must', []) or []}",
        f"3. TEST: {[t.get('path') for t in tests if t.get('path')]}",
        (f"4. VALIDATE: {[c.get('command') if isinstance(c, dict) else c for c in commands]}"),
        f"5. FORBIDDEN: {forbidden.get('patterns', []) or []}",
    ]
    return steps


def render_text(manifest: dict) -> str:
    """Compact scan: STATUS/MANIFEST/READ/MODIFY/TEST/VALIDATE/FORBIDDEN."""
    lines = [f"STATUS: {manifest.get('status', '?')} [{manifest.get('schema', '?')}]"]
    lines.append(f"TASK: {manifest.get('task', '')}  (route: {manifest.get('route', '—')})")
    if manifest.get("status") not in ("READY", "READ_ONLY", "EXECUTION_COMPLETE"):
        lines.append(f"REASON: {manifest.get('reason', '?')}")
        for need in manifest.get("needed", []) or []:
            lines.append(f"  - {need}")
        return "\n".join(lines)
    read = manifest.get("read", []) or []
    lines.append(f"READ ({len(read)} files, hashes pinned):")
    for path in read[:12]:
        lines.append(f"  {path}")
    if len(read) > 12:
        lines.append(f"  (+{len(read) - 12} more)")
    modify = manifest.get("modify", {}) or {}
    lines.append(f"MODIFY must={len(modify.get('must', []))} may={len(modify.get('may', []))}")
    for path in (modify.get("must", []) or [])[:8]:
        lines.append(f"  MUST {path}")
    tests = manifest.get("tests", []) or []
    lines.append(f"TEST ({len([t for t in tests if t.get('path')])} files):")
    for test in tests[:6]:
        if test.get("path"):
            lines.append(f"  {test['path']} [{test.get('relevance', '')}]")
        else:
            lines.append(f"  (missing: {test.get('note', '?')})")
    lines.append(f"VALIDATE [{(manifest.get('validate', {}) or {}).get('scope', '?')}]:")
    for entry in ((manifest.get("validate", {}) or {}).get("commands", []) or [])[:8]:
        command = entry.get("command", entry) if isinstance(entry, dict) else entry
        lines.append(f"  {command}")
    forbidden = manifest.get("forbidden", {}) or {}
    lines.append(f"FORBIDDEN: {', '.join(forbidden.get('patterns', [])[:6]) or '—'}")
    return "\n".join(lines)


def benchmark() -> dict:
    """A packet vs B manifest vs C read vs D tests vs E validation; cold/warm/hit."""
    task = "add validation to broker registry"
    out: dict = {}
    started = time.perf_counter()
    context_packet.build_packet(task, use_cache=False)
    out["A_packet_cold_ms"] = round((time.perf_counter() - started) * 1000, 1)
    started = time.perf_counter()
    manifest, _ = build_manifest(task, use_cache=False)
    out["B_manifest_cold_ms"] = round((time.perf_counter() - started) * 1000, 1)
    started = time.perf_counter()
    build_manifest(task, use_cache=True)
    out["B_manifest_warm_ms"] = round((time.perf_counter() - started) * 1000, 1)
    target = (manifest.get("modify", {}) or {}).get("must", [""])[0]
    started = time.perf_counter()
    read_entry(manifest, target)
    out["C_exact_read_ms"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    _tests = manifest.get("tests", [])
    _ = [t["path"] for t in _tests if t.get("path")]
    out["D_test_resolution_ms"] = round((time.perf_counter() - started) * 1000, 4)
    started = time.perf_counter()
    _commands = (manifest.get("validate", {}) or {}).get("commands", [])
    _ = [c["command"] if isinstance(c, dict) else c for c in _commands]
    out["E_validation_resolution_ms"] = round((time.perf_counter() - started) * 1000, 4)
    started = time.perf_counter()
    manifest2, status2 = build_manifest(task, use_cache=True)
    out["B_manifest_cache_hit_ms"] = round((time.perf_counter() - started) * 1000, 2)
    out["B_manifest_cache_status"] = status2
    _ = manifest2
    started = time.perf_counter()
    entry = read_entry(manifest, target)
    out["C_first_edit_ready_ms"] = round(out["B_manifest_warm_ms"] + out["C_exact_read_ms"], 2)
    out["C_read_regions"] = len(entry.get("regions", []))
    out["searches_performed"] = 0
    out["discovery_calls"] = 0
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-discovery execution surface")
    parser.add_argument("--task", default="", help="task description")
    parser.add_argument("--file", default=None, help="explicit target file")
    parser.add_argument("--symbol", default=None, help="explicit target symbol")
    parser.add_argument("--module", default=None, help="explicit target module")
    parser.add_argument("--route", default=None, help="explicit route key")
    parser.add_argument("--domain", default=None, help="narrow to a route domain")
    parser.add_argument("--modify", action="append", default=[], help="planned edit target")
    parser.add_argument("--read-only", action="store_true", help="read-only manifest")
    parser.add_argument("--json", action="store_true", help="emit the manifest as JSON")
    parser.add_argument("--no-cache", action="store_true", help="skip the manifest cache")
    parser.add_argument("--read", nargs="?", const="", default=None, help="exact read of PATH")
    parser.add_argument("--tests", action="store_true", help="exact test targets only")
    parser.add_argument("--validate", action="store_true", help="validation commands")
    parser.add_argument("--run", action="store_true", help="execute validation with --validate")
    parser.add_argument("--check-edit", default=None, help="gate one edit PATH")
    parser.add_argument("--symbol-check", default=None, help="symbol for --check-edit")
    parser.add_argument("--check-manifest", default=None, help="re-verify a manifest JSON file")
    parser.add_argument("--plan", action="store_true", help="print the READ->VALIDATE workflow")
    parser.add_argument("--stats", action="store_true", help="A/B/C/D/E benchmark")
    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
    if args.stats:
        print(json.dumps(benchmark(), indent=2, sort_keys=True))
        return 0
    if args.check_manifest:
        try:
            manifest = json.loads(Path(args.check_manifest).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(json.dumps({"status": "UNKNOWN", "reason": f"cannot load manifest: {exc}"}))
            return 2
        result = (
            check_manifest(manifest)
            if isinstance(manifest, dict)
            else {"status": "UNKNOWN", "reason": "not an object"}
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] in ("READY", "READ_ONLY") else 1
    if not args.task and not any([args.file, args.symbol, args.module, args.route]):
        parser.print_help()
        return 2
    manifest, _cache_status = build_manifest(
        args.task,
        file=args.file,
        symbol=args.symbol,
        module=args.module,
        route_key=args.route,
        domain=args.domain,
        modify=args.modify,
        read_only=args.read_only,
        use_cache=not args.no_cache,
    )
    if args.read is not None:
        if not args.read:
            subset = {
                "status": manifest["status"],
                "read": manifest.get("read", []),
                "hashes": manifest.get("hashes", {}),
            }
            print(json.dumps(subset, indent=2, sort_keys=True))
            return 0
        result = read_entry(manifest, args.read)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"READ {result.get('file', args.read)} [{result.get('status', '?')}]")
            for region in result.get("regions", []):
                print(
                    f"--- {region.get('qualified', '?')} L{region.get('line')}-{region.get('end')}"
                )
                print(region.get("text", ""))
        return 0 if result.get("status") == "READY" else 1
    if args.tests:
        print(
            json.dumps(
                {"status": manifest["status"], "tests": manifest.get("tests", [])},
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if manifest["status"] in ("READY", "READ_ONLY") else 1
    if args.validate:
        if args.run:
            if manifest["status"] not in ("READY", "READ_ONLY"):
                print(
                    json.dumps({"status": manifest["status"], "reason": manifest.get("reason", "")})
                )
                return 1
            result = run_validation(manifest)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get("success") else 1
        print(
            json.dumps(
                {"status": manifest["status"], "validate": manifest.get("validate", {})},
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if manifest["status"] in ("READY", "READ_ONLY") else 1
    if args.check_edit:
        result = check_edit(manifest, args.check_edit, args.symbol_check)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("verdict") == "ALLOW" else 1
    if args.plan:
        print("\n".join(workflow_plan(manifest)))
        return 0
    if args.json:
        print(json.dumps(manifest, sort_keys=True))
    else:
        print(render_text(manifest))
    codes = {
        "READY": 0,
        "READ_ONLY": 0,
        "EXECUTION_COMPLETE": 0,
        "BLOCKED": 1,
        "STALE": 1,
        "UNKNOWN": 2,
        "NEEDS_CLARIFICATION": 3,
        "TEST_TARGET_MISSING": 1,
        "VALIDATION_UNAVAILABLE": 1,
    }
    return codes[manifest["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
