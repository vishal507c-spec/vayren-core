"""Incremental + cached validation (Phase 6).

EDIT -> change detection (manifest hashes + fresh graph) -> impact analysis
-> deterministic scope L0..L5 -> persistent result cache -> run minimum
safe set. Escalates whenever safety cannot be proven. Consumes (never
redesigns) the graph, warm index, routing, patch surface, packets, and the
execution surface runner. Calls existing validators/commands only.

Usage:
    python scripts/validate_scope.py --task "add validation to broker registry"
    python scripts/validate_scope.py --manifest <manifest.json> [--run] [--full]
    python scripts/validate_scope.py --task "..." --json
    python scripts/validate_scope.py --stats
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import execute_surface  # noqa: E402
import repo_graph  # noqa: E402
import repo_index  # noqa: E402

ROOT = SCRIPTS_DIR.parent
VALID_DIR = repo_index.INDEX_DIR / "validations"

SCOPE_SCHEMA = "validation-scope/v1"
CACHE_SCHEMA = "validation-cache/v1"
LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")
LOCK_TIMEOUT_S = 30.0
LOCK_STALE_S = 300.0
RUN_TIMEOUT_S = 600

GOVERNANCE_COMMANDS = [
    "python scripts/validate_structure.py",
    "python scripts/validate_imports.py",
    "python scripts/validate_language_ownership.py",
    "python scripts/validate_architecture_gate.py",
    "python scripts/validate_authority.py",
    "python scripts/validate_routes.py",
    "python scripts/context_engine.py --check",
]

L5_COMMANDS = [
    "ruff check .",
    "ruff format --check .",
    "pyright",
    "pytest 02_data/data/tests -q",
    "pytest 09_broker/broker/tests -q",
    "pytest scripts/forensics/tests -q",
    "pytest scripts/tests -q",
    *GOVERNANCE_COMMANDS,
    "cargo test -p vayren-core --lib",
]

# Python-only full baseline for incremental≡full comparison. Sound because a
# Python-only change cannot affect cargo outcomes; cargo equivalence itself
# is explicitly unmeasured (see Phase-6 report).
FULL_PY = [c for c in L5_COMMANDS if not c.startswith("cargo ")]

# Phase-1-5 tooling: a change here invalidates scope reasoning itself.
TOOLING_PREFIXES = ("scripts/",)
CANONICAL_DOCS = (
    "90_brain/module_contracts.md",
    "90_brain/event_catalog.md",
    "90_brain/architecture.md",
    "90_brain/ai_memory.md",
)
CANONICAL_POLICY = ("90_brain/ownership_policy.json", "90_brain/language_retention.json")
CANONICAL_ROUTES = ("90_brain/task_routes.json",)

_TOOLCHAIN_OVERRIDE: dict | None = None


class ScopeError(Exception):
    """Scope/cache failure — explicit status, never silent escalation."""


def toolchain_identity() -> dict:
    """Toolchain versions pinning cached results (overridable in tests)."""
    if _TOOLCHAIN_OVERRIDE is not None:
        return dict(_TOOLCHAIN_OVERRIDE)
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for dist, key in (("pytest", "pytest"), ("ruff", "ruff"), ("pyright", "pyright")):
        try:
            versions[key] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            versions[key] = "missing"
    for binary, key in (("cargo", "cargo"), ("rustc", "rustc")):
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
            versions[key] = (
                proc.stdout.strip().splitlines()[0] if proc.returncode == 0 else "missing"
            )
        except (OSError, subprocess.TimeoutExpired):
            versions[key] = "missing"
    return versions


def _sha_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _graph_data() -> dict:
    try:
        payload = repo_index.ensure_fresh()
    except (
        repo_index.IndexError,
        repo_index.IndexLockedError,
        repo_graph.GraphError,
        OSError,
        ValueError,
    ) as exc:
        raise ScopeError(f"index freshness failed: {exc}") from exc
    return payload["graph"]


def _filemeta() -> dict:
    data = repo_index._read_json(repo_index.FILEMETA_FILE)
    return data if isinstance(data, dict) else {}


def detect_changes(manifest: dict, index_before: dict | None = None) -> dict:
    """Manifest hashes vs working tree + pre/post index-manifest compare.

    Returns {changed, deleted, added, unexpected, expected} with sorted paths.
    The exec manifest covers only its read set; files anywhere else in the
    tree (other modules, new files, canonical inputs) are caught by comparing
    the warm index manifest before/after refresh (content shas, not mtimes).
    The roots-scan fallback applies only when no pre-refresh snapshot exists.
    """
    hashes = manifest.get("hashes", {}) or {}
    changed: set[str] = set()
    deleted: set[str] = set()
    for relpath, recorded in sorted(hashes.items()):
        current = _sha_file(ROOT / relpath)
        if current is None:
            deleted.add(relpath)
        elif current != recorded:
            changed.add(relpath)
    modify = manifest.get("modify", {}) or {}
    expected = sorted(set(modify.get("must", []) or []) | set(modify.get("may", []) or []))
    expected_set = set(expected)
    added: set[str] = set()
    index_manifest = repo_index._read_json(repo_index.MANIFEST_FILE)
    post = index_manifest if isinstance(index_manifest, dict) else {}
    if index_before is not None:
        for relpath, entry in post.items():
            old = index_before.get(relpath)
            if not isinstance(old, dict):
                if (ROOT / relpath).is_file():
                    added.add(relpath)
            elif isinstance(entry, dict) and entry.get("sha") != old.get("sha"):
                changed.add(relpath)
        for relpath in index_before:
            if relpath not in post and not (ROOT / relpath).is_file():
                deleted.add(relpath)
    elif manifest.get("modify"):
        # Fallback: brand-new files under expected roots never seen by index.
        roots = sorted({p.split("/")[0] + "/" + p.split("/")[1] for p in expected_set if "/" in p})
        seen: set[str] = set(hashes) | set(post)
        for root in roots:
            root_path = ROOT / root
            if not root_path.is_dir():
                continue
            for path in sorted(root_path.rglob("*")):
                if not path.is_file() or path.suffix not in (".py", ".rs", ".slint"):
                    continue
                if any(part in repo_graph.EXCLUDED_DIRS for part in path.parts):
                    continue
                rel = path.relative_to(ROOT).as_posix()
                if rel not in seen:
                    added.add(rel)
    unexpected = sorted([p for p in changed | deleted | added if p not in expected_set])
    return {
        "changed": sorted(changed),
        "deleted": sorted(deleted),
        "added": sorted(added),
        "unexpected": unexpected,
        "expected": expected,
    }


def _file_import_targets(relpath: str, modules: list[str]) -> list[str]:
    try:
        text = (ROOT / relpath).read_text(encoding="utf-8")
    except OSError:
        return []
    return repo_index._file_imports(relpath, text, modules)


def analyze_impact(
    change: dict, manifest: dict, graph: dict, old_filemeta: dict | None = None
) -> dict:
    """Changed files -> modules/symbols/callers/dependents/contracts (bounded)."""
    modules = [m["id"] for m in graph.get("modules", [])]
    mod_by_id = {m["id"]: m for m in graph.get("modules", [])}
    sym_by_file: dict[str, list[dict]] = {}
    for symbol in graph.get("symbols", []):
        sym_by_file.setdefault(symbol["file"], []).append(symbol)
    regions = manifest.get("regions", {}) or {}
    changed_symbols: list[dict] = []
    changed_modules: set[str] = set()
    incomplete: list[str] = []
    for relpath in change["changed"] + change["deleted"]:
        module = repo_graph.owning_module(relpath, modules)
        if module is not None:
            changed_modules.add(module)
        expected = {r.get("qualified", "") for r in regions.get(relpath, [])}
        actual = {s["qualified"] for s in sym_by_file.get(relpath, [])}
        for qual in sorted(actual - expected):
            symbol = next(s for s in sym_by_file[relpath] if s["qualified"] == qual)
            changed_symbols.append({**_symbol_summary(symbol), "change": "added"})
        for qual in sorted(expected - actual):
            changed_symbols.append({"qualified": qual, "change": "removed"})
        for qual in sorted(actual & expected):
            symbol = next(s for s in sym_by_file[relpath] if s["qualified"] == qual)
            exp_line = next(r["line"] for r in regions[relpath] if r.get("qualified") == qual)
            if symbol["line"] != exp_line:
                changed_symbols.append({**_symbol_summary(symbol), "change": "moved"})
            else:
                changed_symbols.append({**_symbol_summary(symbol), "change": "modified"})
        if relpath.endswith((".rs", ".slint")) and relpath in change["changed"]:
            incomplete.append(relpath)
    for relpath in change["added"]:
        module = repo_graph.owning_module(relpath, modules)
        if module is not None:
            changed_modules.add(module)
    public_changed = [s for s in changed_symbols if s.get("public")]
    dep_changed: list[dict] = []
    old_meta = old_filemeta or {}
    for relpath in change["changed"]:
        module = repo_graph.owning_module(relpath, modules)
        if module is None or mod_by_id.get(module) is None:
            continue
        if (ROOT / relpath).is_file() and not repo_graph.is_test_file(relpath):
            before = set((old_meta.get(relpath, {}) or {}).get("imports", []))
            after = set(_file_import_targets(relpath, modules))
            if before != after:
                dep_changed.append(
                    {
                        "module": module,
                        "file": relpath,
                        "before": sorted(before),
                        "after": sorted(after),
                    }
                )
    callers: set[str] = set()
    caller_modules: set[str] = set()
    for symbol in changed_symbols:
        qual = symbol.get("qualified", "")
        node = next((s for s in graph.get("symbols", []) if s.get("qualified") == qual), None)
        if node is None:
            continue
        for caller in node.get("called_by", []) or []:
            callers.add(caller)
            back = next((s for s in graph.get("symbols", []) if s.get("qualified") == caller), None)
            if back is not None:
                caller_modules.add(back["module"])
    dependents: set[str] = set()
    for mid in changed_modules:
        module = mod_by_id.get(mid)
        if module:
            dependents.update(module.get("depended_on_by", []) or [])
    affected = sorted(changed_modules | caller_modules | dependents)
    canonical = sorted(
        {p for p in change["changed"] + change["added"] if _canonical_kind(p) is not None}
    )
    languages = sorted(
        {
            repo_graph.classify(p, _policy_rules())[2] or "UNKNOWN"
            for p in change["changed"] + change["added"]
            if (ROOT / p).is_file()
        }
    )
    return {
        "modules": sorted(changed_modules),
        "symbols": sorted(changed_symbols, key=lambda s: s["qualified"]),
        "public_changed": sorted(s["qualified"] for s in public_changed),
        "dep_changed": dep_changed,
        "callers": sorted(callers),
        "caller_modules": sorted(caller_modules),
        "dependents": sorted(dependents),
        "affected_modules": affected,
        "canonical": canonical,
        "languages": languages,
        "incomplete": sorted(set(incomplete)),
    }


def _symbol_summary(symbol: dict) -> dict:
    return {
        "qualified": symbol["qualified"],
        "file": symbol["file"],
        "line": symbol["line"],
        "kind": symbol["kind"],
        "public": symbol["public"],
        "module": symbol["module"],
    }


def _canonical_kind(relpath: str) -> str | None:
    if relpath in CANONICAL_POLICY:
        return "ownership"
    if relpath in CANONICAL_ROUTES:
        return "routes"
    if relpath in CANONICAL_DOCS:
        return "docs"
    if relpath.startswith("scripts/validate_") or relpath in (
        "scripts/route.py",
        "scripts/context_engine.py",
    ):
        return "validator"
    if relpath.startswith(TOOLING_PREFIXES):
        return "tooling"
    return None


def _policy_rules() -> list[dict]:
    try:
        policy = json.loads(repo_graph.POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rules = policy.get("rules", [])
    return list(rules) if isinstance(rules, list) else []


def compute_scope(change: dict, graph: dict) -> dict:
    """Deterministic change -> (level, commands, reasons, escalations). Pure."""
    reasons: list[str] = []
    escalations: list[str] = []
    levels: set[str] = set()
    impact = change.get("impact", {})
    changed = change.get("changed", []) + change.get("added", [])

    if change.get("unexpected"):
        foreign = sorted(p for p in change["unexpected"] if _canonical_kind(p) is None)
        if foreign:
            levels.add("L5")
            reasons.append(f"UNEXPECTED_CHANGE_SURFACE: {foreign}")
            escalations.append("unexpected files block incremental validation -> L5 full")
    tooling = [p for p in changed if _canonical_kind(p) == "tooling"]
    if tooling:
        levels.add("L5")
        reasons.append(f"phase tooling changed: {sorted(tooling)}")
        escalations.append("scope reasoning itself may be stale -> L5 full")
    validators = [p for p in changed if _canonical_kind(p) == "validator"]
    if validators:
        levels.add("L4")
        reasons.append(f"validator changed: {sorted(validators)}")

    kinds = {_canonical_kind(p) for p in changed}
    kinds.discard(None)
    if "ownership" in kinds:
        levels.add("L4")
        reasons.append("ownership/language policy changed")
    if "routes" in kinds:
        levels.add("L4")
        reasons.append("task routing changed")
    if "validator" in kinds:
        levels.add("L4")
        reasons.append("validator changed")
    if "docs" in kinds:
        levels.add("L3")
        reasons.append("canonical docs changed")

    canonical_touched = bool(kinds)
    modules = impact.get("modules", []) or []
    public = impact.get("public_changed", []) or []
    languages = [lang for lang in (impact.get("languages", []) or []) if lang != "UNKNOWN"]
    cross_language = len(set(languages)) > 1
    if cross_language:
        levels.add("L4")
        reasons.append(f"cross-language change: {sorted(set(languages))}")

    if not changed and not change.get("deleted"):
        return {
            "level": "L0",
            "levels": ["L0"],
            "commands": [],
            "impact": impact,
            "reasons": ["working tree matches manifest: nothing to validate"],
            "escalations": [],
            "clean": True,
        }

    test_by_target: dict[str, list[str]] = {}
    for test in graph.get("tests", []) or []:
        for target in test.get("targets", []) or []:
            test_by_target.setdefault(target, []).append(test["path"])
    changed_py = [p for p in changed if p.endswith(".py") and (ROOT / p).is_file()]
    if changed_py:
        levels.add("L0")
        reasons.append("syntax/local gate for changed Python files")
    if any(test_by_target.get(p) for p in changed_py):
        levels.add("L1")
        reasons.append("direct tests cover changed files")
    elif _affected_suites(impact, graph):
        levels.add("L2")
        reasons.append("no direct tests; affected module suites cover the change")
    else:
        levels.add("L3")
        reasons.append("no test layer; boundary validators only")
    if any(p.endswith((".rs", ".slint")) and (ROOT / p).is_file() for p in changed) and any(
        _cargo_for_file(p) for p in changed if p.endswith((".rs", ".slint"))
    ):
        levels.add("L1")
        reasons.append("covering cargo commands exist for changed Rust/Slint files")

    if public:
        levels.add("L2")
        reasons.append(f"public symbols changed: {public[:5]}")
    if impact.get("dep_changed"):
        levels.add("L3")
        reasons.append(f"dependency edges changed: {impact['dep_changed']}")
    if len(set(modules)) > 1:
        levels.add("L4")
        reasons.append(f"multi-module change: {sorted(set(modules))}")
    if impact.get("incomplete") and not canonical_touched:
        levels.add("L2")
        reasons.append(f"caller data incomplete for: {impact['incomplete']}")
        escalations.append("Rust/Slint caller edges untracked: module suites cover dependents")

    if not levels:
        levels.add("L0")
        reasons.append("private implementation-only change")
    commands = _commands_for_levels(sorted(levels, key=LEVELS.index), change, impact, graph)
    top = sorted(levels, key=LEVELS.index)[-1]
    return {
        "level": top,
        "levels": sorted(levels, key=LEVELS.index),
        "commands": commands,
        "impact": impact,
        "reasons": reasons,
        "escalations": escalations,
        "clean": False,
    }


def _affected_suites(impact: dict, graph: dict) -> set[str]:
    """Pytest suites covering affected modules (partition + route commands).

    Partition-level (`pytest <tests-dir> -q`) rather than per-file: one
    subprocess per suite instead of one per file, same coverage. Only real
    test files (test_*.py/*_test.py) count toward suite detection.
    """
    mod_by_id = {m["id"]: m for m in graph.get("modules", []) or []}
    suites: set[str] = set()
    for mid in impact.get("affected_modules", []) or []:
        module = mod_by_id.get(mid)
        if module is None:
            continue
        dirs: set[str] = set()
        for path in module.get("files", []) or []:
            name = path.rsplit("/", 1)[-1]
            if (
                "/tests/" in path
                and path.endswith(".py")
                and (name.startswith("test_") or name.removesuffix(".py").endswith("_test"))
            ):
                dirs.add(path.rsplit("/tests/", 1)[0] + "/tests")
        for directory in sorted(dirs):
            suites.add(f"pytest {directory} -q")
        for rkey in module.get("route_keys", []) or []:
            suite = _route_suite(rkey)
            if suite:
                suites.add(suite)
    return suites


def _commands_for_levels(levels: list[str], change: dict, impact: dict, graph: dict) -> list[str]:
    """Assemble existing commands per level (ordered L0 -> L5, deduped)."""
    changed = [
        p for p in change.get("changed", []) + change.get("added", []) if (ROOT / p).is_file()
    ]
    py_files = sorted(p for p in changed if p.endswith(".py"))
    rs_files = sorted(p for p in changed if p.endswith((".rs", ".slint")))
    commands: list[str] = []

    def add(command: str) -> None:
        if command not in commands:
            commands.append(command)

    mod_by_id = {m["id"]: m for m in graph.get("modules", [])}
    test_by_target: dict[str, list[str]] = {}
    for test in graph.get("tests", []) or []:
        for target in test.get("targets", []) or []:
            test_by_target.setdefault(target, []).append(test["path"])

    if "L0" in levels:
        for path in py_files:
            add(f"ruff check {path}")
            add(f"ruff format --check {path}")
            add(f"pyright {path}")
        # No per-file rustfmt gate exists (workspace `cargo fmt --check` carries
        # pre-existing failures); Rust syntax is proven by cargo compile in L1.

    if "L1" in levels:
        direct: set[str] = set()
        for path in changed:
            direct.update(test_by_target.get(path, []))
        for path in sorted(direct):
            add(f"pytest {path} -q")
        for path in rs_files:
            for command in _cargo_for_file(path):
                add(command)

    if "L2" in levels:
        suites = _affected_suites(impact, graph)
        for path in sorted(suites):
            if path.startswith("pytest "):
                add(path)
            else:
                add(f"pytest {path} -q")
        for mid in impact.get("affected_modules", []) or []:
            for path in sorted(
                f for f in (mod_by_id.get(mid, {}).get("files", []) or []) if f.endswith(".rs")
            )[:1]:
                for command in _cargo_for_file(path):
                    add(command)

    if "L3" in levels:
        add("python scripts/validate_routes.py")
        add("python scripts/validate_authority.py")
        add("python scripts/validate_imports.py")

    if "L4" in levels:
        for command in GOVERNANCE_COMMANDS:
            add(command)
        for path in sorted(
            t["path"]
            for t in graph.get("tests", []) or []
            if (t.get("module", "") or "") == "scripts"
        ):
            add(f"pytest {path} -q")

    if "L5" in levels:
        for command in L5_COMMANDS:
            add(command)

    # Dedupe: a per-file pytest covered by a partition command in the same
    # set adds only subprocess overhead (the partition runs those tests).
    partitions = {
        parts[1]
        for command in commands
        if command.startswith("pytest ") and (parts := command.split()) and len(parts) > 2
    }
    pruned: list[str] = []
    for command in commands:
        parts = command.split()
        if (
            command.startswith("pytest ")
            and len(parts) == 3
            and parts[1].endswith(".py")
            and any(parts[1].startswith(d.rstrip("/") + "/") for d in partitions)
        ):
            continue
        pruned.append(command)
    commands[:] = pruned

    # Fallback: a code change with no test layer still gets boundary proof.
    if changed and not [c for c in commands if c.startswith(("pytest", "cargo test"))]:
        for command in (
            "python scripts/validate_routes.py",
            "python scripts/validate_authority.py",
        ):
            add(command)
    return commands


def _cargo_for_file(relpath: str) -> list[str]:
    """Covering routes' cargo commands for one Rust/Slint file (existing only).

    Template commands with `<placeholders>` are skipped: they cannot run
    literally (the runner reports templates as skipped, never executed).
    """
    routes = _load_routes_safe()
    found: list[str] = []
    for route in routes:
        paths = [str(p) for p in route.get("primary_files", [])]
        if relpath in paths or any(
            relpath.startswith(str(op) + "/") for op in route.get("owner_paths", [])
        ):
            for command in route.get("validation", []) or []:
                text = str(command)
                if "<" in text and ">" in text:
                    continue
                if text.startswith("cargo test") and text not in found:
                    found.append(text)
    return found


def _route_suite(route_key: str) -> str | None:
    for route in _load_routes_safe():
        if str(route.get("key", "")) == route_key:
            tests = route.get("tests", {}) or {}
            command = tests.get("command")
            if isinstance(command, str) and command.startswith("pytest "):
                return command
    return None


def _load_routes_safe() -> list[dict]:
    try:
        from route import load_routes  # noqa: E402

        routes = load_routes()
    except (OSError, ValueError):
        return []
    return [r for r in routes if isinstance(r, dict)]


def _relevant_files(command: str, graph: dict) -> list[str]:
    """File set whose bytes pin one command's outcome (sorted, bounded)."""
    parts = command.split()
    if command.startswith("pytest ") and len(parts) >= 2 and not parts[1].startswith("-"):
        target = parts[1]
        roots = [target] if (ROOT / target).is_file() else []
        if (ROOT / target).is_dir():
            roots = [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / target).rglob("*.py"))]
        return sorted(_transitive_closure(roots, graph))
    if command.startswith("cargo "):
        rust_files = [
            p.relative_to(ROOT).as_posix()
            for p in sorted((ROOT / "rust").rglob("*"))
            if p.is_file() and p.suffix in (".rs", ".toml", ".lock")
        ]
        return rust_files
    if command.startswith(("ruff ", "pyright ")):
        targets = [p for p in parts[1:] if not p.startswith("-") and p != "--check"]
        if not targets or targets == ["."]:
            return ["MANIFEST_SHA"]
        return sorted(t for t in targets if (ROOT / t).is_file()) + ["pyproject.toml"]
    if command.startswith("python scripts/"):
        # Governance validators scan the whole tree: the whole-tree digest
        # (precomputed index manifest sha) is the relevant pin.
        return ["MANIFEST_SHA"]
    return ["pyproject.toml"]


def _transitive_closure(roots: list[str], graph: dict) -> set[str]:
    """Test/module files + transitively imported modules' files (bounded)."""
    try:
        filemeta = _filemeta()
    except (OSError, ValueError):
        filemeta = {}
    mod_files: dict[str, list[str]] = {}
    for module in graph.get("modules", []) or []:
        mod_files[module["id"]] = list(module.get("files", []) or [])
    file_module = {f: m for m, files in mod_files.items() for f in files}
    modules: set[str] = set()
    for root in roots:
        if root in file_module:
            modules.add(file_module[root])
        mod = repo_graph.owning_module(root, list(mod_files))
        if mod:
            modules.add(mod)
    changed = True
    while changed:
        changed = False
        for _relpath, info in filemeta.items():
            if not isinstance(info, dict) or info.get("module") not in modules:
                continue
            for imported in info.get("imports", []) or []:
                if imported not in modules and imported in mod_files:
                    modules.add(imported)
                    changed = True
    out: set[str] = set(roots)
    for mid in modules:
        out.update(mod_files.get(mid, []))
    return {p for p in out if (ROOT / p).is_file()}


def _canonical_digest() -> str:
    parts = []
    for rel in (
        "90_brain/ownership_policy.json",
        "90_brain/language_retention.json",
        "90_brain/task_routes.json",
        "90_brain/module_contracts.md",
        "pyproject.toml",
    ):
        sha = _sha_file(ROOT / rel)
        parts.append(f"{rel}={sha or 'missing'}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _command_validator_files(command: str) -> list[str]:
    if command.startswith("python scripts/"):
        script = command.split()[1]
        return [script] if (ROOT / script).is_file() else []
    return []


def _live_tree_digest() -> str:
    """Whole-tree content digest over indexed sources (live bytes, no refresh needed).

    Unlike the index manifest file (which lags the tree until refresh), this
    reads current bytes, so source changes invalidate cache keys immediately.
    """
    manifest = repo_index._read_json(repo_index.MANIFEST_FILE)
    if not isinstance(manifest, dict) or not manifest:
        try:
            return hashlib.sha256(repo_index.MANIFEST_FILE.read_bytes()).hexdigest()
        except OSError:
            return "missing"
    digest = hashlib.sha256()
    for relpath in sorted(manifest):
        digest.update(f"{relpath}={_sha_file(ROOT / relpath) or 'missing'};".encode())
    return digest.hexdigest()


def build_cache_key(command: str, scope: dict, graph: dict) -> dict:
    """Correctness-safe key material (no timestamps anywhere in the key)."""
    relevant = _relevant_files(command, graph)
    tree_digest = _live_tree_digest()
    digest = hashlib.sha256()
    for relpath in relevant:
        if relpath == "MANIFEST_SHA":
            digest.update(f"MANIFEST_SHA={tree_digest};".encode())
            continue
        digest.update(f"{relpath}={_sha_file(ROOT / relpath) or 'missing'};".encode())
    key_material = {
        "schema": CACHE_SCHEMA,
        "command": command,
        "scope_level": scope.get("level", ""),
        "files_digest": digest.hexdigest(),
        "files_count": len(relevant),
        "canonical": _canonical_digest(),
        "validators": sorted(
            f"{p}={_sha_file(ROOT / p) or 'missing'}" for p in _command_validator_files(command)
        ),
        "toolchain": toolchain_identity(),
    }
    key = hashlib.sha256(json.dumps(key_material, sort_keys=True).encode()).hexdigest()[:32]
    return {"key": key, "material": key_material, "relevant_files": relevant}


def _cache_path(key: str) -> Path:
    return VALID_DIR / f"{key}.json"


def cache_lookup(command: str, scope: dict, graph: dict) -> dict | None:
    """Reusable PASS/FAIL/UNAVAILABLE only; TIMEOUT/ENV rerun (never reused)."""
    built = build_cache_key(command, scope, graph)
    try:
        data = json.loads(_cache_path(built["key"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("key") != built["key"]:
        return None
    if data.get("material") != built["material"]:
        return None
    if data.get("outcome") in ("PASS", "FAIL", "UNAVAILABLE"):
        data["cache"] = "hit"
        return data
    return None


def cache_store(
    command: str,
    scope: dict,
    graph: dict,
    outcome: str,
    rc: int | None,
    duration_ms: float,
    tail: str = "",
    note: str = "",
) -> dict:
    """Atomic publish (tmp+replace). TIMEOUT/ENV outcomes are never stored."""
    if outcome in ("TIMEOUT", "ENV_FAIL", "INTERRUPTED"):
        return {"stored": False, "outcome": outcome}
    built = build_cache_key(command, scope, graph)
    record = {
        "schema": CACHE_SCHEMA,
        "key": built["key"],
        "material": built["material"],
        "command": command,
        "outcome": outcome,
        "rc": rc,
        "duration_ms": round(duration_ms, 1),
        "tail": tail[-2000:],
        "note": note,
        "cache": "miss",
    }
    VALID_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(built["key"])
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return {"stored": True, "key": built["key"]}


def _locked_for(key: str, timeout_s: float = LOCK_TIMEOUT_S):
    """Per-key lock (stale expiry); local primitive, no cross imports."""
    lockdir = VALID_DIR / f".lock-{key}"

    class _Guard:
        def __enter__(self) -> None:
            VALID_DIR.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    os.mkdir(lockdir)
                    return
                except FileExistsError:
                    try:
                        age = time.time() - lockdir.stat().st_mtime
                    except OSError:
                        age = 0.0
                    if age > LOCK_STALE_S:
                        shutil.rmtree(lockdir, ignore_errors=True)
                        continue
                    if time.monotonic() > deadline:
                        raise ScopeError(f"validation lock busy: {key}") from None
                    time.sleep(0.05)

        def __exit__(self, *exc: object) -> None:
            shutil.rmtree(lockdir, ignore_errors=True)

    return _Guard()


def run_cached(
    command: str,
    scope: dict,
    graph: dict,
    timeout_s: int = RUN_TIMEOUT_S,
    *,
    use_cache: bool = True,
) -> dict:
    """One command, cache-aware, outcome-classified. Returns the result record."""
    built = build_cache_key(command, scope, graph)
    with _locked_for(built["key"]):
        if use_cache:
            hit = cache_lookup(command, scope, graph)
            if hit is not None:
                return hit
        verified = execute_surface._verify_command(command)
        if not verified.get("available", False):
            record = {
                "schema": CACHE_SCHEMA,
                "key": built["key"],
                "material": built["material"],
                "command": command,
                "outcome": "UNAVAILABLE",
                "rc": None,
                "duration_ms": 0.0,
                "tail": "",
                "note": verified.get("reason", ""),
                "cache": "miss",
            }
            if use_cache:
                cache_store(
                    command, scope, graph, "UNAVAILABLE", None, 0.0, note=verified.get("reason", "")
                )
            return record
        manifest = {"validate": {"commands": [command]}}
        started = time.perf_counter()
        # Cargo commands canonically run under rust/ (Makefile/CI); the
        # runner defaults to the repository root for everything else.
        cwd = ROOT / "rust" if command.startswith("cargo ") else None
        try:
            result = execute_surface.run_validation(manifest, timeout_s=timeout_s, cwd=cwd)
            entry = result["results"][0]
        except (OSError, ValueError) as exc:
            return {
                "schema": CACHE_SCHEMA,
                "key": built["key"],
                "material": built["material"],
                "command": command,
                "outcome": "ENV_FAIL",
                "rc": None,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "tail": "",
                "note": f"runner error: {exc}",
                "cache": "miss",
            }
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        rc = entry.get("rc")
        note = entry.get("note", "") or ""
        if rc is None and ("skipped" in note or "unavailable" in note.lower()):
            outcome = "UNAVAILABLE"
            if use_cache:
                cache_store(
                    command,
                    scope,
                    graph,
                    outcome,
                    rc,
                    duration_ms,
                    tail=entry.get("tail", ""),
                    note=note,
                )
            return {
                "schema": CACHE_SCHEMA,
                "key": built["key"],
                "material": built["material"],
                "command": command,
                "outcome": outcome,
                "rc": rc,
                "duration_ms": duration_ms,
                "tail": entry.get("tail", ""),
                "note": note,
                "cache": "miss",
            }
        if rc is None:
            outcome = "TIMEOUT" if "imeout" in note or "timed out" in note.lower() else "ENV_FAIL"
            return {
                "schema": CACHE_SCHEMA,
                "key": built["key"],
                "material": built["material"],
                "command": command,
                "outcome": outcome,
                "rc": rc,
                "duration_ms": duration_ms,
                "tail": entry.get("tail", ""),
                "note": note,
                "cache": "miss",
            }
        outcome = "PASS" if rc == 0 else "FAIL"
        if use_cache:
            cache_store(
                command,
                scope,
                graph,
                outcome,
                rc,
                duration_ms,
                tail=entry.get("tail", ""),
                note=note,
            )
        return {
            "schema": CACHE_SCHEMA,
            "key": built["key"],
            "material": built["material"],
            "command": command,
            "outcome": outcome,
            "rc": rc,
            "duration_ms": duration_ms,
            "tail": entry.get("tail", ""),
            "note": note,
            "cache": "miss",
        }


def validate_manifest(
    manifest: dict,
    *,
    force_level: str | None = None,
    run: bool = True,
    timeout_s: int = RUN_TIMEOUT_S,
    use_cache: bool = True,
) -> dict:
    """Full pipeline: changes vs manifest -> impact -> scope -> cache -> run.

    The manifest is the EXPECTED baseline: hash mismatches are real changes
    to scope, never a reason to rebuild-and-bless the tree. Rebuilds happen
    nowhere here; freshness failures yield UNKNOWN, races yield STALE.
    """
    started = time.perf_counter()
    if not isinstance(manifest, dict) or not isinstance(manifest.get("hashes"), dict):
        return _result(started, "UNKNOWN", "", "manifest is not a validation manifest", {}, {})
    if manifest.get("status") not in ("READY", "READ_ONLY"):
        return _result(
            started,
            manifest.get("status", "UNKNOWN"),
            "",
            f"patch surface status: {manifest.get('status')}",
            {},
            {},
        )
    freshness = execute_surface.check_manifest(manifest)
    if freshness["status"] == "BLOCKED":
        return _result(started, "UNKNOWN", "", freshness.get("reason", ""), {}, {})
    old_filemeta = _filemeta()
    index_before = repo_index._read_json(repo_index.MANIFEST_FILE)
    if not isinstance(index_before, dict):
        index_before = None
    try:
        repo_index.ensure_fresh()
    except (
        repo_index.IndexError,
        repo_index.IndexLockedError,
        repo_graph.GraphError,
        OSError,
        ValueError,
    ) as exc:
        return {
            "status": "UNKNOWN",
            "scope": "",
            "commands": [],
            "cache": "miss",
            "results": [],
            "impact": {},
            "reason": f"index refresh failed: {exc}",
            "evidence": {},
            "timing_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    try:
        graph = _graph_data()
    except ScopeError as exc:
        return {
            "status": "UNKNOWN",
            "scope": "",
            "commands": [],
            "cache": "miss",
            "results": [],
            "impact": {},
            "reason": str(exc),
            "evidence": {},
            "timing_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    change = detect_changes(manifest, index_before)
    impact = analyze_impact(change, manifest, graph, old_filemeta)
    change["impact"] = impact
    scope = compute_scope(change, graph)
    if force_level is not None:
        if force_level not in LEVELS:
            raise ScopeError(f"unknown level {force_level}")
        scope = dict(scope)
        scope["levels"] = [force_level]
        scope["level"] = force_level
        scope["commands"] = _commands_for_levels([force_level], change, impact, graph)
        scope["reasons"] = [*scope.get("reasons", []), f"forced level {force_level}"]
    evidence = {
        "manifest_key": manifest.get("manifest_key", ""),
        "index_fingerprint": manifest.get("index_fingerprint", ""),
        "graph_inputs_hash": graph.get("inputs_hash", ""),
        "toolchain": toolchain_identity(),
    }
    if not run:
        return {
            "status": "ESCALATED" if scope.get("escalations") else "VALID",
            "scope": scope["level"],
            "commands": scope["commands"],
            "cache": "miss",
            "results": [],
            "impact": impact,
            "reason": "; ".join(scope.get("reasons", [])),
            "escalations": scope.get("escalations", []),
            "evidence": evidence,
            "timing_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    digests: dict[str, str | None] = {}
    for command in scope["commands"]:
        for relpath in _relevant_files(command, graph):
            if relpath != "MANIFEST_SHA" and relpath not in digests:
                digests[relpath] = _sha_file(ROOT / relpath)
    scope = dict(scope)
    scope["_digests"] = digests
    stale_files = _verify_scope_fresh(scope, graph)
    if stale_files:
        return _result(
            started,
            "STALE",
            scope["level"],
            f"tree moved after scope computation: {stale_files}",
            impact,
            evidence,
        )
    results = []
    for command in scope["commands"]:
        results.append(run_cached(command, scope, graph, timeout_s=timeout_s, use_cache=use_cache))
    failed = [r for r in results if r["outcome"] == "FAIL"]
    status = "FAILED" if failed else "VALID"
    hits = sum(1 for r in results if r.get("cache") == "hit")
    return {
        "status": status,
        "scope": scope["level"],
        "commands": scope["commands"],
        "cache": f"{hits}/{len(results)} hits" if results else "miss",
        "results": [
            {
                "command": r["command"],
                "outcome": r["outcome"],
                "rc": r["rc"],
                "cache": r.get("cache", "miss"),
                "duration_ms": r.get("duration_ms", 0.0),
            }
            for r in results
        ],
        "impact": impact,
        "reason": "; ".join(scope.get("reasons", [])),
        "escalations": scope.get("escalations", []),
        "evidence": evidence,
        "timing_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _result(
    started: float, status: str, scope: str, reason: str, impact: dict, evidence: dict
) -> dict:
    return {
        "status": status,
        "scope": scope,
        "commands": [],
        "cache": "miss",
        "results": [],
        "impact": impact,
        "reason": reason,
        "escalations": [],
        "evidence": evidence,
        "timing_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _verify_scope_fresh(scope: dict, graph: dict) -> list[str]:
    """Re-hash scope command files just before execution (race guard).

    Returns mismatched paths; non-empty means the tree moved between scope
    computation and execution -> do NOT validate against stale scope (STALE).
    """
    wanted: set[str] = set()
    for command in scope.get("commands", []) or []:
        wanted.update(_relevant_files(command, graph))
    bad: list[str] = []
    for relpath in sorted(wanted):
        if relpath == "MANIFEST_SHA":
            continue
        current = _sha_file(ROOT / relpath)
        recorded = (scope.get("_digests", {}) or {}).get(relpath)
        if recorded is not None and current != recorded:
            bad.append(relpath)
    return bad


def benchmark() -> dict:
    """Scope-only benchmark (no tree mutation): miss/hit/escalation/governance."""
    out: dict = {}
    manifest, _ = execute_surface.build_manifest("add validation to broker registry")
    started = time.perf_counter()
    result = validate_manifest(manifest, run=False)
    out["scope_only_ms"] = round((time.perf_counter() - started) * 1000, 1)
    out["scope_level"] = result["scope"]
    out["scope_commands"] = len(result["commands"])
    started = time.perf_counter()
    result = validate_manifest(manifest, run=True)
    out["run_miss_ms"] = round((time.perf_counter() - started) * 1000, 1)
    out["run_miss_status"] = result["status"]
    started = time.perf_counter()
    result = validate_manifest(manifest, run=True)
    out["run_hit_ms"] = round((time.perf_counter() - started) * 1000, 1)
    out["run_hit_cache"] = result["cache"]
    out["escalations"] = result.get("escalations", [])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incremental + cached validation")
    parser.add_argument("--task", default="", help="task description (builds manifest)")
    parser.add_argument("--manifest", default=None, help="execution manifest JSON file")
    parser.add_argument("--file", default=None, help="explicit target file")
    parser.add_argument("--symbol", default=None, help="explicit target symbol")
    parser.add_argument("--module", default=None, help="explicit target module")
    parser.add_argument("--route", default=None, help="explicit route key")
    parser.add_argument("--run", action="store_true", help="execute the scope (cache-aware)")
    parser.add_argument("--full", action="store_true", help="force L5 full validation")
    parser.add_argument("--json", action="store_true", help="emit result as JSON")
    parser.add_argument("--no-cache", action="store_true", help="bypass result cache")
    parser.add_argument("--stats", action="store_true", help="scope/cache benchmark")
    args = parser.parse_args(argv)
    if args.stats:
        print(json.dumps(benchmark(), indent=2, sort_keys=True))
        return 0
    if args.manifest:
        try:
            manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(json.dumps({"status": "UNKNOWN", "reason": f"cannot load manifest: {exc}"}))
            return 2
        if not isinstance(manifest, dict):
            print(json.dumps({"status": "UNKNOWN", "reason": "manifest is not an object"}))
            return 2
    elif args.task or any([args.file, args.symbol, args.module, args.route]):
        manifest, _ = execute_surface.build_manifest(
            args.task,
            file=args.file,
            symbol=args.symbol,
            module=args.module,
            route_key=args.route,
            use_cache=not args.no_cache,
        )
    else:
        parser.print_help()
        return 2
    if args.no_cache:
        for path in VALID_DIR.glob("*.json"):
            _ = path
    try:
        result = validate_manifest(
            manifest,
            force_level="L5" if args.full else None,
            run=args.run,
            use_cache=not args.no_cache,
        )
    except ScopeError as exc:
        print(json.dumps({"status": "UNKNOWN", "reason": str(exc)}))
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(render_text(result))
    return {"VALID": 0, "FAILED": 1, "STALE": 1, "UNKNOWN": 2, "ESCALATED": 0}[result["status"]]


def render_text(result: dict) -> str:
    """Compact scan: STATUS/SCOPE/COMMANDS/RESULTS/IMPACT/REASON/TIMING."""
    lines = [f"STATUS: {result.get('status', '?')}  (scope {result.get('scope', '?')})"]
    lines.append(f"COMMANDS ({len(result.get('commands', []))}, cache {result.get('cache', '?')}):")
    for command in result.get("commands", [])[:12]:
        lines.append(f"  {command}")
    for entry in result.get("results", [])[:12]:
        lines.append(
            f"  [{entry.get('outcome', '?')}/{entry.get('cache', '?')}] "
            f"{entry.get('command', '?')} (rc={entry.get('rc')})"
        )
    impact = result.get("impact", {}) or {}
    lines.append(f"IMPACT: modules={impact.get('modules', [])}")
    lines.append(f"REASON: {result.get('reason', '') or '—'}")
    for esc in result.get("escalations", []) or []:
        lines.append(f"ESCALATED: {esc}")
    lines.append(f"TIMING: {result.get('timing_ms', 0)} ms")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
