"""Validator for the derived Repository Intelligence Graph (Phase 1).

Validates the graph artifact only — schema, references, and consistency with
the canonical sources (which always win on conflict). Never replaces the
existing validators (imports/structure/language/architecture/authority/routes).

Usage:
    python scripts/validate_repo_graph.py [--graph 90_brain/repo_graph.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_graph  # noqa: E402

ROOT = SCRIPTS_DIR.parent
LANGUAGES = ("RUST", "PYTHON", "RUST_SLINT")
OWNER_SOURCES = ("policy", "synthetic", "route", "unknown")
RETENTION_STATES = (
    "MIGRATED",
    "MIGRATION_REQUIRED",
    "TEMPORARILY_RETAINED",
    "EXEMPT_WITH_JUSTIFICATION",
)


def _independent_classify(relpath: str, rules: list[dict]) -> str | None:
    """First-match rule id, re-derived independently from the policy JSON."""
    for rule in rules:
        for prefix in rule.get("directory_prefixes", []):
            if relpath == prefix.rstrip("/") or relpath.startswith(prefix):
                if any(
                    relpath == sub.rstrip("/") or relpath.startswith(sub)
                    for sub in rule.get("excluded_subpaths", [])
                ):
                    break
                return rule.get("id")
    return None


def _expected_inputs_hash() -> str:
    canonical = (
        b"".join(
            path.read_bytes()
            for path in (
                repo_graph.POLICY_PATH,
                repo_graph.RETENTION_PATH,
                repo_graph.ROUTES_PATH,
                repo_graph.CONTRACTS_PATH,
            )
        )
        + (SCRIPTS_DIR / "validate_imports.py").read_bytes()
    )
    return hashlib.sha256(canonical).hexdigest()


def validate_graph(graph: dict, root: Path | None = None) -> list[str]:
    """Pure validation. Returns a list of failure strings (empty = PASS).

    `root` overrides the repository root for disk checks (sandbox testing);
    default is the real repository root (Phase-1 behavior unchanged).
    """
    errors: list[str] = []
    disk_root = root or ROOT
    if graph.get("schema_version") != repo_graph.SCHEMA_VERSION:
        errors.append(
            f"schema: schema_version is {graph.get('schema_version')!r}, "
            f"expected {repo_graph.SCHEMA_VERSION}"
        )

    modules = graph.get("modules", [])
    files = graph.get("files", [])
    symbols = graph.get("symbols", [])
    owners = graph.get("owners", [])
    domains = graph.get("domains", [])
    dependencies = graph.get("dependencies", [])
    tests = graph.get("tests", [])
    drift = graph.get("drift", [])
    unresolved = graph.get("unresolved", [])

    def check_unique(records: list[dict], key: str, label: str) -> None:
        seen: set[str] = set()
        for record in records:
            value = record.get(key, "")
            if value in seen:
                errors.append(f"schema: duplicate {label} id {value!r}")
            seen.add(value)

    check_unique(modules, "id", "module")
    check_unique(files, "path", "file")
    check_unique(symbols, "qualified", "symbol")
    check_unique(owners, "id", "owner")
    check_unique(domains, "id", "domain")

    module_ids = {m.get("id") for m in modules}
    owner_ids = {o.get("id") for o in owners}
    domain_ids = {d.get("id") for d in domains}
    file_paths = {f.get("path") for f in files}
    symbol_quals = {s.get("qualified") for s in symbols}

    policy: dict = {}
    routes_doc: dict = {}
    retention: dict = {}
    try:
        policy = json.loads(repo_graph.POLICY_PATH.read_text(encoding="utf-8"))
        policy_rule_ids = {r.get("id") for r in policy.get("rules", []) if isinstance(r, dict)}
    except (OSError, ValueError):
        policy_rule_ids = set()
        errors.append("canonical: cannot read 90_brain/ownership_policy.json")
    try:
        routes_doc = json.loads(repo_graph.ROUTES_PATH.read_text(encoding="utf-8"))
        route_keys = {r.get("key") for r in routes_doc.get("routes", []) if isinstance(r, dict)}
    except (OSError, ValueError):
        route_keys = set()
        errors.append("canonical: cannot read 90_brain/task_routes.json")
    try:
        retention = json.loads(repo_graph.RETENTION_PATH.read_text(encoding="utf-8"))
        retention_files = retention.get("files", {})
    except (OSError, ValueError):
        retention_files = {}
        errors.append("canonical: cannot read 90_brain/language_retention.json")

    for owner in owners:
        owner_id = owner.get("id", "")
        if owner_id in policy_rule_ids:
            continue
        if owner_id.startswith("route:") and owner_id[len("route:") :] in route_keys:
            continue
        if repo_graph.synthetic_for(owner_id) is not None:
            continue
        errors.append(f"owner: {owner_id!r} matches no policy rule, route, or synthetic owner")

    for module in modules:
        mid = module.get("id", "")
        if module.get("owner") not in owner_ids:
            errors.append(f"module: {mid!r} has unknown owner {module.get('owner')!r}")
        if module.get("domain") not in domain_ids:
            errors.append(f"module: {mid!r} has unknown domain {module.get('domain')!r}")
        if module.get("language") not in LANGUAGES:
            errors.append(f"module: {mid!r} has invalid language {module.get('language')!r}")
        if module.get("owner_source") not in OWNER_SOURCES:
            errors.append(f"module: {mid!r} has invalid owner_source")
        if not module.get("contract"):
            errors.append(f"module: {mid!r} has empty contract reference")
        for dep in module.get("depends_on_declared", []) + module.get("depends_on_actual", []):
            if dep not in module_ids:
                errors.append(f"module: {mid!r} depends on unknown module {dep!r}")

    file_module: dict[str, str] = {}
    file_language: dict[str, str] = {}
    for record in files:
        path = record.get("path", "")
        mid = record.get("module", "")
        file_module[path] = mid
        file_language[path] = record.get("language", "")
        if not (disk_root / path).is_file():
            errors.append(f"file: {path!r} does not exist on disk")
        if mid not in module_ids:
            errors.append(f"file: {path!r} belongs to unknown module {mid!r}")
        elif path != mid and not path.startswith(mid + "/"):
            errors.append(f"file: {path!r} is not under its module {mid!r}")
        if record.get("language") not in LANGUAGES:
            errors.append(f"file: {path!r} has invalid language")
        if record.get("owner") not in owner_ids:
            errors.append(f"file: {path!r} has unknown owner")
        if record.get("domain") not in domain_ids:
            errors.append(f"file: {path!r} has unknown domain")
        if record.get("owner_source") not in OWNER_SOURCES:
            errors.append(f"file: {path!r} has invalid owner_source")
        state = record.get("retention_state")
        if state is not None and state not in RETENTION_STATES:
            errors.append(f"file: {path!r} has invalid retention state {state!r}")

    module_file_sets: dict[str, set[str]] = {}
    for record in files:
        module_file_sets.setdefault(record.get("module", ""), set()).add(record["path"])
    for module in modules:
        mid = module.get("id", "")
        if set(module.get("files", [])) != module_file_sets.get(mid, set()):
            errors.append(f"module: {mid!r} files list disagrees with file records")

    file_lines: dict[str, list[str]] = {}
    for symbol in symbols:
        path = symbol.get("file", "")
        if path not in file_paths:
            errors.append(f"symbol: {symbol.get('qualified')!r} file {path!r} not indexed")
            continue
        if path not in file_lines:
            try:
                file_lines[path] = (disk_root / path).read_text(encoding="utf-8").splitlines()
            except OSError:
                errors.append(f"symbol: cannot read {path!r}")
                continue
        lines = file_lines[path]
        lineno = symbol.get("line", 0)
        if not isinstance(lineno, int) or lineno < 1 or lineno > len(lines):
            errors.append(f"symbol: {symbol.get('qualified')!r} has broken line {lineno!r}")
        elif symbol.get("name", "") not in lines[lineno - 1]:
            errors.append(f"symbol: {symbol.get('qualified')!r} name missing on {path}:{lineno}")
        if symbol.get("module") != file_module.get(path):
            errors.append(f"symbol: {symbol.get('qualified')!r} module disagrees with its file")
        if symbol.get("language") != file_language.get(path):
            errors.append(f"symbol: {symbol.get('qualified')!r} language disagrees with its file")
        for target in symbol.get("calls", []):
            if target not in symbol_quals:
                errors.append(f"symbol: {symbol.get('qualified')!r} calls unknown {target!r}")
    for symbol in symbols:
        for caller in symbol.get("called_by", []):
            if caller not in symbol_quals:
                errors.append(f"symbol: {symbol.get('qualified')!r} called_by unknown {caller!r}")
    by_qual = {s.get("qualified"): s for s in symbols}
    for symbol in symbols:
        for target in symbol.get("calls", []):
            peer = by_qual.get(target)
            if peer is not None and symbol["qualified"] not in peer.get("called_by", []):
                errors.append(
                    f"symbol: calls/called_by asymmetric {symbol['qualified']!r} -> {target!r}"
                )

    for edge in dependencies:
        if edge.get("from") not in module_ids:
            errors.append(f"dependency: unknown source {edge.get('from')!r}")
        if edge.get("to") not in module_ids:
            errors.append(f"dependency: unknown target {edge.get('to')!r}")
        if edge.get("kind") not in ("declared", "actual"):
            errors.append(f"dependency: invalid kind {edge.get('kind')!r}")
        if not edge.get("sources"):
            errors.append(f"dependency: {edge.get('from')!r}->{edge.get('to')!r} has no sources")

    for entry in drift:
        if entry.get("from") not in module_ids or entry.get("to") not in module_ids:
            errors.append(f"drift: unknown module in {entry!r}")
        if entry.get("kind") not in ("actual-without-declared", "declared-without-actual"):
            errors.append(f"drift: invalid kind {entry.get('kind')!r}")

    for test in tests:
        if not (disk_root / test.get("path", "")).is_file():
            errors.append(f"test: {test.get('path')!r} does not exist on disk")
        if test.get("module") not in module_ids:
            errors.append(f"test: {test.get('path')!r} has unknown module")
        for target in test.get("targets", []):
            if target not in file_paths:
                errors.append(f"test: {test.get('path')!r} targets unknown {target!r}")
        if test.get("granularity") not in ("file", "module"):
            errors.append(f"test: {test.get('path')!r} has invalid granularity")

    for entry in unresolved:
        if not all(k in entry for k in ("kind", "ref", "reason")):
            errors.append(f"unresolved: malformed entry {entry!r}")

    # Canonical-source conflicts: the graph is stale or contradicts authority.
    try:
        if graph.get("inputs_hash") != _expected_inputs_hash():
            errors.append("canonical: inputs_hash mismatch — graph is stale, rebuild it")
    except OSError:
        errors.append("canonical: cannot hash canonical sources")
    if isinstance(policy.get("rules"), list):
        rules = [r for r in policy["rules"] if isinstance(r, dict)]
        for record in files:
            if record.get("is_test") or record.get("owner_source") != "policy":
                continue
            expected = _independent_classify(record["path"], rules)
            if expected != record.get("owner"):
                errors.append(
                    f"canonical: {record['path']!r} owner {record.get('owner')!r} "
                    f"conflicts with policy ({expected!r})"
                )
        for record in files:
            if record.get("is_test") or record.get("owner_source") != "policy":
                continue
            live = retention_files.get(record["path"], {}).get("state")
            if live != record.get("retention_state"):
                errors.append(
                    f"canonical: {record['path']!r} retention "
                    f"{record.get('retention_state')!r} conflicts with "
                    f"language_retention.json ({live!r})"
                )

    counts = graph.get("entity_count"), graph.get("relationship_count")
    if not all(isinstance(count, int) and count > 0 for count in counts):
        errors.append("schema: entity/relationship counts must be positive integers")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Repository Intelligence Graph")
    parser.add_argument("--graph", default=str(repo_graph.GRAPH_PATH), help="graph JSON path")
    args = parser.parse_args(argv)
    try:
        graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"REPO_GRAPH FAIL: cannot load graph: {exc}")
        return 1
    if not isinstance(graph, dict):
        print("REPO_GRAPH FAIL: graph is not an object")
        return 1
    errors = validate_graph(graph)
    if errors:
        print(f"REPO_GRAPH FAIL: {len(errors)} problem(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(
        f"REPO_GRAPH PASS: {graph.get('entity_count')} entities, "
        f"{graph.get('relationship_count')} relationships, "
        f"{len(graph.get('unresolved', []))} unresolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
