"""Incremental + cached validation tests (Phase 6 matrix §25 + benchmarks §23).

Unit tests use synthetic change dicts (fast, no tree writes). Benchmark tests
mutate the live tree through TreeEdit (backup/restore/hash-verified) and
restore everything even on failure.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VAYREN_SCOPE_BENCH_NESTED") == "1",
    reason="nested benchmark run (outer full-validation in progress)",
)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import execute_surface  # noqa: E402
import repo_graph  # noqa: E402
import repo_index  # noqa: E402
import validate_scope  # noqa: E402
from validate_scope import (  # noqa: E402
    analyze_impact,
    build_cache_key,
    cache_lookup,
    compute_scope,
    detect_changes,
    run_cached,
)

ROOT = SCRIPTS_DIR.parent
BROKER_TASK = "add validation to broker registry"

# FULL for incremental≡full comparison: L5 minus cargo (Python-only changes
# cannot affect Rust; cargo equivalence explicitly unmeasured — see report).
FULL_PY = [
    "ruff check .",
    "ruff format --check .",
    "pyright",
    "pytest 02_data/data/tests -q",
    "pytest 09_broker/broker/tests -q",
    "pytest scripts/forensics/tests -q",
    "pytest scripts/tests -q",
    "python scripts/validate_structure.py",
    "python scripts/validate_imports.py",
    "python scripts/validate_language_ownership.py",
    "python scripts/validate_architecture_gate.py",
    "python scripts/validate_authority.py",
    "python scripts/validate_routes.py",
    "python scripts/context_engine.py --check",
]


class TreeEdit:
    """Live-tree mutations with guaranteed restore + hash verification."""

    def __init__(self) -> None:
        self.originals: dict[str, bytes] = {}
        self.created: list[str] = []

    def modify(self, relpath: str, new_text: str) -> None:
        path = ROOT / relpath
        if relpath not in self.originals:
            self.originals[relpath] = path.read_bytes()
        ending = "\r\n" if b"\r\n" in self.originals[relpath] else "\n"
        path.write_text(new_text.replace("\n", ending), encoding="utf-8", newline="")

    def append(self, relpath: str, extra: str) -> None:
        path = ROOT / relpath
        if relpath not in self.originals:
            self.originals[relpath] = path.read_bytes()
        ending = "\r\n" if b"\r\n" in self.originals[relpath] else "\n"
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(extra.replace("\n", ending))

    def create(self, relpath: str, text: str) -> None:
        path = ROOT / relpath
        assert not path.exists(), relpath
        self.created.append(relpath)
        path.write_text(text, encoding="utf-8", newline="\n")

    def restore(self) -> None:
        for relpath in self.created:
            (ROOT / relpath).unlink(missing_ok=True)
        self.created.clear()
        for relpath, content in self.originals.items():
            (ROOT / relpath).write_bytes(content)
        for relpath, content in self.originals.items():
            assert (ROOT / relpath).read_bytes() == content, relpath
        self.originals.clear()


@pytest.fixture()
def tree() -> Iterator[TreeEdit]:
    edit = TreeEdit()
    yield edit
    edit.restore()


@pytest.fixture()
def graph() -> dict:
    return repo_index.ensure_fresh()["graph"]


def _manifest(task: str = BROKER_TASK) -> dict:
    manifest, _ = execute_surface.build_manifest(task)
    assert manifest["status"] == "READY"
    return manifest


def _change(files: list[str], **overrides: object) -> dict:
    base: dict = {
        "changed": sorted(files),
        "deleted": [],
        "added": [],
        "unexpected": [],
        "expected": sorted(files),
        "impact": {
            "modules": [],
            "symbols": [],
            "public_changed": [],
            "dep_changed": [],
            "callers": [],
            "caller_modules": [],
            "dependents": [],
            "affected_modules": [],
            "canonical": [],
            "languages": ["PYTHON"],
            "incomplete": [],
        },
    }
    base.update(overrides)
    return base


# ── Scope mapping units ──


def test_private_change_targeted(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["modules"] = ["09_broker/broker"]
    impact["affected_modules"] = ["09_broker/broker"]
    change = _change(["09_broker/broker/registry.py"], impact=impact)
    scope = compute_scope(change, graph)
    assert scope["level"] == "L2"
    assert any(c.startswith("ruff check 09_broker") for c in scope["commands"])
    assert any("broker" in c for c in scope["commands"])


def test_public_symbol_change(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["public_changed"] = ["broker.registry:BrokerRegistry"]
    impact["modules"] = ["09_broker/broker"]
    impact["affected_modules"] = ["09_broker/broker"]
    change = _change(["09_broker/broker/registry.py"], impact=impact)
    scope = compute_scope(change, graph)
    assert "L2" in scope["levels"]
    assert any("broker" in c for c in scope["commands"])


def test_module_change_multi(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["modules"] = ["09_broker/broker", "02_data/data"]
    change = _change(["09_broker/broker/registry.py", "02_data/data/settings.py"], impact=impact)
    scope = compute_scope(change, graph)
    assert scope["level"] == "L4"
    assert "multi-module" in "; ".join(scope["reasons"])


def test_dependency_change(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["dep_changed"] = [{"module": "05_strategy/strategy"}]
    change = _change(["05_strategy/strategy/runtime.py"], impact=impact)
    scope = compute_scope(change, graph)
    assert "L3" in scope["levels"]
    assert "python scripts/validate_imports.py" in scope["commands"]


def test_contract_change(graph: dict) -> None:
    change = _change(["90_brain/module_contracts.md"])
    scope = compute_scope(change, graph)
    assert scope["level"] == "L3"
    assert "python scripts/validate_routes.py" in scope["commands"]


def test_ownership_change(graph: dict) -> None:
    change = _change(["90_brain/ownership_policy.json"])
    scope = compute_scope(change, graph)
    assert "L4" in scope["levels"]
    assert "python scripts/validate_language_ownership.py" in scope["commands"]


def test_language_change(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["languages"] = ["PYTHON", "RUST"]
    change = _change(["03_market/market/models/bar.py"], impact=impact)
    scope = compute_scope(change, graph)
    assert "L4" in scope["levels"]
    assert "cross-language" in "; ".join(scope["reasons"])


def test_cross_module_change(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["modules"] = ["09_broker/broker", "02_data/data"]
    impact["affected_modules"] = ["09_broker/broker", "02_data/data"]
    change = _change(["09_broker/broker/registry.py"], impact=impact)
    scope = compute_scope(change, graph)
    assert scope["level"] in ("L2", "L4")


def test_cross_language_change(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["languages"] = ["PYTHON", "RUST"]
    impact["modules"] = ["03_market/market", "rust/vayren-core"]
    impact["affected_modules"] = ["03_market/market", "rust/vayren-core"]
    change = _change(
        ["03_market/market/models/bar.py", "rust/vayren-core/src/market.rs"], impact=impact
    )
    scope = compute_scope(change, graph)
    assert "L4" in scope["levels"]
    assert "python scripts/validate_architecture_gate.py" in scope["commands"]


def test_unexpected_file(graph: dict) -> None:
    change = _change(["09_broker/broker/registry.py"], unexpected=["09_broker/broker/surprise.py"])
    scope = compute_scope(change, graph)
    assert scope["level"] == "L5"


def test_tooling_change_escalates(graph: dict) -> None:
    change = _change(["scripts/repo_graph.py"])
    scope = compute_scope(change, graph)
    assert scope["level"] == "L5"


def test_escalation_recorded(graph: dict) -> None:
    impact = _change([])["impact"]
    impact["incomplete"] = ["rust/vayren-core/src/market.rs"]
    change = _change(["rust/vayren-core/src/market.rs"], impact=impact)
    scope = compute_scope(change, graph)
    assert scope["escalations"]
    assert "L2" in scope["levels"]


def test_deterministic_scope(graph: dict) -> None:
    change = _change(["09_broker/broker/registry.py"])
    first = compute_scope(change, graph)
    second = compute_scope(change, graph)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_commands_exist_and_known() -> None:
    manifest = _manifest()
    graph = repo_index.ensure_fresh()["graph"]
    change = detect_changes(manifest)
    impact = analyze_impact(change, manifest, graph, None)
    change["impact"] = impact
    scope = compute_scope(change, graph)
    assert scope["clean"] is True


# ── Cache units ──


def _scope_stub(level: str = "L1") -> dict:
    return {"level": level, "levels": [level], "commands": []}


def test_cache_hit(graph: dict) -> None:
    scope = _scope_stub("L1")
    first = run_cached("python scripts/validate_routes.py", scope, graph)
    assert first["outcome"] == "PASS"
    second = run_cached("python scripts/validate_routes.py", scope, graph)
    assert second["cache"] == "hit"
    assert second["outcome"] == "PASS"
    assert second["key"] == first["key"]


def test_cache_miss_new_command(graph: dict, tree: TreeEdit) -> None:
    scope = _scope_stub("L0")
    tree.append("09_broker/broker/faces.py", _probe_comment())
    result = run_cached("ruff check 09_broker/broker/faces.py", scope, graph)
    assert result["cache"] == "miss"
    assert result["outcome"] == "PASS"


def test_cache_invalidation_on_source(graph: dict, tree: TreeEdit) -> None:
    scope = _scope_stub("L1")
    command = "python scripts/validate_routes.py"
    assert run_cached(command, scope, graph)["outcome"] == "PASS"
    assert run_cached(command, scope, graph)["cache"] == "hit"
    tree.append("09_broker/broker/registry.py", _probe_comment())
    assert cache_lookup(command, scope, graph) is None


def test_cached_pass_reused(graph: dict) -> None:
    scope = _scope_stub("L1")
    command = "python scripts/validate_imports.py"
    assert run_cached(command, scope, graph)["outcome"] == "PASS"
    hit = cache_lookup(command, scope, graph)
    assert hit is not None and hit["outcome"] == "PASS"


def test_cached_fail_reused(graph: dict, tree: TreeEdit) -> None:
    scope = _scope_stub("L0")
    command = "ruff check 09_broker/broker/registry.py"
    tree.append("09_broker/broker/registry.py", "\ndef broken(:\n")
    first = run_cached(command, scope, graph)
    assert first["outcome"] == "FAIL"
    second = run_cached(command, scope, graph)
    assert second["cache"] == "hit" and second["outcome"] == "FAIL"


def test_timeout_env_not_reused(graph: dict) -> None:
    scope = _scope_stub("L0")
    stored = validate_scope.cache_store(
        "python scripts/validate_routes.py", scope, graph, "TIMEOUT", None, 1.0
    )
    assert stored == {"stored": False, "outcome": "TIMEOUT"}
    stored = validate_scope.cache_store(
        "python scripts/validate_routes.py", scope, graph, "ENV_FAIL", None, 1.0
    )
    assert stored == {"stored": False, "outcome": "ENV_FAIL"}
    # A planted TIMEOUT record under the true key must never read back as usable.
    built = build_cache_key("python scripts/validate_routes.py", scope, graph)
    path = validate_scope.VALID_DIR / f"{built['key']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "key": built["key"],
                "material": built["material"],
                "command": "python scripts/validate_routes.py",
                "outcome": "TIMEOUT",
                "rc": None,
            }
        ),
        encoding="utf-8",
    )
    try:
        assert cache_lookup("python scripts/validate_routes.py", scope, graph) is None
    finally:
        path.unlink(missing_ok=True)


def test_interrupted_validation_ignored(graph: dict) -> None:
    import os
    import time

    scope = _scope_stub("L1")
    validate_scope.VALID_DIR.mkdir(parents=True, exist_ok=True)
    (validate_scope.VALID_DIR / "abc.tmp-123").write_text("half", encoding="utf-8")
    built = build_cache_key("python scripts/validate_authority.py", scope, graph)
    lockdir = validate_scope.VALID_DIR / f".lock-{built['key']}"
    lockdir.mkdir(parents=True, exist_ok=True)
    old = time.time() - 1000.0
    os.utime(lockdir, (old, old))
    try:
        result = run_cached("python scripts/validate_authority.py", scope, graph)
        assert result["outcome"] == "PASS"
    finally:
        (validate_scope.VALID_DIR / "abc.tmp-123").unlink(missing_ok=True)


def test_concurrent_validation_safe(graph: dict, tree: TreeEdit) -> None:
    import threading

    tree.append("scripts/validate_structure.py", _probe_comment())
    scope = _scope_stub("L4")
    command = "python scripts/validate_structure.py"
    executions = {"count": 0}
    real_run = execute_surface.run_validation

    def _counting(manifest: dict, timeout_s: int = 600, cwd: object = None) -> dict:  # noqa: ARG001
        executions["count"] += 1
        return real_run(manifest, timeout_s=timeout_s)

    import unittest.mock as mock

    with mock.patch.object(execute_surface, "run_validation", _counting):
        results: list[dict] = []
        threads = [
            threading.Thread(target=lambda: results.append(run_cached(command, scope, graph)))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
    assert len(results) == 2
    assert all(r["outcome"] == "PASS" for r in results)
    assert executions["count"] == 1


def test_command_unavailable(graph: dict) -> None:
    scope = _scope_stub("L0")
    result = run_cached("frobnicate now --things", scope, graph)
    assert result["outcome"] == "UNAVAILABLE"
    again = run_cached("frobnicate now --things", scope, graph)
    assert again["cache"] == "hit"


def test_validator_change_invalidates(graph: dict, tree: TreeEdit) -> None:
    scope = _scope_stub("L4")
    command = "python scripts/validate_routes.py"
    assert run_cached(command, scope, graph)["outcome"] == "PASS"
    tree.append("scripts/validate_routes.py", _probe_comment())
    assert cache_lookup(command, scope, graph) is None


def test_toolchain_change_invalidates(graph: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    scope = _scope_stub("L1")
    command = "python scripts/validate_routes.py"
    assert run_cached(command, scope, graph)["outcome"] == "PASS"
    monkeypatch.setattr(validate_scope, "_TOOLCHAIN_OVERRIDE", {"python": "0.0-fake"})
    assert cache_lookup(command, scope, graph) is None


def test_cache_key_deterministic(graph: dict) -> None:
    scope = _scope_stub("L2")
    first = build_cache_key("pytest 09_broker/broker/tests -q", scope, graph)
    second = build_cache_key("pytest 09_broker/broker/tests -q", scope, graph)
    assert first["key"] == second["key"]
    assert "timestamp" not in json.dumps(first["material"])
    other = build_cache_key("pytest 02_data/data/tests -q", scope, graph)
    assert other["key"] != first["key"]


# ── Stale handling ──


def test_tampered_manifest_scoped_as_change() -> None:
    manifest = _manifest()
    tampered = dict(manifest)
    tampered["hashes"] = dict(manifest["hashes"])
    target = manifest["modify"]["must"][0]
    tampered["hashes"][target] = "0" * 64
    # No rebuild-and-bless: the mismatch is scoped as a real change.
    result = validate_scope.validate_manifest(tampered, run=False)
    assert result["status"] == "VALID"
    assert result["scope"] == "L2"
    assert result["commands"]


def test_stale_index_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()

    def _broken() -> dict:
        raise repo_index.IndexError("simulated")

    monkeypatch.setattr(repo_index, "ensure_fresh", _broken)
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["status"] == "UNKNOWN"


def test_stale_graph_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()

    def _broken() -> dict:
        raise repo_graph.GraphError("simulated")

    monkeypatch.setattr(repo_index, "ensure_fresh", _broken)
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["status"] == "UNKNOWN"


# ── Benchmark cases (live mutations, restored) ──

PROBE_PRIVATE = "\n\ndef _phase6_probe() -> int:\n    return 1\n"
PROBE_PUBLIC = '\n\ndef phase6_probe_api() -> str:\n    return "probe"\n'

# Ambient breakage in THIS environment (pre-existing, unrelated to changes):
# the fyers suite leaks real credentials from the ambient machine store.
AMBIENT_FAIL_COMMANDS = {
    "pytest 02_data/data/tests/test_fyers_provider.py -q",
    "pytest 02_data/data/tests -q",
}


def _assert_valid_or_ambient(result: dict) -> None:
    if result["status"] == "VALID":
        return
    assert result["status"] == "FAILED"
    fails = [r["command"] for r in result["results"] if r["outcome"] == "FAIL"]
    assert fails and all(f in AMBIENT_FAIL_COMMANDS for f in fails), fails


def _probe_comment() -> str:
    """Per-run-unique comment (unique cache keys; restored by TreeEdit)."""
    return f"\n\n# phase6 probe {uuid.uuid4().hex}\n"


def _run_full_py() -> dict:
    assert os.environ.get("VAYREN_SCOPE_BENCH_NESTED") != "1"
    os.environ["VAYREN_SCOPE_BENCH_NESTED"] = "1"
    try:
        graph = repo_index.ensure_fresh()["graph"]
        scope = {"level": "L5", "levels": ["L5"], "commands": list(validate_scope.FULL_PY)}
        results = [run_cached(command, scope, graph) for command in validate_scope.FULL_PY]
        return {"results": results, "graph": graph}
    finally:
        del os.environ["VAYREN_SCOPE_BENCH_NESTED"]


def _compare_incremental_full(incr: dict, full: dict) -> dict:
    """Same-command outcomes must match; known-env failures listed explicitly.

    Granularity differs by design (per-file vs partition): pytest partitions
    PASS iff every file passes, ruff whole iff every file passes, so outcome
    comparison happens on governance commands (identical strings) plus the
    broker partition vs incremental broker files, with pyright-whole and the
    ambient fyers failure carved out as documented pre-existing breakage.
    """
    full_by_command = {}
    for entry in full["results"]:
        full_by_command.setdefault(entry["command"], entry)
    mismatches: list[str] = []
    for entry in incr["results"]:
        peer = full_by_command.get(entry["command"])
        if peer is None:
            continue
        if peer["outcome"] != entry["outcome"]:
            mismatches.append(f"{entry['command']}: incr={entry['outcome']} full={peer['outcome']}")
    known = {"pyright", "pytest 02_data/data/tests -q"}
    return {
        "mismatches": mismatches,
        "known_env": sorted(known),
        "broker_full": full_by_command.get("pytest 09_broker/broker/tests -q", {}),
    }


def _assert_full_py_sane(full: dict) -> None:
    """Full baseline may only fail on documented pre-existing breakage."""
    by_command = {e["command"]: e for e in full["results"]}
    assert by_command["pytest 09_broker/broker/tests -q"]["outcome"] == "PASS"
    for command, entry in by_command.items():
        if command in ("pyright", "pytest 02_data/data/tests -q"):
            continue
        assert entry["outcome"] == "PASS", (command, entry)


def test_bench_private_py_change(tree: TreeEdit) -> None:
    manifest = _manifest()
    tree.append("09_broker/broker/registry.py", PROBE_PRIVATE)
    incr = validate_scope.validate_manifest(manifest, run=True)
    _assert_valid_or_ambient(incr)
    assert incr["scope"] == "L2"
    second = validate_scope.validate_manifest(manifest, run=True)
    hits, total = second["cache"].split(" ")[0].split("/")
    assert int(hits) == int(total) > 0
    full = _run_full_py()
    compared = _compare_incremental_full(incr, full)
    assert compared["mismatches"] == []
    assert compared["broker_full"].get("outcome") == "PASS"
    _assert_full_py_sane(full)


def test_bench_public_py_change(tree: TreeEdit) -> None:
    manifest = _manifest()
    tree.append("09_broker/broker/registry.py", PROBE_PUBLIC)
    incr = validate_scope.validate_manifest(manifest, run=True)
    _assert_valid_or_ambient(incr)
    assert incr["scope"] == "L2"
    full = _run_full_py()
    compared = _compare_incremental_full(incr, full)
    assert compared["mismatches"] == []
    assert compared["broker_full"].get("outcome") == "PASS"
    _assert_full_py_sane(full)


def test_bench_strategy_change(tree: TreeEdit) -> None:
    manifest, _ = execute_surface.build_manifest("add strategy parameter")
    tree.append("05_strategy/strategy/registry.py", PROBE_PRIVATE)
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["status"] in ("VALID", "ESCALATED")
    assert result["commands"]


def test_bench_market_rust_change(tree: TreeEdit) -> None:
    manifest, _ = execute_surface.build_manifest("add timeframe")
    tree.append("rust/vayren-core/src/aggregate.rs", "\n// phase6 benchmark probe\n")
    result = validate_scope.validate_manifest(manifest, run=True)
    assert result["status"] == "VALID"
    assert any(c.startswith("cargo test") for c in result["commands"])
    assert all(r["outcome"] == "PASS" for r in result["results"])


def test_bench_ui_slint_change(tree: TreeEdit) -> None:
    manifest, _ = execute_surface.build_manifest("market screen")
    tree.append("rust/vayren-shell/ui/market.slint", "\n// phase6 benchmark probe\n")
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["status"] in ("VALID", "ESCALATED")
    assert any("vayren-shell" in c for c in result["commands"])


def test_bench_cross_module_change(tree: TreeEdit) -> None:
    manifest = _manifest()
    tree.append("09_broker/broker/registry.py", PROBE_PRIVATE)
    tree.append("00_app/app/services/broker_selection_service.py", PROBE_PRIVATE)
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["scope"] == "L4"
    assert any("broker" in c or "app" in c for c in result["commands"])


def test_bench_cross_language_change(tree: TreeEdit) -> None:
    manifest, _ = execute_surface.build_manifest("ffi bridge")
    assert manifest["status"] == "READY"
    tree.append("01_core/core/native/loader.py", PROBE_PRIVATE)
    tree.append("rust/vayren-core/src/lib.rs", "\n// phase6 benchmark probe\n")
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["scope"] == "L4"
    assert "python scripts/validate_language_ownership.py" in result["commands"]


def test_bench_unexpected_file(tree: TreeEdit) -> None:
    manifest = _manifest()
    tree.append("09_broker/broker/registry.py", PROBE_PRIVATE)
    tree.create("09_broker/broker/surprise.py", '"""Unexpected."""\n')
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["scope"] == "L5"
    assert "UNEXPECTED_CHANGE_SURFACE" in result["reason"]


def test_bench_unchanged_clean() -> None:
    manifest = _manifest()
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["status"] == "VALID"
    assert result["scope"] == "L0" and result["commands"] == []


def test_bench_stale_cache_miss(tree: TreeEdit) -> None:
    manifest = _manifest()
    first = validate_scope.validate_manifest(manifest, run=True)
    _assert_valid_or_ambient(first)
    # Novel content (never validated before) forces cache misses + recompute.
    tree.append("09_broker/broker/registry.py", _probe_comment())
    second = validate_scope.validate_manifest(manifest, run=True)
    _assert_valid_or_ambient(second)
    assert second["scope"] != "L0" and second["commands"]
    assert second["cache"].split(" ")[0].split("/")[0] == "0"


def test_bench_validator_change(tree: TreeEdit) -> None:
    manifest = _manifest()
    tree.append("scripts/validate_routes.py", "\n# phase6 benchmark probe\n")
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["scope"] == "L4"
    assert "python scripts/validate_routes.py" in result["commands"]


def test_bench_docs_change(tree: TreeEdit) -> None:
    manifest = _manifest()
    tree.append("90_brain/module_contracts.md", "\n<!-- phase6 benchmark probe -->\n")
    result = validate_scope.validate_manifest(manifest, run=False)
    assert result["scope"] == "L3"
