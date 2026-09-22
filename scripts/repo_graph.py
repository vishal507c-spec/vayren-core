"""Repository Intelligence Graph — generator + query API (Phase 1).

Derived index only. Canonical authority always stays with:
  ownership_policy.json (ownership) / language_retention.json (retention) /
  architecture.md + DOMAIN_DEPS in validate_imports.py (dependencies) /
  module_contracts.md (contracts) / event_catalog.md (events) /
  task_routes.json (task routing).
If graph data ever conflicts with those sources, the canonical source wins.

Usage:
    python scripts/repo_graph.py --build
    python scripts/repo_graph.py --query module --name 05_strategy/strategy
    python scripts/repo_graph.py --query symbol --name BrokerRegistry
    python scripts/repo_graph.py --query file --name 09_broker/broker/registry.py
    python scripts/repo_graph.py --query owner --name PYTHON_STRATEGY
    python scripts/repo_graph.py --query deps --name 05_strategy/strategy
    python scripts/repo_graph.py --query dependents --name 01_core/core
    python scripts/repo_graph.py --query tests --name 09_broker/broker
    python scripts/repo_graph.py --query contract --name 05_strategy/strategy
    python scripts/repo_graph.py --stats
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
GRAPH_PATH = ROOT / "90_brain" / "repo_graph.json"
POLICY_PATH = ROOT / "90_brain" / "ownership_policy.json"
RETENTION_PATH = ROOT / "90_brain" / "language_retention.json"
ROUTES_PATH = ROOT / "90_brain" / "task_routes.json"
CONTRACTS_PATH = ROOT / "90_brain" / "module_contracts.md"

SCHEMA_VERSION = 1
LANGUAGES = ("RUST", "PYTHON", "RUST_SLINT")
INDEX_EXTENSIONS = {".py", ".rs", ".slint"}
EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "target",
    ".pytest_cache",
    ".ruff_cache",
    ".context_cache",
    ".forensics",
    "node_modules",
}
CHAPTER_RE = re.compile(r"^\d{2}_[a-z]+$")
RUST_ITEM_RE = re.compile(
    r"^(pub\s+)?(fn|struct|enum|trait|mod|type|const|static)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
SLINT_ITEM_RE = re.compile(r"^(export\s+)?(component|struct|global)\s+([A-Za-z_][A-Za-z0-9_]*)")
RS_USE_RE = re.compile(r"^\s*use\s+(vayren_core|vayren_shell)\b")
CONTRACT_SECTION_RE = re.compile(r"^###\s+\S+\s+Module:\s+`([^`]+)`")

# Synthetic owners for paths the ownership policy does not classify by prefix.
# Authority stays with the referenced canonical source; these add no new rules.
SYNTHETIC_OWNERS = {
    "rust/vayren-core": {
        "domain": "CORE",
        "required_language": "RUST",
        "authority": "AI_ENTRY.md section 1 (language map)",
    },
    "rust/vayren-shell": {
        "domain": "NATIVE_UI",
        "required_language": "RUST_SLINT",
        "authority": "AI_ENTRY.md section 1 (language map)",
    },
    "scripts": {
        "domain": "TOOLING",
        "required_language": "PYTHON",
        "authority": "90_brain/task_routes.json route tooling-validator",
    },
}

# task_routes.json ui-screen owns "rust/vayren-shell + view crates": any other
# rust/*view* crate is a native view crate (NATIVE_UI, RUST_SLINT).
VIEW_CRATE_AUTHORITY = "AI_ENTRY.md section 1 + 90_brain/task_routes.json route ui-screen"


def synthetic_for(module_id: str) -> dict | None:
    """Owner record for unclassified module roots. None = genuinely unknown."""
    if module_id in SYNTHETIC_OWNERS:
        return {"id": module_id, **SYNTHETIC_OWNERS[module_id]}
    if module_id.startswith("rust/") and "view" in Path(module_id).name:
        return {
            "id": module_id,
            "domain": "NATIVE_UI",
            "required_language": "RUST_SLINT",
            "authority": VIEW_CRATE_AUTHORITY,
        }
    return None


DOMAIN_MODULE_MAP = {
    "app": "00_app/app",
    "core": "01_core/core",
    "market": "03_market/market",
    "chart": "rust/vayren-shell",
    "data": "02_data/data",
    "strategy": "05_strategy/strategy",
    "backtest": "06_backtest/backtest",
    "risk": "07_risk/risk",
    "execution": "08_execution/execution",
    "broker": "09_broker/broker",
}

UNRESOLVED_CAP = 200


class GraphError(Exception):
    """Contradictory or unreadable canonical authority — generation must stop."""


def _read_json(path: Path, label: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GraphError(f"missing canonical source: {path}") from exc
    except ValueError as exc:
        raise GraphError(f"corrupt canonical source: {path}") from exc
    if not isinstance(data, dict):
        raise GraphError(f"corrupt canonical source (not an object): {path} ({label})")
    return data


def _load_domain_deps() -> dict[str, set[str]]:
    """Declared dependency rules. Machine truth lives in validate_imports.py."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import validate_imports  # noqa: E402
    finally:
        sys.path.remove(str(SCRIPTS_DIR))
    deps = validate_imports.DOMAIN_DEPS
    if not isinstance(deps, dict):
        raise GraphError("DOMAIN_DEPS in scripts/validate_imports.py is not a mapping")
    unknown = sorted(set(deps) - set(DOMAIN_MODULE_MAP))
    if unknown:
        raise GraphError(f"DOMAIN_DEPS has unmappable domains: {unknown}")
    return {domain: set(targets) for domain, targets in deps.items()}


def _rel_posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS or part.startswith(".venv") for part in path.parts)


def discover_module_roots() -> list[str]:
    """Chapter packages + Rust crates + scripts. Derived from the tree, not listed."""
    roots: set[str] = set()
    for chapter in sorted(ROOT.iterdir()):
        if not (chapter.is_dir() and CHAPTER_RE.match(chapter.name)):
            continue
        for pkg in sorted(chapter.iterdir()):
            if pkg.is_dir() and not _is_excluded(pkg) and next(pkg.glob("*.py"), None):
                roots.add(_rel_posix(pkg))
    rust_dir = ROOT / "rust"
    if rust_dir.is_dir():
        for crate in sorted(rust_dir.iterdir()):
            if crate.is_dir() and (crate / "Cargo.toml").is_file():
                roots.add(_rel_posix(crate))
    if (ROOT / "scripts").is_dir():
        roots.add("scripts")
    return sorted(roots)


def classify(relpath: str, rules: list[dict]) -> tuple[str | None, str | None, str | None]:
    """First-match ownership classification. Mirrors the policy ordering note."""
    for rule in rules:
        for prefix in rule.get("directory_prefixes", []):
            if relpath == prefix.rstrip("/") or relpath.startswith(prefix):
                excluded = any(
                    relpath == sub.rstrip("/") or relpath.startswith(sub)
                    for sub in rule.get("excluded_subpaths", [])
                )
                if excluded:
                    break
                return (
                    rule.get("id"),
                    rule.get("domain"),
                    rule.get("required_language"),
                )
    return None, None, None


def discover_submodules(roots: list[str], rules: list[dict]) -> list[str]:
    """Policy prefixes strictly inside a module root with different domain/language."""
    submodules: set[str] = set()
    for root in roots:
        root_rule = classify(root + "/", rules)[0]
        for rule in rules:
            for prefix in rule.get("directory_prefixes", []):
                clean = prefix.rstrip("/")
                if not clean.startswith(root + "/"):
                    continue
                if classify(prefix, rules)[0] == root_rule:
                    continue
                candidate = ROOT / clean
                if candidate.is_dir():
                    submodules.add(clean)
    return sorted(submodules)


def owning_module(relpath: str, modules: list[str]) -> str | None:
    """Longest module-root prefix wins (submodules beat parents)."""
    best: str | None = None
    for mid in modules:
        if (relpath == mid or relpath.startswith(mid + "/")) and (
            best is None or len(mid) > len(best)
        ):
            best = mid
    return best


def is_test_file(relpath: str) -> bool:
    parts = relpath.split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    return "tests" in parts or stem.startswith("test_") or stem.endswith("_test")


def dotted_name(relpath: str) -> str:
    """Deterministic dotted base for a file (chapter prefix dropped)."""
    parts = Path(relpath).parts
    if parts and CHAPTER_RE.match(parts[0]):
        parts = parts[1:]
    stem = Path(parts[-1]).stem
    if stem == "__init__":
        return ".".join(parts[:-1])
    return ".".join([*parts[:-1], stem])


def parse_python_symbols(
    relpath: str, source: str, unresolved: list[dict]
) -> tuple[list[dict], dict[str, str], dict[str, dict]]:
    """Top-level classes/functions + intra-file alias map + raw call refs."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        unresolved.append({"kind": "parse-failed", "ref": relpath, "reason": "SyntaxError"})
        return [], {}, {"aliases": {}, "raw": {}}
    base = dotted_name(relpath)
    dunder_all: set[str] | None = None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            dunder_all = {
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
    symbols: list[dict] = []
    local: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            qualified = f"{base}:{node.name}"
            public = not node.name.startswith("_") and (
                dunder_all is None or node.name in dunder_all
            )
            symbols.append(
                {
                    "name": node.name,
                    "qualified": qualified,
                    "dotted": f"{base}.{node.name}",
                    "file": relpath,
                    "line": node.lineno,
                    "kind": kind,
                    "public": public,
                }
            )
            local[node.name] = qualified
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                aliases[(alias.asname or alias.name).split(".")[0]] = alias.name
    raw_calls: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        caller = local[node.name]
        refs: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                refs.add(child.id)
        raw_calls[caller] = refs
    return symbols, local, {"aliases": aliases, "raw": raw_calls}


def parse_rust_symbols(relpath: str, lines: list[str]) -> list[dict]:
    symbols: list[dict] = []
    crate = "::".join(Path(relpath).with_suffix("").parts)
    for lineno, line in enumerate(lines, start=1):
        match = RUST_ITEM_RE.match(line)
        if not match:
            continue
        public, kind, name = match.groups()
        symbols.append(
            {
                "name": name,
                "qualified": f"{crate}::{name}",
                "dotted": f"{crate}::{name}",
                "file": relpath,
                "line": lineno,
                "kind": kind,
                "public": bool(public),
            }
        )
    return symbols


def parse_slint_symbols(relpath: str, lines: list[str]) -> list[dict]:
    symbols: list[dict] = []
    base = "::".join(Path(relpath).with_suffix("").parts)
    for lineno, line in enumerate(lines, start=1):
        match = SLINT_ITEM_RE.match(line)
        if not match:
            continue
        export, kind, name = match.groups()
        symbols.append(
            {
                "name": name,
                "qualified": f"{base}::{name}",
                "dotted": f"{base}::{name}",
                "file": relpath,
                "line": lineno,
                "kind": f"slint-{kind}",
                "public": bool(export),
            }
        )
    return symbols


def module_dotted_base(module_id: str) -> str:
    parts = Path(module_id).parts
    if parts and CHAPTER_RE.match(parts[0]):
        parts = parts[1:]
    return ".".join(parts)


def actual_module_for_dotted(dotted: str, modules: list[str]) -> str | None:
    """Longest module dotted-base prefix of an imported dotted path."""
    best: str | None = None
    for mid in modules:
        base = module_dotted_base(mid)
        if (dotted == base or dotted.startswith(base + ".")) and (
            best is None or len(base) > len(module_dotted_base(best))
        ):
            best = mid
    return best


def record_actual_edge(
    actual: dict[str, set[str]],
    evidence: dict[tuple[str, str], set[str]],
    source: str,
    target: str | None,
    relpath: str,
) -> None:
    """Record one actual module dependency with its importing file as evidence."""
    if target and target != source:
        actual[source].add(target)
        evidence.setdefault((source, target), set()).add(relpath)


def resolve_file_calls(
    by_dotted: dict[str, str],
    alias_data: dict[str, dict],
    local: dict[str, str],
) -> dict[str, list[str]]:
    """Resolve one file's caller -> callee edges (pure; reused by incremental index)."""
    aliases = alias_data["aliases"]
    resolved: dict[str, list[str]] = {}
    for caller, refs in alias_data["raw"].items():
        call_targets: set[str] = set()
        for ref in refs:
            if ref in local:
                call_targets.add(local[ref])
            elif ref in aliases and aliases[ref] in by_dotted:
                call_targets.add(by_dotted[aliases[ref]])
        resolved[caller] = sorted(call_targets - {caller})
    return resolved


def rebuild_called_by(symbols: list[dict]) -> None:
    """Rebuild the global called_by inverse from calls (deterministic)."""
    for symbol in symbols:
        symbol["called_by"] = []
    qual_index = {s["qualified"]: s for s in symbols}
    for symbol in symbols:
        for target in symbol["calls"]:
            if target in qual_index:
                qual_index[target]["called_by"].append(symbol["qualified"])
    for symbol in symbols:
        symbol["called_by"] = sorted(symbol["called_by"])


def resolve_symbol_calls(
    symbols: list[dict], py_alias_data: dict[str, dict], py_local_data: dict[str, dict[str, str]]
) -> None:
    """Resolve calls for every Python file, then rebuild called_by (full build)."""
    by_dotted = {s["dotted"]: s["qualified"] for s in symbols}
    qual_index = {s["qualified"]: s for s in symbols}
    for relpath, alias_data in py_alias_data.items():
        for caller, targets in resolve_file_calls(
            by_dotted, alias_data, py_local_data[relpath]
        ).items():
            qual_index[caller]["calls"] = targets
    rebuild_called_by(symbols)


def graph_counts(
    n_domains: int,
    n_owners: int,
    n_languages: int,
    modules: list[dict],
    files: list[dict],
    symbols: list[dict],
    tests: list[dict],
    validators: list[dict],
) -> tuple[int, int]:
    """Entity/relationship totals with the exact full-build formula."""
    entity_count = (
        n_domains
        + n_owners
        + n_languages
        + len(modules)
        + len(files)
        + len(symbols)
        + len(tests)
        + len(validators)
    )
    relationship_count = (
        sum(len(m["depends_on_declared"]) for m in modules)
        + sum(len(m["depends_on_actual"]) for m in modules)
        + sum(len(m["depended_on_by"]) for m in modules)
        + sum(len(s["calls"]) for s in symbols)
        + sum(len(s["called_by"]) for s in symbols)
        + sum(len(s["tests"]) for s in symbols)
    )
    return entity_count, relationship_count


def test_targets(relpath: str, module_id: str, known_files: set[str]) -> tuple[list[str], str]:
    """Sibling-source heuristic; falls back to module-level mapping (no guessing)."""
    stem = Path(relpath).stem
    candidates: list[str] = []
    if stem.startswith("test_"):
        candidates.append(stem[len("test_") :])
    elif stem.endswith("_test"):
        candidates.append(stem[: -len("_test")])
    parts = relpath.split("/")
    nosetests = [p for p in parts if p != "tests"]
    for candidate in candidates:
        sibling = "/".join([*nosetests[:-1], candidate + Path(relpath).suffix])
        if sibling in known_files and sibling != relpath:
            return [sibling], "file"
        flat = f"{module_id}/{candidate}{Path(relpath).suffix}"
        if flat in known_files and flat != relpath:
            return [flat], "file"
    if relpath.startswith("rust/"):
        return [], "module"
    return [], "module"


def parse_contract_sections() -> tuple[dict[str, str], str | None]:
    """Chapter -> contract section reference. Parsed, never copied.

    Also returns the presentation (Rust+Slint shell) section for native UI crates.
    """
    try:
        text = CONTRACTS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise GraphError(f"missing canonical source: {CONTRACTS_PATH}") from exc
    sections: dict[str, str] = {}
    shell_contract: str | None = None
    for line in text.splitlines():
        match = CONTRACT_SECTION_RE.match(line)
        if match:
            chapter = match.group(1).split("/")[0]
            heading = line.lstrip("#").strip().split("—")[0].strip()
            sections[chapter] = f"90_brain/module_contracts.md {heading}"
        elif line.startswith("###") and "vayren-shell" in line:
            heading = line.lstrip("#").strip().split("—")[0].strip()
            shell_contract = f"90_brain/module_contracts.md {heading}"
    return sections, shell_contract


def normalize_route_dep(raw: str, modules: list[str]) -> str | None:
    lowered = raw.lower()
    for mid in sorted(modules, key=len, reverse=True):
        if mid.lower() in lowered:
            return mid
    if "vayren-shell" in lowered:
        return "rust/vayren-shell"
    if "vayren-core" in lowered or "kernel" in lowered:
        return "rust/vayren-core"
    return None


def build_graph() -> tuple[dict, list[dict]]:
    started = time.perf_counter()
    unresolved: list[dict] = []

    def note(kind: str, ref: str, reason: str) -> None:
        if len(unresolved) < UNRESOLVED_CAP:
            unresolved.append({"kind": kind, "ref": ref, "reason": reason})

    policy = _read_json(POLICY_PATH, "ownership policy")
    retention = _read_json(RETENTION_PATH, "language retention")
    routes_doc = _read_json(ROUTES_PATH, "task routes")
    domain_deps = _load_domain_deps()
    contract_sections, shell_contract = parse_contract_sections()

    rules = policy.get("rules", [])
    if not isinstance(rules, list) or not rules:
        raise GraphError("ownership_policy.json has no rules")
    rule_ids = [r.get("id") for r in rules if isinstance(r, dict)]
    if len(set(rule_ids)) != len(rule_ids):
        raise GraphError("ownership_policy.json has duplicate rule ids")
    routes = routes_doc.get("routes", [])
    if not isinstance(routes, list) or not routes:
        raise GraphError("task_routes.json has no routes")
    retention_files = retention.get("files", {})
    if not isinstance(retention_files, dict):
        raise GraphError("language_retention.json has no files mapping")

    roots = discover_module_roots()
    if not roots:
        raise GraphError("no module roots discovered")
    modules = sorted(set(roots) | set(discover_submodules(roots, rules)))

    owners: dict[str, dict] = {}
    for rule in rules:
        owners[rule["id"]] = {
            "id": rule["id"],
            "domain": rule.get("domain", "UNKNOWN"),
            "required_language": rule.get("required_language", "UNKNOWN"),
            "authority": "90_brain/ownership_policy.json",
        }

    def route_owner_for(module_id: str) -> dict | None:
        """Route fallback: exact owner_module/owner_paths match (canonical routing)."""
        hits = [
            r
            for r in routes
            if isinstance(r, dict)
            and (r.get("owner_module") == module_id or module_id in r.get("owner_paths", []))
        ]
        if not hits:
            return None
        route = sorted(hits, key=lambda r: str(r.get("key", "")))[0]
        return {
            "id": f"route:{route.get('key')}",
            "domain": route.get("domain", "UNKNOWN"),
            "required_language": route.get("language", "UNKNOWN"),
            "authority": "90_brain/task_routes.json",
        }

    module_owner: dict[str, str] = {}
    module_domain: dict[str, str] = {}
    module_language: dict[str, str] = {}
    module_owner_source: dict[str, str] = {}
    for mid in modules:
        rule_id, domain, language = classify(mid + "/", rules)
        if rule_id is not None:
            module_owner[mid] = rule_id
            module_domain[mid] = domain or "UNKNOWN"
            module_language[mid] = language or "UNKNOWN"
            module_owner_source[mid] = "policy"
            continue
        synth = synthetic_for(mid)
        if synth is not None:
            owners[synth["id"]] = synth
            module_owner[mid] = synth["id"]
            module_domain[mid] = synth["domain"]
            module_language[mid] = synth["required_language"]
            module_owner_source[mid] = "synthetic"
            continue
        fallback = route_owner_for(mid)
        if fallback is not None:
            owners[fallback["id"]] = fallback
            module_owner[mid] = fallback["id"]
            module_domain[mid] = fallback["domain"]
            module_language[mid] = fallback["required_language"]
            module_owner_source[mid] = "route"
            continue
        module_owner[mid] = "UNKNOWN"
        module_domain[mid] = "UNKNOWN"
        module_language[mid] = "UNKNOWN"
        module_owner_source[mid] = "unknown"
        note("owner-unknown", mid, "no policy rule, synthetic owner, or route covers it")

    domains: dict[str, dict] = {}
    for owner in owners.values():
        domain = owner["domain"]
        domains.setdefault(
            domain,
            {
                "id": domain,
                "required_language": owner["required_language"],
                "rule_ids": [],
                "authority": "90_brain/ownership_policy.json",
            },
        )
        if owner["id"] not in SYNTHETIC_OWNERS:
            domains[domain]["rule_ids"].append(owner["id"])
    for domain in domains.values():
        domain["rule_ids"] = sorted(domain["rule_ids"])

    # Files.
    files: list[dict] = []
    for mid in modules:
        root = ROOT / mid
        if not root.is_dir():  # file-level rule prefixes never become modules
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in INDEX_EXTENSIONS:
                continue
            if _is_excluded(path):
                continue
            relpath = _rel_posix(path)
            if owning_module(relpath, modules) != mid:
                continue  # belongs to a nested submodule
            files.append({"path": relpath, "module": mid})
    known_files = {f["path"] for f in files}

    for record in files:
        relpath = record["path"]
        mid = record["module"]
        record["is_test"] = is_test_file(relpath)
        if record["is_test"]:
            # Policy test_files rule: tests inherit their parent module's ownership.
            record["owner"] = module_owner[mid]
            record["domain"] = module_domain[mid]
            record["language"] = module_language[mid]
            record["owner_source"] = module_owner_source[mid]
            record["retention_state"] = None
            continue
        rule_id, domain, language = classify(relpath, rules)
        if rule_id is not None:
            record["owner"] = rule_id
            record["domain"] = domain
            record["language"] = language or "UNKNOWN"
            record["owner_source"] = "policy"
        elif module_owner_source[mid] != "unknown":
            # Policy is silent (e.g. 00_app root files): inherit the module's
            # ownership, which itself comes from a canonical fallback source.
            record["owner"] = module_owner[mid]
            record["domain"] = module_domain[mid]
            record["language"] = module_language[mid]
            record["owner_source"] = module_owner_source[mid]
        else:
            note("owner-unknown", relpath, "no policy rule and module owner unknown")
            record["owner"] = "UNKNOWN"
            record["domain"] = "UNKNOWN"
            record["language"] = "UNKNOWN"
            record["owner_source"] = "unknown"
        record["retention_state"] = retention_files.get(relpath, {}).get("state")

    # Symbols.
    symbols: list[dict] = []
    py_alias_data: dict[str, dict] = {}
    py_local_data: dict[str, dict[str, str]] = {}
    for record in sorted(files, key=lambda f: f["path"]):
        relpath = record["path"]
        try:
            text = (ROOT / relpath).read_text(encoding="utf-8")
        except OSError:
            note("file-unreadable", relpath, "cannot read file")
            continue
        if relpath.endswith(".py"):
            found, local, alias_data = parse_python_symbols(relpath, text, unresolved)
            py_local_data[relpath] = local
            py_alias_data[relpath] = alias_data
        elif relpath.endswith(".rs"):
            found = parse_rust_symbols(relpath, text.splitlines())
        else:
            found = parse_slint_symbols(relpath, text.splitlines())
        for symbol in found:
            symbol["module"] = record["module"]
            symbol["language"] = record["language"]
            symbol["calls"] = []
            symbol["called_by"] = []
            symbol["callers_complete"] = relpath.endswith(".py")
            symbol["tests"] = []
            symbols.append(symbol)

    resolve_symbol_calls(symbols, py_alias_data, py_local_data)

    # Tests.
    tests: list[dict] = []
    for record in sorted(files, key=lambda f: f["path"]):
        if not record["is_test"]:
            continue
        targets, granularity = test_targets(record["path"], record["module"], known_files)
        tests.append(
            {
                "path": record["path"],
                "module": record["module"],
                "targets": targets,
                "granularity": granularity,
            }
        )
    file_test_map: dict[str, list[str]] = {}
    for test in tests:
        for target in test["targets"]:
            file_test_map.setdefault(target, []).append(test["path"])
    for symbol in symbols:
        # Direct file-targeted tests only; module-level coverage is resolved
        # at query time so records stay compact.
        symbol["tests"] = sorted(file_test_map.get(symbol["file"], []))

    # Declared dependencies: validator DOMAIN_DEPS + route depends_on for
    # chapter modules (route depends_on describes feature areas, not Rust
    # crate internals, so routes never declare rust/* edges).
    declared: dict[str, set[str]] = {mid: set() for mid in modules}
    declared_sources: dict[tuple[str, str], set[str]] = {}
    chapter_of = {mid: Path(mid).parts[0] for mid in modules}

    def add_declared(mid: str, dep: str, source: str) -> None:
        if dep == mid:
            return
        declared[mid].add(dep)
        declared_sources.setdefault((mid, dep), set()).add(source)

    for mid in modules:
        chapter = chapter_of[mid]
        for domain, targets in domain_deps.items():
            if DOMAIN_MODULE_MAP.get(domain) == mid and CHAPTER_RE.match(chapter):
                for target in targets:
                    if target in DOMAIN_MODULE_MAP:
                        add_declared(mid, DOMAIN_MODULE_MAP[target], f"DOMAIN_DEPS:{domain}")
    for route in routes:
        if not isinstance(route, dict):
            continue
        for op in route.get("owner_paths", []):
            for mid in modules:
                if not CHAPTER_RE.match(chapter_of[mid]):
                    continue
                if op == mid or op.startswith(mid + "/") or mid.startswith(op + "/"):
                    for raw in route.get("depends_on", []):
                        normalized = normalize_route_dep(str(raw), modules)
                        if normalized:
                            add_declared(mid, normalized, f"route:{route.get('key')}")
                        else:
                            note(
                                "dep-normalize",
                                f"{route.get('key')}:{raw}",
                                "route dependency maps to no known module",
                            )
    for mid in modules:
        for dep in sorted(declared[mid]):
            if dep not in modules:
                note("dep-unknown-module", f"{mid}->{dep}", "declared target is no module")

    # Actual dependencies from imports, with per-file evidence.
    actual: dict[str, set[str]] = {mid: set() for mid in modules}
    actual_evidence: dict[tuple[str, str], set[str]] = {}
    for record in files:
        if record["is_test"]:
            continue
        relpath = record["path"]
        try:
            text = (ROOT / relpath).read_text(encoding="utf-8")
        except OSError:
            continue
        source_module = record["module"]

        if relpath.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    if node.module:
                        record_actual_edge(
                            actual,
                            actual_evidence,
                            source_module,
                            actual_module_for_dotted(node.module, modules),
                            relpath,
                        )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        record_actual_edge(
                            actual,
                            actual_evidence,
                            source_module,
                            actual_module_for_dotted(alias.name, modules),
                            relpath,
                        )
        elif relpath.endswith(".rs"):
            for line in text.splitlines():
                match = RS_USE_RE.match(line)
                if match:
                    crate = match.group(1).replace("_", "-")
                    target = f"rust/{crate}"
                    if target in modules:
                        record_actual_edge(actual, actual_evidence, source_module, target, relpath)

    dependencies: list[dict] = []
    for mid in modules:
        for dep in sorted(declared[mid]):
            dependencies.append(
                {
                    "from": mid,
                    "to": dep,
                    "kind": "declared",
                    "sources": sorted(declared_sources.get((mid, dep), [])),
                }
            )
        for dep in sorted(actual[mid]):
            dependencies.append(
                {
                    "from": mid,
                    "to": dep,
                    "kind": "actual",
                    "sources": sorted(actual_evidence.get((mid, dep), [])),
                }
            )
    dependencies.sort(key=lambda d: (d["from"], d["to"], d["kind"]))

    drift: list[dict] = []
    for mid in modules:
        for dep in sorted(actual[mid] - declared[mid]):
            drift.append({"from": mid, "to": dep, "kind": "actual-without-declared"})
        for dep in sorted(declared[mid] - actual[mid]):
            drift.append({"from": mid, "to": dep, "kind": "declared-without-actual"})
    drift.sort(key=lambda d: (d["from"], d["to"], d["kind"]))

    depended_on_by: dict[str, set[str]] = {mid: set() for mid in modules}
    for edge in dependencies:
        if edge["to"] in depended_on_by:
            depended_on_by[edge["to"]].add(edge["from"])

    # Contracts + routes per module.
    route_keys: dict[str, list[str]] = {mid: [] for mid in modules}
    for route in routes:
        if not isinstance(route, dict):
            continue
        for op in route.get("owner_paths", []):
            for mid in modules:
                if op == mid or op.startswith(mid + "/"):
                    key = str(route.get("key", ""))
                    if key and key not in route_keys[mid]:
                        route_keys[mid].append(key)
    for mid in route_keys:
        route_keys[mid] = sorted(route_keys[mid])

    module_records: list[dict] = []
    for mid in modules:
        chapter = chapter_of[mid]
        contract = contract_sections.get(chapter)
        if contract is None and (
            mid == "rust/vayren-shell"
            or (mid.startswith("rust/") and module_domain[mid] == "NATIVE_UI")
        ):
            contract = shell_contract
        if contract is None:
            parent = owning_parent_module(mid, modules)
            if parent is not None and parent != mid:
                contract = contract_sections.get(Path(parent).parts[0])
        if contract is None and mid == "rust/vayren-core":
            contract = "AI_ENTRY.md section 1 (language map)"
        if contract is None and mid == "scripts":
            contract = "AGENTS.md workflow + Makefile check"
        if contract is None:
            note("contract-unknown", mid, "no module_contracts section or route covers it")
            contract = "UNKNOWN"
        module_records.append(
            {
                "id": mid,
                "domain": module_domain[mid],
                "owner": module_owner[mid],
                "owner_source": module_owner_source[mid],
                "language": module_language[mid],
                "files": sorted(f["path"] for f in files if f["module"] == mid),
                "depends_on_declared": sorted(declared[mid]),
                "depends_on_actual": sorted(actual[mid]),
                "depended_on_by": sorted(depended_on_by[mid]),
                "contract": contract,
                "route_keys": route_keys[mid],
            }
        )

    validators = sorted(
        ({"path": _rel_posix(p), "scope": "repo"} for p in SCRIPTS_DIR.glob("validate_*.py")),
        key=lambda v: v["path"],
    )

    canonical_bytes = (
        b"".join(p.read_bytes() for p in (POLICY_PATH, RETENTION_PATH, ROUTES_PATH, CONTRACTS_PATH))
        + (SCRIPTS_DIR / "validate_imports.py").read_bytes()
    )
    inputs_hash = hashlib.sha256(canonical_bytes).hexdigest()

    entity_count, relationship_count = graph_counts(
        len(domains),
        len(owners),
        len(LANGUAGES),
        module_records,
        files,
        symbols,
        tests,
        validators,
    )

    graph = {
        "schema_version": SCHEMA_VERSION,
        "repository": {"name": "vayren-core", "root": "."},
        "inputs_hash": inputs_hash,
        "entity_count": entity_count,
        "relationship_count": relationship_count,
        "domains": sorted(domains.values(), key=lambda d: d["id"]),
        "owners": sorted(owners.values(), key=lambda o: o["id"]),
        "languages": [{"id": lang} for lang in LANGUAGES],
        "modules": sorted(module_records, key=lambda m: m["id"]),
        "files": sorted(files, key=lambda f: f["path"]),
        "symbols": sorted(symbols, key=lambda s: s["qualified"]),
        "dependencies": dependencies,
        "drift": drift,
        "tests": sorted(tests, key=lambda t: t["path"]),
        "validators": validators,
        "unresolved": sorted(unresolved, key=lambda u: (u["kind"], u["ref"])),
    }
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return graph, [{"kind": "build", "elapsed_ms": round(elapsed_ms, 1)}]


def owning_parent_module(module_id: str, modules: list[str]) -> str | None:
    candidates = [mid for mid in modules if mid != module_id and module_id.startswith(mid + "/")]
    return max(candidates, key=len) if candidates else None


def write_graph(graph: dict, path: Path | None = None) -> int:
    path = path or GRAPH_PATH
    text = json.dumps(graph, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def load_graph(path: Path | None = None) -> dict:
    path = path or GRAPH_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GraphError(f"graph is not an object: {path}")
    return data


# ── Query API (reads the generated graph; never scans the repository) ──


def query_module(graph: dict, name: str) -> dict | None:
    for module in graph.get("modules", []):
        if module["id"] == name:
            return module
    return None


def query_file(graph: dict, name: str) -> dict | None:
    for record in graph.get("files", []):
        if record["path"] == name:
            return record
    return None


def query_symbol(graph: dict, name: str) -> list[dict]:
    exact = [s for s in graph.get("symbols", []) if s["qualified"] == name]
    if exact:
        return exact
    return sorted(
        (s for s in graph.get("symbols", []) if s["name"] == name),
        key=lambda s: s["qualified"],
    )


def query_owner(graph: dict, name: str) -> dict | None:
    for owner in graph.get("owners", []):
        if owner["id"] == name:
            modules = sorted(m["id"] for m in graph.get("modules", []) if m["owner"] == name)
            files = sorted(f["path"] for f in graph.get("files", []) if f["owner"] == name)
            return {**owner, "modules": modules, "files": files}
    return None


def query_dependencies(graph: dict, name: str) -> dict | None:
    module = query_module(graph, name)
    if module is None:
        return None
    return {
        "module": name,
        "declared": module["depends_on_declared"],
        "actual": module["depends_on_actual"],
    }


def query_dependents(graph: dict, name: str) -> dict | None:
    module = query_module(graph, name)
    if module is None:
        return None
    return {"module": name, "depended_on_by": module["depended_on_by"]}


def query_tests(graph: dict, name: str) -> dict | None:
    module = query_module(graph, name)
    if module is None:
        symbols = query_symbol(graph, name)
        if symbols:
            direct = sorted({t for s in symbols for t in s["tests"]})
            inherited = sorted(
                {
                    t["path"]
                    for s in symbols
                    for t in graph.get("tests", [])
                    if t["module"] == s["module"]
                }
            )
            return {"symbol": name, "tests": direct, "module_tests": inherited}
        record = query_file(graph, name)
        if record is None:
            return None
        hits = [t for t in graph.get("tests", []) if t["path"] == name or name in t["targets"]]
        return {"file": name, "tests": hits}
    hits = [t for t in graph.get("tests", []) if t["module"] == name]
    return {"module": name, "tests": hits}


def query_contract(graph: dict, name: str) -> dict | None:
    module = query_module(graph, name)
    if module is None:
        return None
    return {"module": name, "contract": module["contract"], "routes": module["route_keys"]}


QUERIES = (
    "module",
    "file",
    "symbol",
    "owner",
    "deps",
    "dependents",
    "tests",
    "contract",
)


def run_query(graph: dict, kind: str, name: str) -> dict | list | None:
    if kind == "module":
        return query_module(graph, name)
    if kind == "file":
        return query_file(graph, name)
    if kind == "symbol":
        return query_symbol(graph, name)
    if kind == "owner":
        return query_owner(graph, name)
    if kind == "deps":
        return query_dependencies(graph, name)
    if kind == "dependents":
        return query_dependents(graph, name)
    if kind == "tests":
        return query_tests(graph, name)
    if kind == "contract":
        return query_contract(graph, name)
    raise GraphError(f"unknown query kind: {kind} (expected one of {', '.join(QUERIES)})")


def stats_report(graph: dict, path: Path = GRAPH_PATH) -> dict:
    return {
        "schema_version": graph.get("schema_version"),
        "entity_count": graph.get("entity_count"),
        "relationship_count": graph.get("relationship_count"),
        "modules": len(graph.get("modules", [])),
        "files": len(graph.get("files", [])),
        "symbols": len(graph.get("symbols", [])),
        "dependencies": len(graph.get("dependencies", [])),
        "tests": len(graph.get("tests", [])),
        "drift": len(graph.get("drift", [])),
        "unresolved": len(graph.get("unresolved", [])),
        "graph_bytes": path.stat().st_size if path.is_file() else 0,
    }


def lookup_timed(graph: dict, kind: str, name: str, repeats: int = 1000) -> float:
    """Mean in-process lookup time in microseconds (single query, no I/O)."""
    started = time.perf_counter()
    for _ in range(repeats):
        run_query(graph, kind, name)
    return (time.perf_counter() - started) / repeats * 1e6


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repository Intelligence Graph")
    parser.add_argument("--build", action="store_true", help="regenerate 90_brain/repo_graph.json")
    parser.add_argument("--query", choices=QUERIES, help="lookup kind")
    parser.add_argument("--name", default="", help="module/file/symbol/owner name")
    parser.add_argument("--stats", action="store_true", help="print graph measurements")
    args = parser.parse_args(argv)
    if args.build:
        try:
            graph, _timing = build_graph()
        except GraphError as exc:
            print(f"REPO_GRAPH FAIL: {exc}", file=sys.stderr)
            return 1
        size = write_graph(graph)
        report = stats_report(graph)
        report["graph_bytes"] = size
        print(json.dumps(report, indent=2, sort_keys=True))
        if graph["unresolved"]:
            print(f"unresolved: {len(graph['unresolved'])} (see graph.unresolved)")
        return 0
    if args.query:
        if not args.name:
            print("REPO_GRAPH FAIL: --name is required with --query", file=sys.stderr)
            return 1
        try:
            graph = load_graph()
        except (OSError, ValueError, GraphError) as exc:
            print(f"REPO_GRAPH FAIL: {exc}", file=sys.stderr)
            return 1
        try:
            result = run_query(graph, args.query, args.name)
        except GraphError as exc:
            print(f"REPO_GRAPH FAIL: {exc}", file=sys.stderr)
            return 1
        if result is None or result == []:
            print(json.dumps({"status": "UNKNOWN", "query": args.query, "name": args.name}))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.stats:
        try:
            graph = load_graph()
        except (OSError, ValueError, GraphError) as exc:
            print(f"REPO_GRAPH FAIL: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(stats_report(graph), indent=2, sort_keys=True))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
