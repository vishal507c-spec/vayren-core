"""Context & execution engine (Phase 7).

Task -> route (Phase 5, reused) -> minimal context packet -> execution plan ->
targeted validation. Deterministic, compact, machine-readable. No repository
rediscovery per task; unknown tasks use controlled discovery (never guessed).

Levels: L0 task+route, L1 +primary files/symbols (default), L2 +deps/contracts/
callers/tests, L3 +change-surface/plan/forbidden/full validation.
Scopes (validation escalation): internal < contract < ownership < full.

Usage:
    python scripts/context_engine.py --task "add strategy parameter"
    python scripts/context_engine.py --task "x" --level L2 --scope contract --json
    python scripts/context_engine.py --task "x" --modify 05_strategy/strategy/sma.py
    python scripts/context_engine.py --check
    python scripts/context_engine.py --scope-check --planned a.py,b.py
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from route import load_routes, match_route, unknown_package  # noqa: E402

ROOT = SCRIPTS_DIR.parent
ROUTES_FILE = ROOT / "90_brain" / "task_routes.json"
POLICY_FILE = ROOT / "90_brain" / "ownership_policy.json"
CACHE_DIR = ROOT / ".context_cache"

LEVELS = ("L0", "L1", "L2", "L3")
SCOPES = ("internal", "contract", "ownership", "full")
CHAPTERS = (
    "00_app",
    "01_core",
    "02_data",
    "03_market",
    "05_strategy",
    "06_backtest",
    "07_risk",
    "08_execution",
    "09_broker",
)

SYMBOL_DEF = re.compile(
    r"^\s*(?:pub\s+)?(?:async\s+)?(def|class|fn|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
)

# Scope -> validation level (deterministic escalation, existing commands only).
VALIDATION_LEVELS = {
    "internal": ["ruff check <files>", "ruff format --check <files>", "<targeted-tests>"],
    "contract": [
        "<targeted-tests>",
        "validate_imports",
        "validate_authority",
        "validate_routes",
        "language_ownership",
    ],
    "ownership": [
        "architecture_gate",
        "validate_authority",
        "validate_routes",
        "language_ownership",
        "validate_imports",
    ],
    "full": ["make check", "cargo test -p vayren-core --lib", "full pytest"],
}


def resolve_symbol(name: str, source: str) -> int | None:
    """Pure: 1-based definition line of a symbol in source text, else None."""
    for lineno, line in enumerate(source.splitlines(), 1):
        match = SYMBOL_DEF.match(line)
        if match and match.group(2) == name:
            return lineno
    return None


def find_callers(name: str, defining_file: str, root: Path = ROOT) -> list[str]:
    """Targeted single-grep caller lookup (product chapters only, def file excluded)."""
    try:
        proc = subprocess.run(
            ["git", "grep", "-l", "-E", "-e", name, "--", *CHAPTERS],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return sorted(
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip() and line.strip() != defining_file and "/tests/" not in line
    )[:10]


def symbol_snippet(source: str, line: int | None, window: int) -> str:
    """Pure: window lines around a 1-based definition line (bounded excerpt)."""
    if not line or window <= 0:
        return ""
    window = min(window, 40)
    lines = source.splitlines()
    start = max(0, line - 1 - window)
    return "\n".join(f"{n + 1}: {lines[n]}" for n in range(start, min(len(lines), line + window)))


def symbol_context(route: dict, root: Path = ROOT, window: int = 0) -> dict[str, dict]:
    """Resolve every route symbol: file, line, callers, contract surface."""
    resolved: dict[str, dict] = {}
    for mod_path, names in route.get("symbols", {}).items():
        path = root / mod_path
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            resolved.update({n: {"file": mod_path, "line": None} for n in names})
            continue
        for name in names:
            line = resolve_symbol(name, source)
            entry: dict = {
                "file": mod_path,
                "line": line,
                "callers": find_callers(name, mod_path, root) if line else [],
            }
            snippet = symbol_snippet(source, line, window)
            if snippet:
                entry["snippet"] = snippet
            resolved[name] = entry
    return resolved


def change_surface(route: dict) -> dict:
    """Change-surface block straight from route metadata (no inference)."""
    cs = route.get("change_surface", {})
    tests = route.get("tests", {})
    return {
        "likely_changed": cs.get("likely_changed", []),
        "affected_interfaces": cs.get("affected_interfaces", []),
        "affected_modules": cs.get("affected_modules", []),
        "required_tests": cs.get("required_tests", tests.get("command", tests.get("note", ""))),
    }


def select_tests(route: dict, scope: str) -> dict:
    """Targeted tests first; escalate only by scope. Reports its reason."""
    tests = route.get("tests", {})
    targeted = tests.get("command", tests.get("note", "validators only"))
    if scope == "internal":
        return {
            "targeted": targeted,
            "escalated": None,
            "reason": "isolated scope: module tests only",
        }
    if scope == "contract":
        return {
            "targeted": targeted,
            "escalated": "validators (imports/authority/routes/language)",
            "reason": "interface scope: contract validators added",
        }
    if scope == "ownership":
        return {
            "targeted": targeted,
            "escalated": "full architecture gate + authority + routes + language",
            "reason": "ownership scope: complete gate required",
        }
    return {
        "targeted": "full pytest + cargo test -p vayren-core --lib",
        "escalated": "make check",
        "reason": "full scope: global validation required",
    }


def escalate(scope: str, drift: bool) -> tuple[str, list[str]]:
    """Validation level for a scope; scope drift escalates exactly one level."""
    order = ["internal", "contract", "ownership", "full"]
    level = scope if scope in order else "internal"
    if drift:
        level = order[min(order.index(level) + 1, 3)]
    commands = list(VALIDATION_LEVELS[level])
    return level, commands


def safety_check(route: dict, targets: list[str], root: Path = ROOT) -> list[dict]:
    """Pre-modification verification per target. Empty list = safe to plan."""
    blocked: list[dict] = []
    language = str(route.get("language", ""))
    allowed_exts = {
        "PYTHON": {".py"},
        "RUST": {".rs"},
        "SLINT": {".slint"},
        "PYTHON+RUST": {".py", ".rs"},
        "RUST+SLINT": {".rs", ".slint"},
    }
    for target in targets:
        if not (root / target).is_file():
            blocked.append(
                {
                    "target": target,
                    "reason": "target file does not exist",
                    "fix": f"route to an existing file (see {route.get('key')} primary_files)",
                }
            )
            continue
        if any(fnmatch.fnmatch(target, pat) for pat in route.get("forbidden", [])):
            blocked.append(
                {
                    "target": target,
                    "reason": "target is in route forbidden areas",
                    "fix": "pick a target inside the route change surface",
                }
            )
            continue
        if Path(target).suffix not in allowed_exts.get(language, set()):
            blocked.append(
                {
                    "target": target,
                    "reason": f"target language drifts from route {language}",
                    "fix": "match the route language or file a policy change",
                }
            )
    return blocked


def scope_check(planned: list[str], actual: list[str], forbidden: list[str]) -> dict:
    """Planned vs actual changed files. Block on forbidden, escalate on drift."""
    planned_set, actual_set = set(planned), set(actual)
    unexpected = sorted(actual_set - planned_set)
    forbidden_hit = sorted(u for u in unexpected if any(fnmatch.fnmatch(u, p) for p in forbidden))
    if forbidden_hit:
        return {
            "planned": sorted(planned_set),
            "actual": sorted(actual_set),
            "unexpected": unexpected,
            "action": "block",
        }
    if unexpected:
        return {
            "planned": sorted(planned_set),
            "actual": sorted(actual_set),
            "unexpected": unexpected,
            "action": "escalate",
        }
    return {
        "planned": sorted(planned_set),
        "actual": sorted(actual_set),
        "unexpected": [],
        "action": "ok",
    }


def _cache_key(task: str, level: str, scope: str, window: int = 0) -> str:
    digest = hashlib.sha1()
    digest.update(" ".join(task.lower().split()).encode())
    digest.update(f"{level}/{scope}/{window}".encode())
    for path in (ROUTES_FILE, POLICY_FILE):
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()[:16]


def build_packet(
    task: str,
    routes: list[dict],
    level: str = "L1",
    scope: str = "internal",
    use_cache: bool = True,
    window: int = 0,
) -> tuple[dict, str]:
    """Deterministic packet builder. Returns (packet, cache_status)."""
    started = time.perf_counter()
    if level not in LEVELS:
        return (
            {
                "status": "CONTEXT FAIL",
                "rule": "bad-level",
                "task": task,
                "reason": f"unknown level {level}",
                "fix": f"use one of {LEVELS}",
            },
            "miss",
        )
    if scope not in SCOPES:
        return (
            {
                "status": "CONTEXT FAIL",
                "rule": "bad-scope",
                "task": task,
                "reason": f"unknown scope {scope}",
                "fix": f"use one of {SCOPES}",
            },
            "miss",
        )
    key = _cache_key(task, level, scope, window)
    cache_file = CACHE_DIR / f"{key}.json"
    if use_cache and cache_file.is_file():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8")), "hit"
        except (OSError, ValueError):
            pass
    route, how = match_route(task, routes)
    if route is None:
        packet = {"status": "UNKNOWN", **unknown_package(task)}
        _store_cache(cache_file, packet, use_cache)
        return packet, "miss"
    packet = {
        "status": "OK",
        "task": " ".join(task.split()),
        "matched_by": how,
        "route": route.get("key"),
        "owner": route.get("owner_module"),
        "language": route.get("language"),
        "domain": route.get("domain"),
    }
    if level in ("L1", "L2", "L3"):
        packet["primary_files"] = route.get("primary_files", [])
        packet["symbols"] = symbol_context(route, window=window)
    if level in ("L2", "L3"):
        packet["secondary_files"] = route.get("secondary_files", [])
        packet["depends_on"] = route.get("depends_on", [])
        packet["depended_on_by"] = route.get("depended_on_by", [])
        packet["contract"] = route.get("contract", "")
        packet["tests"] = select_tests(route, scope)
    if level == "L3":
        packet["change_surface"] = change_surface(route)
        packet["forbidden"] = route.get("forbidden", [])
        _, commands = escalate(scope, drift=False)
        packet["validation"] = commands
    else:
        packet["forbidden"] = route.get("forbidden", [])
    packet["meta"] = {
        "level": level,
        "scope": scope,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    _store_cache(cache_file, packet, use_cache)
    return packet, "miss"


def _store_cache(cache_file: Path, packet: dict, use_cache: bool) -> None:
    if not use_cache:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    except OSError:
        pass


def execution_plan(packet: dict, modify: list[str]) -> dict:
    """READ/MODIFY/TEST/VALIDATE/FORBIDDEN plan from packet + safety check."""
    if packet.get("status") != "OK":
        return packet
    return {
        "READ": [
            f"{s['file']}:{s['line']}" for s in packet.get("symbols", {}).values() if s.get("line")
        ]
        + packet.get("primary_files", [])[:3],
        "MODIFY": modify,
        "TEST": packet.get("tests", {}).get("targeted", packet.get("tests", "")),
        "VALIDATE": packet.get("validation", ["<targeted-tests>"]),
        "FORBIDDEN": packet.get("forbidden", []),
    }


def render_handoff(packet: dict) -> str:
    """Compact §13 handoff text."""
    if packet.get("status") == "UNKNOWN":
        lines = [f"TASK\n{packet.get('task')} -> UNKNOWN", "", "ROUTE", "controlled discovery:"]
        lines.extend(f"- {step}" for step in packet.get("next", []))
        return "\n".join(lines)
    if packet.get("status") != "OK":
        return (
            "CONTEXT FAIL\n\n"
            f"rule: {packet.get('rule')}\n"
            f"task: {packet.get('task')}\n"
            f"reason: {packet.get('reason')}\n"
            f"fix: {packet.get('fix')}"
        )
    lines = [
        "TASK",
        str(packet.get("task")),
        "",
        f"ROUTE\n{packet.get('route')} ({packet.get('matched_by')})",
        "",
        f"OWNER\n{packet.get('owner')}",
        "",
        f"LANGUAGE\n{packet.get('language')}",
        "",
        "READ",
    ]
    for path in packet.get("primary_files", []):
        lines.append(f"- {path}")
    for name, info in packet.get("symbols", {}).items():
        if info.get("line"):
            lines.append(f"- {info['file']}:{info['line']} ({name})")
    if "change_surface" in packet:
        lines += [
            "",
            "CHANGE",
            f"- likely: {', '.join(packet['change_surface'].get('likely_changed', []))}",
        ]
    if "tests" in packet:
        tests = packet["tests"]
        lines += ["", "TEST", f"- {tests.get('targeted') if isinstance(tests, dict) else tests}"]
    if "validation" in packet:
        lines += ["", "VALIDATE"] + [f"- {c}" for c in packet["validation"]]
    lines += ["", "FORBIDDEN"] + [f"- {f}" for f in packet.get("forbidden", [])]
    return "\n".join(lines)


def self_check(routes: list[dict] | None = None) -> list[str]:
    """Engine + metadata self-validation (CI): schema, resolvability, determinism."""
    routes = load_routes() if routes is None else routes
    errors: list[str] = []
    required = {"key", "owner_module", "language", "primary_files", "validation", "forbidden"}
    for route in routes:
        key = str(route.get("key", "?"))
        missing = required - set(route.keys())
        if missing:
            errors.append(f"{key}: packet schema missing {sorted(missing)}")
        packet, _ = build_packet(key, routes, level="L3", use_cache=False)
        if packet.get("status") != "OK":
            errors.append(f"{key}: self-route failed")
            continue
        for name, info in packet.get("symbols", {}).items():
            if info.get("line") is None:
                errors.append(f"{key}: symbol '{name}' has no line")
        again, _ = build_packet(key, routes, level="L3", use_cache=False)
        content = {k: v for k, v in packet.items() if k != "meta"}
        content_again = {k: v for k, v in again.items() if k != "meta"}
        if content_again != content:
            errors.append(f"{key}: nondeterministic packet")
    if not routes:
        errors.append("no routes loaded")
    return errors


def _actual_changed(root: Path = ROOT) -> list[str]:
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        others = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
    except OSError:
        return []
    return sorted({line.strip() for line in diff + others if line.strip()})


def validate_packet(packet: dict, root: Path = ROOT) -> list[str]:
    """Verify a generated packet is still valid (stale content fails loudly)."""
    problems: list[str] = []
    if packet.get("status") != "OK":
        return ["packet status is not OK"]
    routes = load_routes()
    route, _ = match_route(str(packet.get("task", "")), routes)
    if route is None or route.get("key") != packet.get("route"):
        problems.append("route no longer resolves to the packet route")
        return problems
    for path in packet.get("primary_files", []):
        if not (root / path).is_file():
            problems.append(f"primary file gone: {path}")
    for name, info in packet.get("symbols", {}).items():
        if info.get("line") is None:
            problems.append(f"symbol unresolved: {name}")
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False
        ).stdout.splitlines()
        existing = {line.strip() for line in tracked if line.strip()}
    except OSError:
        existing = set()
    for pattern in packet.get("forbidden", []):
        if existing and not fnmatch.filter(existing, pattern):
            problems.append(f"forbidden pattern matches nothing: {pattern}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a minimal task context packet")
    parser.add_argument("--task", default="")
    parser.add_argument("--level", default="L1", choices=list(LEVELS))
    parser.add_argument("--scope", default="internal", choices=list(SCOPES))
    parser.add_argument("--modify", default="", help="comma-separated planned files")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--scope-check", action="store_true")
    parser.add_argument(
        "--planned", default="", help="comma-separated planned files for scope check"
    )
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--context-lines",
        type=int,
        default=0,
        help="definition-window lines per symbol (0 = file:line only, max 40)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="verify the generated packet is still valid (route fresh, files/symbols resolve)",
    )
    args = parser.parse_args(argv)
    routes = load_routes()

    if args.check:
        errors = self_check(routes)
        if errors:
            print("CONTEXT FAIL")
            for error in errors[:20]:
                print(f"  - {error}")
            return 1
        print(f"Context self-check PASSED ({len(routes)} routes)")
        return 0

    if args.scope_check:
        planned = [p.strip() for p in args.planned.split(",") if p.strip()]
        route, _ = match_route(args.task, routes)
        forbidden = route.get("forbidden", []) if route else []
        result = scope_check(planned, _actual_changed(), forbidden)
        if result["action"] == "block":
            print("SCOPE DRIFT")
            print(f"planned: {result['planned']}")
            print(f"actual: {result['actual']}")
            print(f"unexpected: {result['unexpected']}")
            print("action: block (forbidden area touched)")
            return 1
        if result["action"] == "escalate":
            print("SCOPE DRIFT")
            print(f"planned: {result['planned']}")
            print(f"actual: {result['actual']}")
            print(f"unexpected: {result['unexpected']}")
            print("action: escalate validation one level")
            return 0
        print("scope OK (no drift)")
        return 0

    packet, cache = build_packet(
        args.task, routes, args.level, args.scope, not args.no_cache, args.context_lines
    )
    packet["meta"] = {**packet.get("meta", {}), "cache": cache}
    if packet.get("status") == "UNKNOWN":
        print(render_handoff(packet) if not args.json else json.dumps(packet, indent=2))
        return 2
    if packet.get("status") != "OK":
        print(render_handoff(packet))
        return 1
    if args.validate:
        problems = validate_packet(packet)
        if problems:
            print("CONTEXT FAIL")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"context valid ({packet.get('route')}, {args.level}/{args.scope})")
        return 0
    modify = [m.strip() for m in args.modify.split(",") if m.strip()]
    if modify:
        route, _ = match_route(args.task, routes)
        blocked = safety_check(route or {}, modify) if route else []
        if blocked:
            first = blocked[0]
            print("EXECUTION BLOCKED")
            print(f"reason: {first['reason']} ({first['target']})")
            print("canonical: route change surface")
            print(f"fix: {first['fix']}")
            return 1
        # Boundary auto-escalation (§7): modify targets spanning languages can
        # never stay at internal scope. Single-language modifies keep the
        # requested scope (the route's own validation already covers them).
        target_langs = {Path(m).suffix for m in modify}
        if args.scope == "internal" and len(target_langs) > 1:
            args.scope = "contract"
            packet, _ = build_packet(
                args.task, routes, args.level, args.scope, not args.no_cache, args.context_lines
            )
            print("note: boundary crossed -> validation escalated to contract")
    if args.explain:
        route, how = match_route(args.task, routes)
        print(f"task normalized: '{' '.join(args.task.split()).lower()}'")
        print(f"route: {route.get('key') if route else None} (matched by {how})")
        print(render_handoff(packet) if not args.json else json.dumps(packet, indent=2))
        return 0
    if args.plan:
        output = execution_plan(packet, modify)
        print(json.dumps(output, indent=2) if args.json else render_handoff(packet))
        return 0
    print(json.dumps(packet, indent=2) if args.json else render_handoff(packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
