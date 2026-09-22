"""Always-warm repository index (Phase 2).

Acceleration layer over the Phase-1 derived graph (`90_brain/repo_graph.json`).
Layers: canonical sources -> derived graph (authority) -> warm lookup index
(speed only) -> index state/metadata. If the warm index ever disagrees with
the graph or canonical sources, the graph/canonical sources win.

Change -> manifest diff -> incremental patch or full rebuild -> validate ->
atomic publish. Failures keep the previous valid index (fail closed: stale
data is never served as fresh).

Usage:
    python scripts/repo_index.py --refresh [--full]
    python scripts/repo_index.py --status
    python scripts/repo_index.py --check
    python scripts/repo_index.py --stats
    python scripts/repo_index.py --query module --name 05_strategy/strategy
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import pickle
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_graph  # noqa: E402
from validate_repo_graph import validate_graph  # noqa: E402

ROOT = SCRIPTS_DIR.parent
INDEX_DIR = ROOT / ".repo_index"
STATE_FILE = INDEX_DIR / "state.json"
MANIFEST_FILE = INDEX_DIR / "manifest.json"
FILEMETA_FILE = INDEX_DIR / "filemeta.json"
LOOKUP_FILE = INDEX_DIR / "lookup.pkl"
LOCK_DIR = INDEX_DIR / "lock"

INDEX_VERSION = 1
PICKLE_PROTOCOL = 4
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 120.0


def _canonical_relpaths() -> tuple[str, ...]:
    """Hashed canonical inputs, resolved from current path constants (sandboxable)."""
    return tuple(
        p.relative_to(repo_graph.ROOT).as_posix()
        for p in (
            repo_graph.POLICY_PATH,
            repo_graph.RETENTION_PATH,
            repo_graph.ROUTES_PATH,
            repo_graph.CONTRACTS_PATH,
            repo_graph.SCRIPTS_DIR / "validate_imports.py",
            repo_graph.SCRIPTS_DIR / "repo_graph.py",
        )
    )


class IndexError(Exception):
    """Refresh/validation failure — previous valid index is preserved."""


class IndexLockedError(Exception):
    """Another update holds the lock (fail closed, nothing was changed)."""


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _rel_posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _read_json(path: Path) -> dict | list | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, (dict, list)) else None


def _write_json_atomic(path: Path, payload: dict | list) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _manifest_paths(modules: list[str]) -> list[str]:
    """Every hashed input: indexed sources + canonical files (sorted)."""
    paths: set[str] = set(_canonical_relpaths())
    for mid in modules:
        root = ROOT / mid
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in repo_graph.INDEX_EXTENSIONS:
                continue
            if repo_graph._is_excluded(path):
                continue
            paths.add(_rel_posix(path))
    return sorted(paths)


def _stat_entry(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _build_manifest(paths: list[str]) -> dict[str, dict]:
    """Full content-hash manifest (slow path: reads every file)."""
    manifest: dict[str, dict] = {}
    for relpath in paths:
        entry = _stat_entry(ROOT / relpath)
        if entry is None:
            continue
        size, mtime_ns = entry
        try:
            sha = _sha_file(ROOT / relpath)
        except OSError:
            continue
        manifest[relpath] = {"sha": sha, "size": size, "mtime_ns": mtime_ns}
    return manifest


def _fast_changed(manifest: dict[str, dict], paths: list[str]) -> list[str]:
    """Stat-only change probe (no content reads): missing/new/size/mtime."""
    changed: list[str] = []
    known = set(manifest)
    for relpath in paths:
        entry = _stat_entry(ROOT / relpath)
        if entry is None:
            if relpath in known:
                changed.append(relpath)
            continue
        old = manifest.get(relpath)
        if old is None or old["size"] != entry[0] or old["mtime_ns"] != entry[1]:
            changed.append(relpath)
    for relpath in known:
        if relpath not in set(paths):
            changed.append(relpath)
    return sorted(set(changed))


def _confirm_changed(
    manifest: dict[str, dict], candidates: list[str]
) -> tuple[list[str], dict[str, dict]]:
    """Re-hash stat-dirty files; mtime-only touches without content change are clean."""
    changed: list[str] = []
    refreshed = dict(manifest)
    for relpath in candidates:
        path = ROOT / relpath
        entry = _stat_entry(path)
        if entry is None:
            if relpath in refreshed:
                del refreshed[relpath]
                changed.append(relpath)
            continue
        try:
            sha = _sha_file(path)
        except OSError:
            if relpath in refreshed:
                del refreshed[relpath]
                changed.append(relpath)
            continue
        old = refreshed.get(relpath)
        if old is None or old["sha"] != sha:
            changed.append(relpath)
        refreshed[relpath] = {"sha": sha, "size": entry[0], "mtime_ns": entry[1]}
    return sorted(set(changed)), refreshed


@contextmanager
def _locked(timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Mutual exclusion via atomic lock-dir creation (stale locks expire)."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            os.mkdir(LOCK_DIR)
            break
        except FileExistsError:
            try:
                age = time.time() - LOCK_DIR.stat().st_mtime
            except OSError:
                age = 0.0
            if age > LOCK_STALE_S:
                shutil.rmtree(LOCK_DIR, ignore_errors=True)
                continue
            if time.monotonic() > deadline:
                raise IndexLockedError(f"lock busy: {LOCK_DIR}") from None
            time.sleep(0.05)
    try:
        (LOCK_DIR / f"pid-{os.getpid()}").write_text(str(time.time()), encoding="utf-8")
        yield
    finally:
        shutil.rmtree(LOCK_DIR, ignore_errors=True)


def _file_imports(relpath: str, text: str, modules: list[str]) -> list[str]:
    """Target modules imported by one file (evidence for actual edges)."""
    found: set[str] = set()
    if relpath.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                target = repo_graph.actual_module_for_dotted(node.module, modules)
                if target:
                    found.add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = repo_graph.actual_module_for_dotted(alias.name, modules)
                    if target:
                        found.add(target)
    elif relpath.endswith(".rs"):
        for line in text.splitlines():
            match = repo_graph.RS_USE_RE.match(line)
            if match:
                target = f"rust/{match.group(1).replace('_', '-')}"
                if target in modules:
                    found.add(target)
    return sorted(found)


def build_filemeta(manifest: dict[str, dict], modules: list[str]) -> dict[str, dict]:
    """Per-file derived cache powering incremental invalidation (no graph needed)."""
    meta: dict[str, dict] = {}
    for relpath in sorted(manifest):
        if relpath in _canonical_relpaths():
            continue
        module = repo_graph.owning_module(relpath, modules)
        if module is None:
            continue
        try:
            text = (ROOT / relpath).read_text(encoding="utf-8")
        except OSError:
            continue
        dotted: list[str] = []
        alias_targets: list[str] = []
        if relpath.endswith(".py"):
            try:
                _symbols, _local, alias_data = repo_graph.parse_python_symbols(relpath, text, [])
            except Exception:  # noqa: BLE001 - cache must never fail a refresh probe
                continue
            dotted = sorted({s["dotted"] for s in _symbols})
            alias_targets = sorted(set(alias_data["aliases"].values()))
        meta[relpath] = {
            "module": module,
            "is_test": repo_graph.is_test_file(relpath),
            "dotted": dotted,
            "alias_targets": alias_targets,
            "imports": _file_imports(relpath, text, modules),
        }
    return meta


def build_maps(graph: dict) -> dict:
    """O(1) secondary indexes with the exact query shapes of run_query."""
    by_qual: dict[str, dict] = {}
    by_name: dict[str, list[str]] = {}
    by_file: dict[str, list[str]] = {}
    by_module: dict[str, list[str]] = {}
    for symbol in graph.get("symbols", []):
        qual = symbol["qualified"]
        by_qual[qual] = symbol
        by_name.setdefault(symbol["name"], []).append(qual)
        by_file.setdefault(symbol["file"], []).append(qual)
        by_module.setdefault(symbol["module"], []).append(qual)
    for quals in list(by_name.values()) + list(by_file.values()) + list(by_module.values()):
        quals.sort()
    tests_by_module: dict[str, list[str]] = {}
    for test in graph.get("tests", []):
        tests_by_module.setdefault(test["module"], []).append(test["path"])
    for paths in tests_by_module.values():
        paths.sort()
    return {
        "by_qual": by_qual,
        "by_name": by_name,
        "by_file": by_file,
        "by_module": by_module,
        "tests_by_module": tests_by_module,
    }


def query_with_maps(payload: dict, kind: str, name: str) -> dict | list | None:
    """Map-accelerated lookups; output shapes identical to repo_graph.run_query."""
    graph = payload["graph"]
    maps = payload["maps"]
    if kind == "symbol":
        by_qual = maps["by_qual"]
        if name in by_qual:
            return [by_qual[name]]
        return [by_qual[q] for q in maps["by_name"].get(name, [])]
    if kind == "tests":
        module = repo_graph.query_module(graph, name)
        if module is None:
            hits = query_with_maps(payload, "symbol", name)
            assert isinstance(hits, list)
            if hits:
                direct = sorted({t for s in hits for t in s["tests"]})
                inherited = sorted(
                    {
                        t["path"]
                        for s in hits
                        for t in graph.get("tests", [])
                        if t["module"] == s["module"]
                    }
                )
                return {"symbol": name, "tests": direct, "module_tests": inherited}
            record = repo_graph.query_file(graph, name)
            if record is None:
                return None
            hits = [t for t in graph.get("tests", []) if t["path"] == name or name in t["targets"]]
            return {"file": name, "tests": hits}
        return {
            "module": name,
            "tests": [t for t in graph.get("tests", []) if t["module"] == name],
        }
    return repo_graph.run_query(graph, kind, name)


def _expected_roots(modules: list[str]) -> list[str]:
    """Module roots (submodules excluded) as discovered from the tree."""
    return sorted(
        m
        for m in modules
        if repo_graph.owning_parent_module(m, [x for x in modules if x != m]) is None
    )


def _payload_has_sets(node: object) -> bool:
    if isinstance(node, (set, frozenset)):
        return True
    if isinstance(node, dict):
        return any(_payload_has_sets(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return any(_payload_has_sets(v) for v in node)
    return False


def _save_lookup(payload: dict) -> None:
    if _payload_has_sets(payload):
        raise IndexError("lookup payload must not contain sets (determinism)")
    tmp = LOOKUP_FILE.with_name(f"{LOOKUP_FILE.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as handle:
        pickle.dump(payload, handle, protocol=PICKLE_PROTOCOL)
    os.replace(tmp, LOOKUP_FILE)


def _load_lookup() -> dict | None:
    try:
        with open(LOOKUP_FILE, "rb") as handle:
            payload = pickle.load(handle)  # noqa: S301 - own regenerable cache file
    except (OSError, ValueError, EOFError, pickle.UnpicklingError):
        return None
    if not isinstance(payload, dict) or "graph" not in payload or "maps" not in payload:
        return None
    return payload


def _state_fingerprint(state: dict) -> str:
    core = {k: v for k, v in state.items() if k not in ("status", "fingerprint")}
    return _sha_bytes(json.dumps(core, sort_keys=True).encode("utf-8"))


def _stored_fingerprint(state: dict | list | None) -> str | None:
    """Fingerprint recorded in state (None when state is missing/corrupt)."""
    return state.get("fingerprint") if isinstance(state, dict) else None


def _publish(graph: dict, manifest: dict, filemeta: dict, mode: str) -> dict:
    """Validate then atomically publish graph + warm index. Raises IndexError."""
    errors = validate_graph(graph, ROOT)
    if errors:
        raise IndexError(f"patched graph invalid: {errors[0]} ({len(errors)} total)")
    maps = build_maps(graph)
    graph_text = json.dumps(graph, indent=2, sort_keys=True) + "\n"
    graph_tmp = repo_graph.GRAPH_PATH.with_name(f"{repo_graph.GRAPH_PATH.name}.tmp-{os.getpid()}")
    graph_tmp.write_text(graph_text, encoding="utf-8", newline="\n")
    manifest_tmp = MANIFEST_FILE.with_name(f"{MANIFEST_FILE.name}.tmp-{os.getpid()}")
    filemeta_tmp = FILEMETA_FILE.with_name(f"{FILEMETA_FILE.name}.tmp-{os.getpid()}")
    manifest_tmp.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    filemeta_tmp.write_text(
        json.dumps(filemeta, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    _save_lookup({"graph": graph, "maps": maps})
    state = {
        "index_version": INDEX_VERSION,
        "schema_version": repo_graph.SCHEMA_VERSION,
        "generator_sha": _sha_file(SCRIPTS_DIR / "repo_graph.py"),
        "inputs_hash": graph["inputs_hash"],
        "manifest_sha": _sha_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8")),
        "graph_sha": _sha_bytes(graph_text.encode("utf-8")),
        "mode": mode,
        "status": "READY",
    }
    state["fingerprint"] = _state_fingerprint(state)
    os.replace(graph_tmp, repo_graph.GRAPH_PATH)
    os.replace(manifest_tmp, MANIFEST_FILE)
    os.replace(filemeta_tmp, FILEMETA_FILE)
    _write_json_atomic(STATE_FILE, state)
    # Post-publish verification: state attests exactly what is on disk.
    reread = _read_json(STATE_FILE)
    if not isinstance(reread, dict) or _state_fingerprint(reread) != state["fingerprint"]:
        raise IndexError("post-publish state verification failed")
    return state


def _full_rebuild(modules_hint: list[str]) -> tuple[dict, dict, dict, str]:
    graph, _timing = repo_graph.build_graph()
    modules = [m["id"] for m in graph["modules"]]
    _ = modules_hint
    manifest = _build_manifest(_manifest_paths(modules))
    filemeta = build_filemeta(manifest, modules)
    return graph, manifest, filemeta, "full"


def _patch_incremental(
    graph: dict,
    manifest: dict[str, dict],
    filemeta: dict[str, dict],
    changed: list[str],
) -> tuple[dict, dict, dict, str]:
    """Surgical patch for source-only changes under known modules (else FULL)."""
    modules = [m["id"] for m in graph["modules"]]
    if repo_graph.discover_module_roots() != _expected_roots(modules):
        raise _NeedsFullError("module roots changed")
    for relpath in changed:
        if relpath in _canonical_relpaths():
            raise _NeedsFullError(f"canonical input changed: {relpath}")
        if repo_graph.owning_module(relpath, modules) is None and (ROOT / relpath).exists():
            raise _NeedsFullError(f"path outside known modules: {relpath}")

    policy = json.loads(repo_graph.POLICY_PATH.read_text(encoding="utf-8"))
    rules = [r for r in policy.get("rules", []) if isinstance(r, dict)]
    retention_files = json.loads(repo_graph.RETENTION_PATH.read_text(encoding="utf-8")).get(
        "files", {}
    )
    mod_info = {m["id"]: m for m in graph["modules"]}

    # `changed` mixes added/modified/deleted; disk state separates them
    # (the manifest passed in is already pruned, so membership proves nothing).
    deleted = sorted(p for p in changed if not (ROOT / p).is_file())
    touched = sorted(p for p in changed if (ROOT / p).is_file())
    changed_set = set(changed)

    before_dotted: set[str] = set()
    for relpath in touched + deleted:
        before_dotted.update(filemeta.get(relpath, {}).get("dotted", []))

    notes: list[dict] = [n for n in graph.get("unresolved", []) if n.get("ref") not in changed_set]

    def note(kind: str, ref: str, reason: str) -> None:
        if len(notes) < repo_graph.UNRESOLVED_CAP:
            notes.append({"kind": kind, "ref": ref, "reason": reason})

    # Files: drop deleted/touched, re-add touched.
    files = [f for f in graph["files"] if f["path"] not in changed_set]
    for relpath in touched:
        mid = repo_graph.owning_module(relpath, modules)
        assert mid is not None
        info = mod_info[mid]
        is_test = repo_graph.is_test_file(relpath)
        record: dict = {"path": relpath, "module": mid, "is_test": is_test}
        if is_test:
            record.update(
                {
                    "owner": info["owner"],
                    "domain": info["domain"],
                    "language": info["language"],
                    "owner_source": info["owner_source"],
                    "retention_state": None,
                }
            )
        else:
            rule_id, domain, language = repo_graph.classify(relpath, rules)
            if rule_id is not None:
                record.update(
                    {
                        "owner": rule_id,
                        "domain": domain,
                        "language": language or "UNKNOWN",
                        "owner_source": "policy",
                    }
                )
            elif info["owner_source"] != "unknown":
                record.update(
                    {
                        "owner": info["owner"],
                        "domain": info["domain"],
                        "language": info["language"],
                        "owner_source": info["owner_source"],
                    }
                )
            else:
                note("owner-unknown", relpath, "no policy rule and module owner unknown")
                record.update(
                    {
                        "owner": "UNKNOWN",
                        "domain": "UNKNOWN",
                        "language": "UNKNOWN",
                        "owner_source": "unknown",
                    }
                )
            record["retention_state"] = retention_files.get(relpath, {}).get("state")
        files.append(record)
    files.sort(key=lambda f: f["path"])
    known_files = {f["path"] for f in files}

    # Symbols: drop quals of changed files, parse touched files.
    dropped_quals = {s["qualified"] for s in graph["symbols"] if s["file"] in changed_set}
    symbols = [s for s in graph["symbols"] if s["qualified"] not in dropped_quals]
    fresh_alias: dict[str, dict] = {}
    fresh_local: dict[str, dict[str, str]] = {}
    after_dotted: set[str] = set(before_dotted)
    for relpath in touched:
        try:
            text = (ROOT / relpath).read_text(encoding="utf-8")
        except OSError:
            note("file-unreadable", relpath, "cannot read file")
            continue
        record = next(f for f in files if f["path"] == relpath)
        if relpath.endswith(".py"):
            found, local, alias_data = repo_graph.parse_python_symbols(relpath, text, notes)
            fresh_alias[relpath] = alias_data
            fresh_local[relpath] = local
        elif relpath.endswith(".rs"):
            found = repo_graph.parse_rust_symbols(relpath, text.splitlines())
        else:
            found = repo_graph.parse_slint_symbols(relpath, text.splitlines())
        for symbol in found:
            symbol["module"] = record["module"]
            symbol["language"] = record["language"]
            symbol["calls"] = []
            symbol["called_by"] = []
            symbol["callers_complete"] = relpath.endswith(".py")
            symbol["tests"] = []
            symbols.append(symbol)
            after_dotted.add(symbol["dotted"])
    symbols.sort(key=lambda s: s["qualified"])

    # filemeta refresh for touched/deleted.
    filemeta = dict(filemeta)
    for relpath in deleted:
        filemeta.pop(relpath, None)
    for relpath in touched:
        record = next(f for f in files if f["path"] == relpath)
        try:
            text = (ROOT / relpath).read_text(encoding="utf-8")
        except OSError:
            filemeta.pop(relpath, None)
            continue
        dotted = sorted({s["dotted"] for s in symbols if s["file"] == relpath})
        alias_targets: list[str] = []
        if relpath in fresh_alias:
            alias_targets = sorted(set(fresh_alias[relpath]["aliases"].values()))
        elif relpath.endswith(".py"):
            try:
                _s, _l, alias_data = repo_graph.parse_python_symbols(relpath, text, [])
                alias_targets = sorted(set(alias_data["aliases"].values()))
            except Exception:  # noqa: BLE001 - stale cache entry is safer than a crash
                alias_targets = filemeta.get(relpath, {}).get("alias_targets", [])
        filemeta[relpath] = {
            "module": record["module"],
            "is_test": record["is_test"],
            "dotted": dotted,
            "alias_targets": alias_targets,
            "imports": _file_imports(relpath, text, modules),
        }

    # Calls: recompute touched py files + reverse-affected callers.
    by_qual = {s["qualified"]: s for s in symbols}
    by_dotted = {s["dotted"]: s["qualified"] for s in symbols}
    recompute: set[str] = set()
    for relpath in touched:
        if relpath.endswith(".py"):
            recompute.add(relpath)
    for relpath, meta in filemeta.items():
        if relpath in recompute or not relpath.endswith(".py"):
            continue
        if set(meta.get("alias_targets", [])) & after_dotted:
            recompute.add(relpath)
    # Parse alias data for reverse-affected files (touched already have it).
    for relpath in sorted(recompute):
        if relpath in fresh_alias:
            continue
        try:
            text = (ROOT / relpath).read_text(encoding="utf-8")
            _s, local, alias_data = repo_graph.parse_python_symbols(relpath, text, notes)
        except OSError:
            continue
        fresh_alias[relpath] = alias_data
        fresh_local[relpath] = local
    for relpath in sorted(recompute):
        for caller, targets in repo_graph.resolve_file_calls(
            by_dotted, fresh_alias[relpath], fresh_local[relpath]
        ).items():
            if caller in by_qual:
                by_qual[caller]["calls"] = targets
    repo_graph.rebuild_called_by(symbols)

    # Tests for affected modules.
    affected = sorted(
        {
            mid
            for p in touched + deleted
            for mid in [repo_graph.owning_module(p, modules)]
            if mid is not None
        }
    )
    tests = [t for t in graph["tests"] if t["module"] not in set(affected)]
    for record in sorted(files, key=lambda f: f["path"]):
        if record["module"] not in set(affected) or not record["is_test"]:
            continue
        targets, granularity = repo_graph.test_targets(
            record["path"], record["module"], known_files
        )
        tests.append(
            {
                "path": record["path"],
                "module": record["module"],
                "targets": targets,
                "granularity": granularity,
            }
        )
    tests.sort(key=lambda t: t["path"])
    file_test_map: dict[str, list[str]] = {}
    for test in tests:
        for target in test["targets"]:
            file_test_map.setdefault(target, []).append(test["path"])
    affected_set = set(affected)
    for symbol in symbols:
        if symbol["module"] in affected_set:
            symbol["tests"] = sorted(file_test_map.get(symbol["file"], []))

    # Actual edges for affected modules (declared unchanged: no canonical change).
    actual: dict[str, set[str]] = {}
    evidence: dict[tuple[str, str], set[str]] = {}
    for relpath, meta in filemeta.items():
        if meta.get("is_test"):
            continue
        source = meta["module"]
        if source not in affected_set:
            continue
        for target in meta.get("imports", []):
            if target != source and target in modules:
                actual.setdefault(source, set()).add(target)
                evidence.setdefault((source, target), set()).add(relpath)
    dependencies = [
        e
        for e in graph["dependencies"]
        if not (e["from"] in affected_set and e["kind"] == "actual")
    ]
    for module in mod_info.values():
        if module["id"] not in affected_set:
            continue
        for dep in sorted(actual.get(module["id"], set())):
            dependencies.append(
                {
                    "from": module["id"],
                    "to": dep,
                    "kind": "actual",
                    "sources": sorted(evidence.get((module["id"], dep), [])),
                }
            )
    dependencies.sort(key=lambda d: (d["from"], d["to"], d["kind"]))

    depended_on_by: dict[str, set[str]] = {mid: set() for mid in modules}
    for edge in dependencies:
        if edge["to"] in depended_on_by:
            depended_on_by[edge["to"]].add(edge["from"])
    drift: list[dict] = []
    for module in [mod_info[mid] for mid in modules]:
        actual_set = (
            actual.get(module["id"], set())
            if module["id"] in affected_set
            else set(module["depends_on_actual"])
        )
        for dep in sorted(actual_set - set(module["depends_on_declared"])):
            drift.append({"from": module["id"], "to": dep, "kind": "actual-without-declared"})
        for dep in sorted(set(module["depends_on_declared"]) - actual_set):
            drift.append({"from": module["id"], "to": dep, "kind": "declared-without-actual"})
    drift.sort(key=lambda d: (d["from"], d["to"], d["kind"]))

    module_records = []
    for module in [mod_info[mid] for mid in modules]:
        record = dict(module)
        if module["id"] in affected_set:
            record["files"] = sorted(f["path"] for f in files if f["module"] == module["id"])
            record["depends_on_actual"] = sorted(actual.get(module["id"], set()))
        record["depended_on_by"] = sorted(depended_on_by[module["id"]])
        module_records.append(record)

    validators = sorted(
        (
            {"path": p.relative_to(ROOT).as_posix(), "scope": "repo"}
            for p in SCRIPTS_DIR.glob("validate_*.py")
        ),
        key=lambda v: v["path"],
    )

    entity_count, relationship_count = repo_graph.graph_counts(
        len(graph["domains"]),
        len(graph["owners"]),
        len(graph["languages"]),
        module_records,
        files,
        symbols,
        tests,
        validators,
    )
    patched = {
        "schema_version": graph["schema_version"],
        "repository": graph["repository"],
        "inputs_hash": graph["inputs_hash"],
        "entity_count": entity_count,
        "relationship_count": relationship_count,
        "domains": graph["domains"],
        "owners": graph["owners"],
        "languages": graph["languages"],
        "modules": sorted(module_records, key=lambda m: m["id"]),
        "files": files,
        "symbols": symbols,
        "dependencies": dependencies,
        "drift": drift,
        "tests": tests,
        "validators": validators,
        "unresolved": sorted(notes, key=lambda u: (u["kind"], u["ref"])),
    }
    return patched, manifest, filemeta, "incremental"


class _NeedsFullError(Exception):
    """Incremental case not proven safe — caller falls back to full rebuild."""


def refresh(*, full: bool = False) -> dict:
    """Detect changes and publish an updated index (atomic, locked)."""
    with _locked():
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        state = _read_json(STATE_FILE)
        manifest = _read_json(MANIFEST_FILE)
        filemeta = _read_json(FILEMETA_FILE)
        have_index = (
            isinstance(state, dict)
            and isinstance(manifest, dict)
            and isinstance(filemeta, dict)
            and LOOKUP_FILE.is_file()
        )
        graph = None
        if repo_graph.GRAPH_PATH.is_file():
            try:
                graph = repo_graph.load_graph()
            except (OSError, ValueError, repo_graph.GraphError):
                graph = None
        if not full and have_index and graph is not None:
            assert isinstance(manifest, dict) and isinstance(filemeta, dict)
            modules = [m["id"] for m in graph["modules"]]
            paths = _manifest_paths(modules)
            if repo_graph.discover_module_roots() != _expected_roots(modules):
                full = True
            else:
                candidates = _fast_changed(manifest, paths)
                if not candidates:
                    payload = _load_lookup()
                    if payload is not None:
                        return {
                            "status": "READY",
                            "mode": "noop",
                            "fingerprint": _stored_fingerprint(state),
                        }
                    graph, manifest, filemeta, mode = _full_rebuild(modules)
                    return _publish(graph, manifest, filemeta, "recover")
                changed, refreshed_manifest = _confirm_changed(manifest, candidates)
                if not changed:
                    _write_json_atomic(MANIFEST_FILE, refreshed_manifest)
                    return {
                        "status": "READY",
                        "mode": "stat-only",
                        "fingerprint": _stored_fingerprint(state),
                    }
                try:
                    graph, manifest, filemeta, mode = _patch_incremental(
                        graph, refreshed_manifest, filemeta, changed
                    )
                except _NeedsFullError as needs:
                    graph, manifest, filemeta, mode = _full_rebuild(modules)
                    mode = f"full ({needs})"
                return _publish(graph, manifest, filemeta, mode)
        modules_hint = [m["id"] for m in graph["modules"]] if graph else []
        graph, manifest, filemeta, mode = _full_rebuild(modules_hint)
        return _publish(graph, manifest, filemeta, mode if full else "initial")


def freshness() -> dict:
    """Read-only freshness probe (never mutates): READY/STALE/REBUILD_REQUIRED/INVALID."""
    state = _read_json(STATE_FILE)
    manifest = _read_json(MANIFEST_FILE)
    if (
        not isinstance(state, dict)
        or not isinstance(manifest, dict)
        or not LOOKUP_FILE.is_file()
        or not repo_graph.GRAPH_PATH.is_file()
    ):
        return {"status": "INVALID", "reason": "index files missing"}
    if _state_fingerprint(state) != state.get("fingerprint"):
        return {"status": "INVALID", "reason": "state fingerprint mismatch"}
    try:
        graph = repo_graph.load_graph()
    except (OSError, ValueError, repo_graph.GraphError):
        return {"status": "INVALID", "reason": "graph unreadable"}
    if graph.get("inputs_hash") != state.get("inputs_hash"):
        return {"status": "REBUILD_REQUIRED", "reason": "graph/inputs_hash drift"}
    modules = [m["id"] for m in graph.get("modules", [])]
    if repo_graph.discover_module_roots() != _expected_roots(modules):
        return {"status": "REBUILD_REQUIRED", "reason": "module roots changed"}
    candidates = _fast_changed(manifest, _manifest_paths(modules))
    if candidates:
        canonical_hit = [c for c in candidates if c in _canonical_relpaths()]
        if canonical_hit:
            return {
                "status": "REBUILD_REQUIRED",
                "reason": f"canonical inputs changed: {canonical_hit[:3]}",
            }
        return {"status": "STALE", "reason": f"{len(candidates)} source file(s) changed"}
    payload = _load_lookup()
    if payload is None:
        return {"status": "INVALID", "reason": "warm lookup payload corrupt"}
    try:
        graph_sha = _sha_file(repo_graph.GRAPH_PATH)
    except OSError:
        return {"status": "INVALID", "reason": "graph unreadable"}
    if graph_sha != state.get("graph_sha"):
        return {"status": "INVALID", "reason": "graph file changed under index"}
    return {"status": "READY", "fingerprint": state.get("fingerprint")}


def ensure_fresh() -> dict:
    """Serve path: verify freshness, refresh when permitted, else fail closed."""
    probe = freshness()
    if probe["status"] == "READY":
        payload = _load_lookup()
        if payload is not None:
            return payload
    result = refresh()
    payload = _load_lookup()
    if payload is None:
        raise IndexError(f"refresh {result} produced no warm payload")
    _ = result
    return payload


def self_check() -> list[str]:
    """Consistency of graph + warm payload + state (read-only)."""
    errors: list[str] = []
    probe = freshness()
    if probe["status"] != "READY":
        # Never validate stale data as if it were fresh: report, don't serve.
        return [f"stale: {probe}"]
    payload = _load_lookup()
    if payload is None:
        return ["payload corrupt"]
    errors.extend(f"graph: {e}" for e in validate_graph(payload["graph"], ROOT))
    try:
        disk_graph = repo_graph.load_graph()
    except (OSError, ValueError, repo_graph.GraphError) as exc:
        return [f"graph unreadable: {exc}"]
    if _sha_bytes(json.dumps(payload["graph"], sort_keys=True).encode()) != _sha_bytes(
        json.dumps(disk_graph, sort_keys=True).encode()
    ):
        errors.append("payload graph differs from 90_brain/repo_graph.json")
    if _payload_has_sets(payload):
        errors.append("payload contains sets (nondeterministic pickle)")
    for kind, name in (
        ("module", "09_broker/broker"),
        ("symbol", "BrokerRegistry"),
        ("deps", "05_strategy/strategy"),
        ("tests", "09_broker/broker"),
    ):
        if query_with_maps(payload, kind, name) != repo_graph.run_query(
            payload["graph"], kind, name
        ):
            errors.append(f"map query diverges for {kind}:{name}")
    return errors


def benchmark() -> dict:
    """Phase-1 vs Phase-2 timings (actual measurements, no claims)."""
    out: dict = {}
    started = time.perf_counter()
    graph = repo_graph.load_graph()
    out["phase1_cold_load_ms"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    payload = _load_lookup()
    out["phase2_warm_load_ms"] = (
        round((time.perf_counter() - started) * 1000, 2) if payload else None
    )
    queries = [
        ("module", "05_strategy/strategy"),
        ("file", "09_broker/broker/registry.py"),
        ("symbol", "BrokerRegistry"),
        ("owner", "PYTHON_STRATEGY"),
        ("deps", "08_execution/execution"),
        ("dependents", "01_core/core"),
        ("tests", "09_broker/broker"),
        ("contract", "05_strategy/strategy"),
    ]
    phase1: dict[str, float] = {}
    phase2: dict[str, float] = {}
    for kind, name in queries:
        phase1[kind] = round(repo_graph.lookup_timed(graph, kind, name) / 1000, 4)
        if payload:
            started = time.perf_counter()
            for _ in range(1000):
                query_with_maps(payload, kind, name)
            phase2[kind] = round((time.perf_counter() - started), 4)
    out["phase1_warm_lookup_ms_per_1000"] = phase1
    out["phase2_warm_lookup_ms_per_1000"] = phase2
    started = time.perf_counter()
    repo_graph.build_graph()
    out["phase1_full_rebuild_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Always-warm repository index")
    parser.add_argument(
        "--refresh", action="store_true", help="incremental refresh (or full when unsafe)"
    )
    parser.add_argument("--full", action="store_true", help="force full rebuild with --refresh")
    parser.add_argument("--status", action="store_true", help="read-only freshness probe")
    parser.add_argument("--check", action="store_true", help="self-validation (read-only)")
    parser.add_argument("--stats", action="store_true", help="phase-1 vs phase-2 benchmark")
    parser.add_argument("--query", choices=repo_graph.QUERIES, help="lookup kind (freshness first)")
    parser.add_argument("--name", default="", help="module/file/symbol/owner name")
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps(freshness(), indent=2, sort_keys=True))
        return 0
    if args.check:
        errors = self_check()
        if errors:
            print(f"REPO_INDEX FAIL: {len(errors)} problem(s)")
            for error in errors:
                print(f"  - {error}")
            return 1
        print("REPO_INDEX PASS: warm payload agrees with graph and canonical state")
        return 0
    if args.stats:
        try:
            print(json.dumps(benchmark(), indent=2, sort_keys=True))
        except (OSError, ValueError, IndexError, repo_graph.GraphError) as exc:
            print(f"REPO_INDEX FAIL: {exc}")
            return 1
        return 0
    if args.refresh:
        try:
            result = refresh(full=args.full)
        except (IndexError, IndexLockedError, repo_graph.GraphError) as exc:
            print(f"REPO_INDEX FAIL: {exc}")
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.query:
        if not args.name:
            print("REPO_INDEX FAIL: --name is required with --query", file=sys.stderr)
            return 1
        try:
            payload = ensure_fresh()
            result = query_with_maps(payload, args.query, args.name)
        except (
            IndexError,
            IndexLockedError,
            OSError,
            ValueError,
            repo_graph.GraphError,
        ) as exc:
            print(f"REPO_INDEX FAIL: {exc}")
            return 1
        if result is None or result == []:
            print(json.dumps({"status": "UNKNOWN", "query": args.query, "name": args.name}))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
