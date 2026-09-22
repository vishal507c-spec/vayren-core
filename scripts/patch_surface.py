"""Intent -> exact patch surface (Phase 3).

Deterministic consumer (no new authority, no repository search):
  task_routes.json (canonical routing via route.py match)
+ repo graph / warm index (files, symbols, lines, callers, deps, tests)
+ ownership policy + language retention (ownership/language gate)

Resolution priority: explicit file > explicit symbol > explicit module >
exact route > deterministic intent match > UNKNOWN / NEEDS_CLARIFICATION.

Usage:
    python scripts/patch_surface.py --task "add validation to StrategyRegistry"
    python scripts/patch_surface.py --task "..." --json
    python scripts/patch_surface.py --symbol BrokerRegistry --json
    python scripts/patch_surface.py --task "..." --modify 09_broker/broker/registry.py
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_graph  # noqa: E402
import repo_index  # noqa: E402
from route import load_routes, match_route  # noqa: E402

ROOT = SCRIPTS_DIR.parent

# Allowed file extensions per route language (same contract as
# context_engine.safety_check: a target outside these is blocked).
LANGUAGE_EXTENSIONS = {
    "PYTHON": {".py"},
    "RUST": {".rs"},
    "SLINT": {".slint"},
    "PYTHON+RUST": {".py", ".rs"},
    "RUST+SLINT": {".rs", ".slint"},
}

# Scope -> extra governance validators (existing scripts only; route.validation
# commands are always included verbatim first).
SCOPE_VALIDATORS = {
    "internal": [],
    "contract": ["scripts/validate_routes.py", "scripts/validate_authority.py"],
    "ownership": [
        "scripts/validate_language_ownership.py",
        "scripts/validate_architecture_gate.py",
        "scripts/validate_authority.py",
    ],
    "full": [
        "scripts/validate_imports.py",
        "scripts/validate_language_ownership.py",
        "scripts/validate_architecture_gate.py",
        "scripts/validate_authority.py",
        "scripts/validate_routes.py",
    ],
}

EXPLICIT_RE = re.compile(r"\b(file|symbol|module|route|domain):(\S+)")


class PatchError(Exception):
    """Resolver failure — caller maps to UNKNOWN, never to a search fallback."""


def parse_intent(
    task: str,
    *,
    file: str | None = None,
    symbol: str | None = None,
    module: str | None = None,
    route: str | None = None,
    domain: str | None = None,
) -> dict:
    """Explicit markers (flags beat inline `kind:value` tokens) + normalized text."""
    tokens = dict(EXPLICIT_RE.findall(task))
    return {
        "task": task,
        "want": " ".join(task.lower().split()),
        "file": file or tokens.get("file"),
        "symbol": symbol or tokens.get("symbol"),
        "module": module or tokens.get("module"),
        "route": route or tokens.get("route"),
        "domain": domain or tokens.get("domain"),
    }


def candidate_scores(task: str, routes: list[dict]) -> list[tuple[dict, tuple[int, int]]]:
    """All matching routes with (score, width). Mirrors route.match_route priority.

    Kept consistent with route.py by contract (exact key > exact alias >
    alias substring; longest alias wins); agreement is pinned by tests.
    """
    want = " ".join(task.lower().split())
    scored: list[tuple[dict, tuple[int, int]]] = []
    if not want:
        return scored
    for route in routes:
        key = str(route.get("key", "")).lower()
        aliases = [str(a).lower() for a in route.get("aliases", []) if a]
        if want == key:
            scored.append((route, (3, len(key))))
        elif want in aliases:
            scored.append((route, (2, len(want))))
        else:
            hits = [len(a) for a in aliases if a and (want in a or a in want)]
            if hits:
                scored.append((route, (1, max(hits))))
    return scored


def detect_ambiguity(task: str, routes: list[dict], winner: dict | None) -> list[dict]:
    """Routes tied with the winner at (score, width) under different owners."""
    scored = candidate_scores(task, routes)
    if not scored or winner is None:
        return []
    top = max(score for _, score in scored)
    tied = [route for route, score in scored if score == top]
    owners = {str(route.get("owner_module", "")) for route in tied}
    if len(tied) > 1 and len(owners) > 1:
        return sorted(tied, key=lambda r: str(r.get("key", "")))
    return []


def _policy_rules() -> list[dict]:
    try:
        policy = json.loads(repo_graph.POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PatchError(f"cannot read ownership policy: {exc}") from exc
    rules = policy.get("rules", [])
    if not isinstance(rules, list):
        raise PatchError("ownership policy has no rules list")
    return [r for r in rules if isinstance(r, dict)]


def _retention_files() -> dict:
    try:
        data = json.loads(repo_graph.RETENTION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files", {}) if isinstance(data, dict) else {}
    return files if isinstance(files, dict) else {}


def _warm_payload() -> dict:
    try:
        return repo_index.ensure_fresh()
    except (
        repo_index.IndexError,
        repo_index.IndexLockedError,
        repo_graph.GraphError,
        OSError,
        ValueError,
    ) as exc:
        raise PatchError(f"stale index, refresh failed: {exc}") from exc


def _file_owners(graph: dict) -> dict[str, str]:
    return {f["path"]: f["owner"] for f in graph.get("files", [])}


def _symbol_evidence(
    symbol: dict, file_owners: dict[str, str], route_listed: set[str] | None = None
) -> dict:
    return {
        "qualified": symbol["qualified"],
        "name": symbol["name"],
        "file": symbol["file"],
        "line": symbol["line"],
        "kind": symbol["kind"],
        "public": symbol["public"],
        "module": symbol["module"],
        "owner": file_owners.get(symbol["file"], "UNKNOWN"),
        "callers": sorted(symbol.get("called_by", [])),
        "caller_count": len(symbol.get("called_by", [])),
        "tests": sorted(symbol.get("tests", [])),
        "route_listed": symbol["name"] in (route_listed or set()),
    }


def resolve_symbol_targets(payload: dict, name: str) -> list[dict]:
    """Index symbol lookup with per-symbol evidence (lines always current)."""
    hits = repo_index.query_with_maps(payload, "symbol", name)
    if not isinstance(hits, list) or not hits:
        raise PatchError(f"symbol not found in index: {name}")
    owners = _file_owners(payload["graph"])
    return [_symbol_evidence(symbol, owners) for symbol in hits]


def resolve_file_targets(
    payload: dict, path: str, route_listed: set[str] | None = None
) -> list[dict]:
    """Exact file target: record must exist in the graph, else UNKNOWN."""
    record = repo_graph.query_file(payload["graph"], path)
    if record is None:
        raise PatchError(f"file not in index: {path}")
    owners = _file_owners(payload["graph"])
    symbols = sorted(
        (s for s in payload["graph"].get("symbols", []) if s["file"] == path),
        key=lambda s: (s["line"], s["qualified"]),
    )
    return [
        {
            "file": path,
            "module": record["module"],
            "owner": record["owner"],
            "language": record["language"],
            "is_test": record["is_test"],
            "symbols": [_symbol_evidence(symbol, owners, route_listed) for symbol in symbols],
        }
    ]


def resolve_module_targets(payload: dict, module_id: str) -> list[dict]:
    """Module target: every non-test file with its symbols (bounded, sorted)."""
    module = repo_graph.query_module(payload["graph"], module_id)
    if module is None:
        raise PatchError(f"module not in index: {module_id}")
    return [
        resolve_file_targets(payload, path)[0]
        for path in sorted(module["files"])
        if not repo_graph.is_test_file(path)
    ]


def resolve_route_targets(payload: dict, route: dict) -> tuple[list[dict], list[str]]:
    """Route primary files + route symbols resolved via the index.

    Returns (targets, missing):     missing names are reported explicitly,
    never silently invented.
    """
    targets: list[dict] = []
    missing: list[str] = []
    seen_files: set[str] = set()
    listed_by_file: dict[str, set[str]] = {}
    for file_path, names in route.get("symbols", {}).items():
        listed_by_file.setdefault(str(file_path), set()).update(str(n) for n in names)
    for path in route.get("primary_files", []) + route.get("secondary_files", []):
        if path in seen_files:
            continue
        seen_files.add(path)
        try:
            targets.extend(resolve_file_targets(payload, str(path), listed_by_file.get(str(path))))
        except PatchError:
            missing.append(f"file:{path}")
    for file_path, names in route.get("symbols", {}).items():
        for name in names:
            hits = repo_index.query_with_maps(payload, "symbol", str(name))
            if not isinstance(hits, list):
                missing.append(f"symbol:{name}")
                continue
            scoped = [s for s in hits if s["file"] == file_path] or hits
            for symbol in scoped:
                if symbol["file"] not in seen_files:
                    seen_files.add(symbol["file"])
                    targets.extend(
                        resolve_file_targets(
                            payload, symbol["file"], listed_by_file.get(symbol["file"], {str(name)})
                        )
                    )
    targets.sort(key=lambda t: t["file"])
    return targets, sorted(set(missing))


def build_impact(payload: dict, targets: list[dict], route: dict | None) -> dict:
    """Callers, deps, dependents, affected modules/interfaces from graph edges."""
    graph = payload["graph"]
    target_files = sorted({t["file"] for t in targets})
    target_modules = sorted({t["module"] for t in targets})
    caller_quails = sorted({c for t in targets for s in t["symbols"] for c in s["callers"]})
    by_qual = {s["qualified"]: s for s in graph.get("symbols", [])}
    caller_files = sorted({by_qual[q]["file"] for q in caller_quails if q in by_qual})
    caller_modules = sorted({by_qual[q]["module"] for q in caller_quails if q in by_qual})
    dependencies: dict[str, list[str]] = {}
    dependents: dict[str, list[str]] = {}
    dependents_actual: dict[str, list[str]] = {}
    for mid in target_modules:
        module = repo_graph.query_module(graph, mid)
        if module is None:
            continue
        dependencies[mid] = sorted(
            set(module.get("depends_on_declared", [])) | set(module.get("depends_on_actual", []))
        )
        dependents[mid] = sorted(module.get("depended_on_by", []))
    actual_edges = {
        (e["from"], e["to"]) for e in graph.get("dependencies", []) if e["kind"] == "actual"
    }
    for mid in target_modules:
        dependents_actual[mid] = sorted({src for (src, dst) in actual_edges if dst == mid})
    affected_modules = sorted(
        set(target_modules)
        | set(caller_modules)
        | {src for ds in dependents_actual.values() for src in ds}
    )
    public_symbols = sorted({s["qualified"] for t in targets for s in t["symbols"] if s["public"]})
    route_interfaces: list[str] = []
    if route is not None:
        surface = route.get("change_surface", {})
        if isinstance(surface, dict):
            route_interfaces = sorted(str(i) for i in surface.get("affected_interfaces", []))
    return {
        "target_files": target_files,
        "target_modules": target_modules,
        "callers": caller_quails,
        "caller_files": caller_files,
        "caller_modules": caller_modules,
        "dependencies": dependencies,
        "dependents": dependents,
        "dependents_actual": dependents_actual,
        "affected_modules": affected_modules,
        "affected_interfaces": sorted(set(public_symbols) | set(route_interfaces)),
    }


def build_tests(payload: dict, targets: list[dict], route: dict | None) -> dict:
    """Test resolution priority: symbol > file > module > route > validators."""
    graph = payload["graph"]
    direct = sorted({t for target in targets for s in target["symbols"] for t in s["tests"]})
    target_files = {t["file"] for t in targets}
    file_tests = sorted(
        {
            test["path"]
            for test in graph.get("tests", [])
            if target_files & set(test.get("targets", []))
        }
    )
    module_names = sorted({t["module"] for t in targets})
    module_tests = sorted(
        {test["path"] for test in graph.get("tests", []) if test.get("module") in module_names}
    )
    route_tests: dict = {"files": [], "command": None, "note": None}
    if route is not None:
        tests = route.get("tests", {})
        if isinstance(tests, dict):
            route_tests = {
                "files": sorted(str(f) for f in tests.get("files", [])),
                "command": tests.get("command"),
                "note": tests.get("note"),
            }
    return {
        "direct": direct,
        "file": file_tests,
        "module": module_tests,
        "route": route_tests,
    }


def expand_forbidden(graph: dict, patterns: list[str], cap: int = 20) -> dict:
    """Resolve route forbidden globs against indexed files (bounded, counted)."""
    files = [f["path"] for f in graph.get("files", [])]
    matched = {
        path for path in files for pattern in patterns if fnmatch.fnmatch(path, str(pattern))
    }
    ordered = sorted(matched)
    return {
        "patterns": sorted(str(p) for p in patterns),
        "files": ordered[:cap],
        "file_count": len(ordered),
        "truncated": len(ordered) > cap,
    }


def ownership_gate(
    route: dict | None,
    targets: list[dict],
    modify: list[str],
    rules: list[dict],
    retention: dict,
) -> dict:
    """BLOCKED when a target violates existence/forbidden/language/ownership.

    Returns {"blocked": bool, "violations": [...]}; each violation carries the
    requested target, canonical owner/language, reason, and allowed location.
    """
    violations: list[dict] = []
    language = str((route or {}).get("language", ""))
    allowed_exts = LANGUAGE_EXTENSIONS.get(language, set()) if route else None
    forbidden = list((route or {}).get("forbidden", []))
    rust_primaries = [
        str(f)
        for f in (route or {}).get("primary_files", [])
        if Path(str(f)).suffix in (".rs", ".slint")
    ]

    def check(path: str, kind: str) -> None:
        rule_id, _domain, required = repo_graph.classify(path, rules)
        suffix = Path(path).suffix
        if any(fnmatch.fnmatch(path, str(pat)) for pat in forbidden):
            violations.append(
                {
                    "target": path,
                    "kind": kind,
                    "canonical_owner": rule_id or "UNKNOWN",
                    "canonical_language": required or "UNKNOWN",
                    "reason": "target is in route forbidden areas",
                    "allowed": "pick a target inside the route change surface",
                }
            )
            return
        entry = retention.get(path, {}) if suffix == ".py" else {}
        covered = isinstance(entry, dict) and entry.get("state") in (
            "TEMPORARILY_RETAINED",
            "EXEMPT_WITH_JUSTIFICATION",
        )
        if suffix == ".py" and required in ("RUST", "RUST_SLINT") and not covered:
            state = entry.get("state", "no retention entry") if isinstance(entry, dict) else "?"
            allowed = (entry.get("migration_target", "") if isinstance(entry, dict) else "") or (
                rust_primaries[0] if rust_primaries else "rust/vayren-core/src/"
            )
            violations.append(
                {
                    "target": path,
                    "kind": kind,
                    "canonical_owner": rule_id or "UNKNOWN",
                    "canonical_language": required or "UNKNOWN",
                    "reason": (
                        "Python modification inside Rust-owned authority "
                        f"({state}; touch-to-migrate applies)"
                    ),
                    "allowed": allowed,
                }
            )
            return
        if suffix in (".rs", ".slint") and required == "PYTHON":
            violations.append(
                {
                    "target": path,
                    "kind": kind,
                    "canonical_owner": rule_id or "UNKNOWN",
                    "canonical_language": required or "UNKNOWN",
                    "reason": "Rust/Slint modification inside Python-owned authority",
                    "allowed": "Python module owning this logic",
                }
            )
            return
        if allowed_exts is not None and suffix not in allowed_exts and not covered:
            violations.append(
                {
                    "target": path,
                    "kind": kind,
                    "canonical_owner": rule_id or "UNKNOWN",
                    "canonical_language": required or language or "UNKNOWN",
                    "reason": f"extension {suffix} outside route language {language}",
                    "allowed": f"targets with {sorted(allowed_exts)}",
                }
            )

    for target in targets:
        check(target["file"], "target")
    for path in modify:
        check(path, "modify")
    return {"blocked": bool(violations), "violations": violations}


def validation_scope(targets: list[dict], impact: dict, route: dict | None) -> str:
    """Deterministic escalation: internal < contract < ownership < full.

    RUST+SLINT counts as one native-UI authority; only a Python x Rust/Slint
    mix (or a PYTHON+RUST route) is multi-language.
    """
    route_language = str((route or {}).get("language", ""))
    suffixes = {Path(f).suffix for f in impact["target_files"]}
    multi_language = route_language == "PYTHON+RUST" or (
        ".py" in suffixes and (".rs" in suffixes or ".slint" in suffixes)
    )
    cross_module = len(impact["target_modules"]) > 1 or any(
        m not in impact["target_modules"] for m in impact["caller_modules"]
    )
    touches_public = any(s["public"] for t in targets for s in t["symbols"])
    has_contract = bool(route and route.get("contract")) and (
        touches_public or cross_module or len(impact["affected_modules"]) > 2
    )
    if multi_language and len(impact["affected_modules"]) > 3:
        return "full"
    if multi_language:
        return "ownership"
    if has_contract:
        return "contract"
    return "internal"


def validation_plan(scope: str, route: dict | None, tests: dict) -> dict:
    """Route validation commands verbatim first, then scope governance extras."""
    commands: list[str] = []
    if route:
        commands.extend(str(c) for c in route.get("validation", []))
    seen = {" ".join(c.split()).replace("python ", "") for c in commands}
    for extra in SCOPE_VALIDATORS.get(scope, []):
        command = f"python {extra}"
        if extra not in seen and command not in commands:
            commands.append(command)
            seen.add(extra)
    test_commands: list[str] = []
    for path in tests["direct"][:5]:
        test_commands.append(f"pytest {path} -q")
    if not test_commands:
        for path in tests["module"][:5]:
            test_commands.append(f"pytest {path} -q")
    route_command = tests["route"].get("command")
    if route_command and route_command not in commands + test_commands:
        test_commands.append(str(route_command))
    return {"scope": scope, "commands": commands, "tests": test_commands}


def build_boundaries(
    targets: list[dict], impact: dict, tests: dict, route: dict | None, graph: dict
) -> dict:
    """MUST/MAY/MUST-NOT from ownership, route, and contract information."""
    must_change = sorted({t["file"] for t in targets} | set(tests["direct"][:10]))
    may_change = sorted(
        (set(impact["caller_files"]) | set(tests["module"][:10])) - set(must_change)
    )
    must_not = (
        expand_forbidden(graph, list((route or {}).get("forbidden", [])))
        if route
        else {"patterns": [], "files": [], "file_count": 0, "truncated": False}
    )
    return {"must_change": must_change, "may_change": may_change, "must_not_change": must_not}


def resolve(
    task: str = "",
    *,
    file: str | None = None,
    symbol: str | None = None,
    module: str | None = None,
    route_key: str | None = None,
    domain: str | None = None,
    modify: list[str] | None = None,
) -> dict:
    """Intent -> patch surface. Never searches; failures are structured."""
    intent = parse_intent(
        task, file=file, symbol=symbol, module=module, route=route_key, domain=domain
    )
    routes = load_routes()
    if not routes:
        return _unknown(intent, None, "no routes available", "add a route to task_routes.json")
    try:
        payload = _warm_payload()
    except PatchError as exc:
        return _unknown(intent, None, str(exc), "refresh the warm index and retry")
    graph = payload["graph"]

    kind = "intent"
    route: dict | None = None
    how = ""
    targets: list[dict] = []
    missing: list[str] = []
    try:
        if intent["file"]:
            kind = "file"
            targets = resolve_file_targets(payload, intent["file"])
            route = _route_for_module(routes, targets[0]["module"])
        elif intent["symbol"]:
            kind = "symbol"
            symbols = resolve_symbol_targets(payload, intent["symbol"])
            modules = {s["module"] for s in symbols}
            if len(modules) > 1 and ":" not in intent["symbol"]:
                return _needs_clarification(
                    intent,
                    None,
                    f"symbol matches {len(modules)} modules: {sorted(modules)}",
                    [f"--symbol {s['qualified']}" for s in symbols],
                    [s["qualified"] for s in symbols],
                )
            files = sorted({s["file"] for s in symbols})
            targets = [resolve_file_targets(payload, path)[0] for path in files]
            route = _route_for_module(routes, next(iter(modules)))
        elif intent["module"]:
            kind = "module"
            targets = resolve_module_targets(payload, intent["module"])
            route = _route_for_module(routes, intent["module"])
        else:
            if intent["route"]:
                kind = "route"
                route = next(
                    (r for r in routes if str(r.get("key", "")).lower() == intent["route"].lower()),
                    None,
                )
                if route is None:
                    return _unknown(
                        intent,
                        None,
                        f"unknown route key: {intent['route']}",
                        "list routes with scripts/route.py --list",
                    )
                how = "exact-key"
            else:
                winner, how = match_route(intent["want"] or intent["task"], routes)
                if winner is None:
                    from route import unknown_package  # noqa: E402

                    needed = unknown_package(intent["task"]).get("next", [])
                    return _unknown(intent, None, "no route matches this task", needed)
                route = winner
                rivals = detect_ambiguity(intent["want"] or intent["task"], routes, winner)
                if rivals:
                    return _needs_clarification(
                        intent,
                        route,
                        "multiple routes match equally well",
                        [str(r.get("key", "")) for r in rivals],
                        [str(r.get("owner_module", "")) for r in rivals],
                    )
            if domain_filter(intent, route):
                return _unknown(
                    intent,
                    route,
                    f"route domain {route.get('domain')} != requested {intent['domain']}",
                    "drop --domain or pick a route in that domain",
                )
            targets, missing = resolve_route_targets(payload, route)
            if not targets:
                return _unknown(
                    intent,
                    route,
                    f"route targets missing from index: {missing}",
                    "check route primary_files against the graph",
                )
    except PatchError as exc:
        owner = _route_owner_for_error(routes, intent)
        return _unknown(intent, owner, str(exc), "refine file/symbol/module spelling")

    rules = _policy_rules()
    retention = _retention_files()
    gate = ownership_gate(route, targets, modify or [], rules, retention)
    if gate["blocked"]:
        return _blocked(intent, route, targets, gate, payload)

    impact = build_impact(payload, targets, route)
    tests = build_tests(payload, targets, route)
    scope = validation_scope(targets, impact, route)
    plan = validation_plan(scope, route, tests)
    boundaries = build_boundaries(targets, impact, tests, route, graph)
    state = _state_evidence()
    return {
        "status": "RESOLVED",
        "intent": intent,
        "resolution": {"kind": kind, "by": how or kind},
        "route": _route_evidence(route),
        "targets": targets,
        "impact": impact,
        "tests": tests,
        "validation": plan,
        "safety": {
            "owner": sorted({t.get("owner", "") for t in targets}),
            "language": sorted({t.get("language", "") for t in targets}),
            "gate": gate,
        },
        "boundaries": boundaries,
        "missing": missing,
        "evidence": {
            "route_source": "90_brain/task_routes.json",
            "graph_source": "90_brain/repo_graph.json",
            "graph_inputs_hash": graph.get("inputs_hash"),
            "index_source": ".repo_index/lookup.pkl",
            "index_fingerprint": state.get("fingerprint"),
            "index_status": state.get("status"),
            "contract": (route or {}).get("contract", ""),
            "ownership_source": "90_brain/ownership_policy.json",
        },
    }


def _route_for_module(routes: list[dict], module_id: str) -> dict | None:
    hits = [
        r
        for r in routes
        if module_id in r.get("owner_paths", []) or r.get("owner_module") == module_id
    ]
    if not hits:
        hits = [
            r
            for r in routes
            if any(str(op).startswith(module_id + "/") for op in r.get("owner_paths", []))
        ]
    return sorted(hits, key=lambda r: str(r.get("key", "")))[0] if hits else None


def _route_owner_for_error(routes: list[dict], intent: dict) -> dict | None:
    if intent["module"]:
        return _route_for_module(routes, intent["module"])
    return None


def domain_filter(intent: dict, route: dict) -> bool:
    """True when an explicit domain request mismatches the resolved route."""
    return (
        bool(intent["domain"]) and str(route.get("domain", "")).lower() != intent["domain"].lower()
    )


def _route_evidence(route: dict | None) -> dict:
    if route is None:
        return {"key": None, "note": "no route (explicit file/symbol/module outside routing)"}
    return {
        "key": route.get("key"),
        "domain": route.get("domain"),
        "owner": route.get("owner_module"),
        "language": route.get("language"),
        "language_boundary": route.get("language_boundary", ""),
        "contract": route.get("contract", ""),
        "depends_on": route.get("depends_on", []),
        "forbidden": route.get("forbidden", []),
    }


def _state_evidence() -> dict:
    state = repo_index._read_json(repo_index.STATE_FILE)
    return state if isinstance(state, dict) else {}


def _unknown(intent: dict, route: dict | None, reason: str, needed: object) -> dict:
    return {
        "status": "UNKNOWN",
        "intent": intent,
        "resolution": {"kind": "unknown", "by": "no-match"},
        "route": _route_evidence(route),
        "targets": [],
        "impact": {},
        "tests": {},
        "validation": {},
        "safety": {},
        "boundaries": {},
        "missing": [],
        "evidence": {"route_source": "90_brain/task_routes.json"},
        "unknown": {"reason": reason, "needed": needed},
    }


def _needs_clarification(
    intent: dict, route: dict | None, reason: str, options: list[str], evidence: list[str]
) -> dict:
    packet = _unknown(intent, route, reason, options)
    packet["status"] = "NEEDS_CLARIFICATION"
    packet["resolution"] = {"kind": "ambiguous", "by": "tie"}
    packet["unknown"] = {"reason": reason, "needed": options, "candidates": evidence}
    return packet


def _blocked(
    intent: dict,
    route: dict | None,
    targets: list[dict],
    gate: dict,
    payload: dict,
) -> dict:
    graph = payload["graph"]
    impact = build_impact(payload, targets, route)
    tests = build_tests(payload, targets, route)
    state = _state_evidence()
    return {
        "status": "BLOCKED",
        "intent": intent,
        "resolution": {"kind": "blocked", "by": "ownership-gate"},
        "route": _route_evidence(route),
        "targets": targets,
        "impact": impact,
        "tests": tests,
        "validation": {"scope": "blocked", "commands": [], "tests": []},
        "safety": {
            "owner": sorted({t.get("owner", "") for t in targets}),
            "language": sorted({t.get("language", "") for t in targets}),
            "gate": gate,
        },
        "boundaries": build_boundaries(targets, impact, tests, route, graph),
        "missing": [],
        "evidence": {
            "route_source": "90_brain/task_routes.json",
            "graph_source": "90_brain/repo_graph.json",
            "graph_inputs_hash": graph.get("inputs_hash"),
            "index_source": ".repo_index/lookup.pkl",
            "index_fingerprint": state.get("fingerprint"),
            "contract": (route or {}).get("contract", ""),
            "ownership_source": "90_brain/ownership_policy.json",
        },
    }


def _substantive(paths: list[str]) -> list[str]:
    """Test paths with real suites first (empty `__init__.py` last). Display order only."""
    return sorted(paths, key=lambda p: ("/__init__.py" in p, p))


def render_text(packet: dict) -> str:
    """One compact screen: where to edit, what to run, what is forbidden."""
    status = packet.get("status", "UNKNOWN")
    lines = [f"STATUS: {status}"]
    intent = packet.get("intent", {})
    lines.append(f"TASK: {intent.get('task', '')}  (resolution: {packet.get('resolution', {})})")
    if status in ("UNKNOWN", "NEEDS_CLARIFICATION"):
        unknown = packet.get("unknown", {})
        lines.append(f"REASON: {unknown.get('reason', '?')}")
        for need in unknown.get("needed", []) or []:
            lines.append(f"  - {need}")
        return "\n".join(lines)
    route = packet.get("route", {})
    lines.append(f"ROUTE: {route.get('key')}  [{route.get('domain')}]  ({route.get('language')})")
    targets = packet.get("targets", [])
    lines.append(f"TARGETS ({len(targets)} files):")
    for target in targets[:10]:
        lines.append(f"  {target['file']}  [{target['module']}]")
        for symbol in target["symbols"][:8]:
            lines.append(f"    L{symbol['line']}: {symbol['qualified']} ({symbol['kind']})")
    impact = packet.get("impact", {})
    lines.append(
        f"CALLERS: {len(impact.get('callers', []))}  "
        f"AFFECTED MODULES: {', '.join(impact.get('affected_modules', [])) or '—'}"
    )
    tests = packet.get("tests", {})
    ordered_tests = (
        tests.get("direct", []) or tests.get("file", []) or _substantive(tests.get("module", []))
    )
    if ordered_tests and all("__init__.py" in t for t in ordered_tests):
        ordered_tests = []  # empty suites are noise; the route note tells the truth
    route_test = tests.get("route", {}).get("command") or tests.get("route", {}).get("note")
    lines.append(f"TESTS: {', '.join(ordered_tests[:5]) or route_test or '—'}")
    plan = packet.get("validation", {})
    lines.append(f"VALIDATION [{plan.get('scope', '?')}]:")
    for command in plan.get("commands", [])[:8]:
        lines.append(f"  {command}")
    boundaries = packet.get("boundaries", {})
    must = boundaries.get("must_change", [])
    shown = ", ".join(must[:6]) or "—"
    if len(must) > 6:
        shown += f" (+{len(must) - 6} more)"
    lines.append(f"MUST CHANGE: {shown}")
    must_not = boundaries.get("must_not_change", {}).get("patterns", [])[:6]
    lines.append(f"MUST NOT: {', '.join(must_not) or '—'}")
    if status == "BLOCKED":
        for violation in packet.get("safety", {}).get("gate", {}).get("violations", []):
            lines.append(
                f"BLOCKED: {violation['target']} — {violation['reason']} "
                f"(owner {violation['canonical_owner']}/{violation['canonical_language']})"
            )
    return "\n".join(lines)


def benchmark() -> dict:
    """A route-only vs B route+graph vs C route+warm-index vs D full surface."""
    out: dict = {}
    routes = load_routes()
    task = "add validation to broker registry"
    started = time.perf_counter()
    for _ in range(100):
        match_route(task, routes)
    out["A_route_only_ms_per_100"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    graph = repo_graph.load_graph()
    out["B_graph_load_ms"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    for _ in range(100):
        route, _how = match_route(task, routes)
        assert route is not None
        repo_graph.run_query(graph, "symbol", "BrokerRegistry")
    out["B_route_plus_graph_ms_per_100"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    payload = repo_index.ensure_fresh()
    out["C_warm_ensure_fresh_ms"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    for _ in range(100):
        route, _how = match_route(task, routes)
        assert route is not None
        repo_index.query_with_maps(payload, "symbol", "BrokerRegistry")
    out["C_route_plus_index_ms_per_100"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    resolve(task)
    out["D_patch_surface_cold_ms"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    for _ in range(20):
        resolve(task)
    out["D_patch_surface_warm_ms_per_20"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    resolve("zzz no such task anywhere")
    out["D_unknown_task_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intent -> exact patch surface")
    parser.add_argument("--task", default="", help="natural-language task description")
    parser.add_argument("--file", default=None, help="explicit target file")
    parser.add_argument("--symbol", default=None, help="explicit target symbol")
    parser.add_argument("--module", default=None, help="explicit target module")
    parser.add_argument("--route", default=None, help="explicit route key")
    parser.add_argument("--domain", default=None, help="narrow to a route domain")
    parser.add_argument("--modify", action="append", default=[], help="planned edit target (gate)")
    parser.add_argument("--json", action="store_true", help="emit the packet as JSON")
    parser.add_argument("--stats", action="store_true", help="A/B/C/D benchmark")
    args = parser.parse_args(argv)
    if args.stats:
        print(json.dumps(benchmark(), indent=2, sort_keys=True))
        return 0
    if not args.task and not any([args.file, args.symbol, args.module, args.route]):
        parser.print_help()
        return 2
    packet = resolve(
        args.task,
        file=args.file,
        symbol=args.symbol,
        module=args.module,
        route_key=args.route,
        domain=args.domain,
        modify=args.modify,
    )
    if args.json:
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        print(render_text(packet))
    return {"RESOLVED": 0, "BLOCKED": 1, "UNKNOWN": 2, "NEEDS_CLARIFICATION": 3}[packet["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
