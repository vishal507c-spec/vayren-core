"""Always-warm index tests (Phase 2 test matrix §17).

All cases run against a synthetic sandbox repo (tmp_path) with path constants
monkeypatched — the real tree is never touched. NOTE: `validate_imports` may
resolve from sys.modules (real or sandbox copy); DOMAIN_DEPS is identical.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_graph  # noqa: E402
import repo_index  # noqa: E402
from validate_repo_graph import validate_graph  # noqa: E402

CHAPTERS = {
    "00_app/app": ("__init__.py", '"""sandbox"""\n', "service.py", "class Service:\n"),
    "01_core/core": (
        "__init__.py",
        '__all__ = ["Event"]\n',
        "events.py",
        "class Event:\n    pass\n",
    ),
    "02_data/data": ("__init__.py", '"""sandbox"""\n'),
    "03_market/market": (
        "__init__.py",
        '__all__ = ["Bar"]\n',
        "bar.py",
        "from core.events import Event\n\n\nclass Bar:\n    pass\n",
    ),
    "05_strategy/strategy": (
        "__init__.py",
        '"""sandbox"""\n',
        "registry.py",
        "from core.events import Event\nfrom market.bar import Bar\n\n\n"
        "class StrategyRegistry:\n    def make(self):\n        return (Event(), Bar())\n",
        "runtime.py",
        "from strategy.registry import StrategyRegistry\n"
        "from market.bar import Bar\n\n\n"
        "class Runtime:\n    def run(self):\n        return (StrategyRegistry(), Bar())\n",
    ),
    "06_backtest/backtest": ("__init__.py", '"""sandbox"""\n'),
    "07_risk/risk": ("__init__.py", '"""sandbox"""\n'),
    "08_execution/execution": ("__init__.py", '"""sandbox"""\n'),
    "09_broker/broker": (
        "__init__.py",
        '"""sandbox"""\n',
        "registry.py",
        "class BrokerRegistry:\n    pass\n\n\n"
        "def default_registry():\n    return BrokerRegistry()\n",
        "tests/test_registry.py",
        "from broker.registry import BrokerRegistry\n\n\n"
        "def test_registry_builds():\n    assert BrokerRegistry() is not None\n",
    ),
}

POLICY = {
    "_note": "sandbox",
    "version": 1,
    "rules": [
        {
            "id": rid,
            "domain": d,
            "required_language": "PYTHON",
            "directory_prefixes": [f"{c}/"],
            "excluded_subpaths": [],
        }
        for rid, c, d in [
            ("R_APP", "00_app/app", "APP"),
            ("R_CORE", "01_core/core", "CORE"),
            ("R_DATA", "02_data/data", "DATA"),
            ("R_MARKET", "03_market/market", "MARKET"),
            ("R_STRATEGY", "05_strategy/strategy", "STRATEGY"),
            ("R_BACKTEST", "06_backtest/backtest", "BACKTEST"),
            ("R_RISK", "07_risk/risk", "RISK"),
            ("R_EXECUTION", "08_execution/execution", "EXECUTION"),
            ("R_BROKER", "09_broker/broker", "BROKER"),
        ]
    ],
}

ROUTES = {
    "version": 1,
    "routes": [
        {
            "key": "strategy-change",
            "aliases": ["strategy"],
            "domain": "STRATEGY",
            "owner_module": "05_strategy/strategy",
            "language": "PYTHON",
            "primary_files": [],
            "secondary_files": [],
            "depends_on": ["01_core/core", "03_market/market"],
            "depended_on_by": [],
            "tests": {"files": []},
            "validation": [],
            "forbidden": [],
            "symbols": {},
            "contract": "c-strategy",
            "change_surface": {},
            "owner_paths": ["05_strategy/strategy"],
        },
        {
            "key": "broker-ubl",
            "aliases": ["broker"],
            "domain": "BROKER",
            "owner_module": "09_broker/broker",
            "language": "PYTHON",
            "primary_files": [],
            "secondary_files": [],
            "depends_on": [],
            "depended_on_by": [],
            "tests": {"files": []},
            "validation": [],
            "forbidden": [],
            "symbols": {},
            "contract": "c-broker",
            "change_surface": {},
            "owner_paths": ["09_broker/broker"],
        },
    ],
}

CONTRACTS = """# Module Contracts

### 5.1 Module: `00_app` — Manager

### 5.2 Module: `01_core` — Foundation

### 5.3 Module: `02_data` — Data

### 5.4 Module: `03_market` — Market

### 5.6 Module: `05_strategy` — Strategy

### 5.7 Module: `06_backtest` — Backtest

### 5.8 Module: `07_risk` — Risk

### 5.9 Module: `08_execution` — Execution

### 5.10 Module: `09_broker` — Broker
"""


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


@pytest.fixture()
def sb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for chapter, blobs in CHAPTERS.items():
        for name, content in zip(blobs[::2], blobs[1::2], strict=True):
            _write(tmp_path, f"{chapter}/{name}", content)
    for crate in ("rust/vayren-core", "rust/vayren-shell"):
        _write(tmp_path, f"{crate}/Cargo.toml", '[package]\nname = "x"\n')
        _write(tmp_path, f"{crate}/src/lib.rs", "// empty\n")
    real_validator = SCRIPTS_DIR / "validate_imports.py"
    dest = tmp_path / "scripts" / "validate_imports.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(real_validator.read_bytes())
    _write(tmp_path, "scripts/repo_graph.py", '"""sandbox generator marker"""\n')
    brain = tmp_path / "90_brain"
    brain.mkdir(exist_ok=True)
    _write(tmp_path, "90_brain/ownership_policy.json", json.dumps(POLICY, indent=2))
    _write(tmp_path, "90_brain/language_retention.json", json.dumps({"version": 2, "files": {}}))
    _write(tmp_path, "90_brain/task_routes.json", json.dumps(ROUTES, indent=2))
    _write(tmp_path, "90_brain/module_contracts.md", CONTRACTS)
    index_dir = tmp_path / ".repo_index"
    monkeypatch.setattr(repo_graph, "ROOT", tmp_path)
    monkeypatch.setattr(repo_graph, "GRAPH_PATH", tmp_path / "90_brain" / "repo_graph.json")
    monkeypatch.setattr(repo_graph, "POLICY_PATH", brain / "ownership_policy.json")
    monkeypatch.setattr(repo_graph, "RETENTION_PATH", brain / "language_retention.json")
    monkeypatch.setattr(repo_graph, "ROUTES_PATH", brain / "task_routes.json")
    monkeypatch.setattr(repo_graph, "CONTRACTS_PATH", brain / "module_contracts.md")
    monkeypatch.setattr(repo_graph, "SCRIPTS_DIR", tmp_path / "scripts")
    monkeypatch.setattr(repo_index, "ROOT", tmp_path)
    monkeypatch.setattr(repo_index, "SCRIPTS_DIR", tmp_path / "scripts")
    monkeypatch.setattr(repo_index, "INDEX_DIR", index_dir)
    monkeypatch.setattr(repo_index, "STATE_FILE", index_dir / "state.json")
    monkeypatch.setattr(repo_index, "MANIFEST_FILE", index_dir / "manifest.json")
    monkeypatch.setattr(repo_index, "FILEMETA_FILE", index_dir / "filemeta.json")
    monkeypatch.setattr(repo_index, "LOOKUP_FILE", index_dir / "lookup.pkl")
    monkeypatch.setattr(repo_index, "LOCK_DIR", index_dir / "lock")
    return tmp_path


def _digest_graph() -> str:
    return hashlib.sha256(repo_graph.GRAPH_PATH.read_bytes()).hexdigest()


def _symbol(graph: dict, qual: str) -> dict:
    return next(s for s in graph["symbols"] if s["qualified"] == qual)


def test_initial_index_creation(sb: Path) -> None:
    result = repo_index.refresh()
    assert result["status"] == "READY" and result["mode"] == "initial"
    assert repo_index.LOOKUP_FILE.is_file()
    probe = repo_index.freshness()
    assert probe["status"] == "READY", probe
    assert repo_index.self_check() == []
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []


def test_noop_refresh_when_unchanged(sb: Path) -> None:
    assert sb.is_dir()
    first = repo_index.refresh()
    before = _digest_graph()
    second = repo_index.refresh()
    assert second["mode"] == "noop"
    assert second["fingerprint"] == first["fingerprint"]
    assert _digest_graph() == before


def test_warm_lookup_matches_graph_queries(sb: Path) -> None:
    assert sb.is_dir()
    repo_index.refresh()
    payload = repo_index.ensure_fresh()
    for kind, name in [
        ("module", "05_strategy/strategy"),
        ("file", "09_broker/broker/registry.py"),
        ("symbol", "BrokerRegistry"),
        ("symbol", "strategy.registry:StrategyRegistry"),
        ("owner", "R_STRATEGY"),
        ("deps", "05_strategy/strategy"),
        ("dependents", "01_core/core"),
        ("tests", "09_broker/broker"),
        ("contract", "05_strategy/strategy"),
    ]:
        assert repo_index.query_with_maps(payload, kind, name) == repo_graph.run_query(
            payload["graph"], kind, name
        )
    assert repo_index.query_with_maps(payload, "owner", "R_STRATEGY") is not None
    assert repo_index.query_with_maps(payload, "symbol", "BrokerRegistry") != []


def test_modified_file_incremental(sb: Path) -> None:
    repo_index.refresh()
    _write(
        sb,
        "05_strategy/strategy/registry.py",
        "from core.events import Event\nfrom market.bar import Bar\n\n\n"
        "class StrategyRegistry:\n    def make(self):\n        return (Event(), Bar())\n\n\n"
        "class ExtraStrategy:\n    pass\n",
    )
    result = repo_index.refresh()
    assert result["mode"] == "incremental"
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    assert _symbol(graph, "strategy.registry:ExtraStrategy")["line"] == 10


def test_symbol_line_movement(sb: Path) -> None:
    repo_index.refresh()
    before = _symbol(repo_graph.load_graph(), "strategy.registry:StrategyRegistry")["line"]
    assert before == 5
    _write(
        sb,
        "05_strategy/strategy/registry.py",
        "# comment 1\n# comment 2\n# comment 3\n"
        "from core.events import Event\nfrom market.bar import Bar\n\n\n"
        "class StrategyRegistry:\n    def make(self):\n        return (Event(), Bar())\n",
    )
    repo_index.refresh()
    payload = repo_index.ensure_fresh()
    hits = repo_index.query_with_maps(payload, "symbol", "StrategyRegistry")
    assert isinstance(hits, list) and hits[0]["line"] == 8


def test_symbol_removed(sb: Path) -> None:
    repo_index.refresh()
    runtime = sb / "05_strategy" / "strategy" / "runtime.py"
    runtime.write_text(
        "from market.bar import Bar\n\n\n"
        "class Runtime:\n    def run(self):\n        return Bar()\n",
        encoding="utf-8",
        newline="\n",
    )
    repo_index.refresh()
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    quals = {s["qualified"] for s in graph["symbols"]}
    assert "strategy.runtime:Runtime" in quals
    assert [s for s in graph["symbols"] if s["file"] == "05_strategy/strategy/runtime.py"]
    # StrategyRegistry no longer called from runtime.py.
    assert (
        "strategy.runtime:Runtime"
        not in _symbol(graph, "strategy.registry:StrategyRegistry")["called_by"]
    )


def test_added_file(sb: Path) -> None:
    repo_index.refresh()
    _write(
        sb,
        "09_broker/broker/faces.py",
        "class TradingFace:\n    pass\n",
    )
    _write(
        sb,
        "09_broker/broker/tests/test_faces.py",
        "from broker.faces import TradingFace\n\n\ndef test_face():\n    assert TradingFace()\n",
    )
    result = repo_index.refresh()
    assert result["mode"] == "incremental"
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    assert _symbol(graph, "broker.faces:TradingFace")["file"] == "09_broker/broker/faces.py"
    target = next(t for t in graph["tests"] if t["path"] == "09_broker/broker/tests/test_faces.py")
    assert target["granularity"] == "file"
    assert target["targets"] == ["09_broker/broker/faces.py"]


def test_deleted_file(sb: Path) -> None:
    repo_index.refresh()
    (sb / "05_strategy" / "strategy" / "runtime.py").unlink()
    result = repo_index.refresh()
    assert result["mode"] == "incremental"
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    assert "05_strategy/strategy/runtime.py" not in {f["path"] for f in graph["files"]}
    assert not [s for s in graph["symbols"] if s["file"] == "05_strategy/strategy/runtime.py"]


def test_renamed_file(sb: Path) -> None:
    repo_index.refresh()
    src = sb / "03_market" / "market" / "bar.py"
    src.rename(sb / "03_market" / "market" / "candle.py")
    result = repo_index.refresh()
    assert result["mode"] == "incremental"
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    assert "03_market/market/bar.py" not in {f["path"] for f in graph["files"]}
    assert _symbol(graph, "market.candle:Bar")["file"] == "03_market/market/candle.py"


def test_dependency_change(sb: Path) -> None:
    repo_index.refresh()
    _write(
        sb,
        "05_strategy/strategy/runtime.py",
        "from strategy.registry import StrategyRegistry\n"
        "from market.bar import Bar\n"
        "from broker.registry import BrokerRegistry\n\n\n"
        "class Runtime:\n    def run(self):\n"
        "        return (StrategyRegistry(), Bar(), BrokerRegistry())\n",
    )
    repo_index.refresh()
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    module = next(m for m in graph["modules"] if m["id"] == "05_strategy/strategy")
    assert "09_broker/broker" in module["depends_on_actual"]
    edge = next(
        e
        for e in graph["dependencies"]
        if e["from"] == "05_strategy/strategy"
        and e["to"] == "09_broker/broker"
        and e["kind"] == "actual"
    )
    assert edge["sources"] == ["05_strategy/strategy/runtime.py"]
    assert {
        "from": "05_strategy/strategy",
        "to": "09_broker/broker",
        "kind": "actual-without-declared",
    } in graph["drift"]


def test_caller_change(sb: Path) -> None:
    repo_index.refresh()
    _write(
        sb,
        "05_strategy/strategy/runtime.py",
        "from strategy.registry import StrategyRegistry\n\n\n"
        "class Runtime:\n    def run(self):\n        return StrategyRegistry()\n",
    )
    repo_index.refresh()
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    runtime = _symbol(graph, "strategy.runtime:Runtime")
    assert runtime["calls"] == ["strategy.registry:StrategyRegistry"]
    registry = _symbol(graph, "strategy.registry:StrategyRegistry")
    assert "strategy.runtime:Runtime" in registry["called_by"]
    assert "market.bar:Bar" not in runtime["calls"]


def test_test_change(sb: Path) -> None:
    repo_index.refresh()
    _write(
        sb,
        "09_broker/broker/tests/test_registry.py",
        "from broker.registry import BrokerRegistry\n"
        "from broker.registry import default_registry\n\n\n"
        "def test_registry_builds():\n    assert default_registry() is not None\n",
    )
    result = repo_index.refresh()
    assert result["mode"] == "incremental"
    assert validate_graph(repo_graph.load_graph(), sb) == []


def test_ownership_change_triggers_full_rebuild(sb: Path) -> None:
    repo_index.refresh()
    policy = json.loads((sb / "90_brain" / "ownership_policy.json").read_text(encoding="utf-8"))
    for rule in policy["rules"]:
        if rule["id"] == "R_STRATEGY":
            rule["id"] = "R_STRATEGY_V2"
            rule["domain"] = "STRATEGY_V2"
    _write(sb, "90_brain/ownership_policy.json", json.dumps(policy, indent=2))
    assert repo_index.freshness()["status"] == "REBUILD_REQUIRED"
    result = repo_index.refresh()
    assert result["mode"].startswith("full")
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    module = next(m for m in graph["modules"] if m["id"] == "05_strategy/strategy")
    assert module["owner"] == "R_STRATEGY_V2"
    assert module["domain"] == "STRATEGY_V2"


def test_language_policy_change(sb: Path) -> None:
    repo_index.refresh()
    policy = json.loads((sb / "90_brain" / "ownership_policy.json").read_text(encoding="utf-8"))
    for rule in policy["rules"]:
        if rule["id"] == "R_BROKER":
            rule["required_language"] = "RUST"
    _write(sb, "90_brain/ownership_policy.json", json.dumps(policy, indent=2))
    result = repo_index.refresh()
    assert result["mode"].startswith("full")
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    module = next(m for m in graph["modules"] if m["id"] == "09_broker/broker")
    assert module["language"] == "RUST"


def test_contract_change_triggers_full_rebuild(sb: Path) -> None:
    repo_index.refresh()
    text = (sb / "90_brain" / "module_contracts.md").read_text(encoding="utf-8")
    text = text.replace("### 5.10 Module: `09_broker` — Broker\n", "")
    _write(sb, "90_brain/module_contracts.md", text)
    result = repo_index.refresh()
    assert result["mode"].startswith("full")
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    module = next(m for m in graph["modules"] if m["id"] == "09_broker/broker")
    assert module["contract"] == "UNKNOWN"


def test_route_change_updates_declared_deps(sb: Path) -> None:
    repo_index.refresh()
    routes = json.loads((sb / "90_brain" / "task_routes.json").read_text(encoding="utf-8"))
    for route in routes["routes"]:
        if route["key"] == "strategy-change":
            route["depends_on"].append("09_broker/broker")
    _write(sb, "90_brain/task_routes.json", json.dumps(routes, indent=2))
    result = repo_index.refresh()
    assert result["mode"].startswith("full")
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    module = next(m for m in graph["modules"] if m["id"] == "05_strategy/strategy")
    assert "09_broker/broker" in module["depends_on_declared"]


def test_incremental_equals_full_rebuild(sb: Path) -> None:
    repo_index.refresh()
    _write(sb, "05_strategy/strategy/extra.py", "class Extra:\n    pass\n")
    (sb / "05_strategy" / "strategy" / "runtime.py").unlink()
    _write(
        sb,
        "09_broker/broker/tests/test_registry.py",
        "from broker.registry import default_registry\n\n\n"
        "def test_registry_builds():\n    assert default_registry() is not None\n",
    )
    assert repo_index.refresh()["mode"] == "incremental"
    incremental_bytes = repo_graph.GRAPH_PATH.read_bytes()
    assert repo_index.refresh(full=True)["mode"] == "full"
    assert repo_graph.GRAPH_PATH.read_bytes() == incremental_bytes


def test_interrupted_update_recovery(sb: Path) -> None:
    repo_index.refresh()
    before = _digest_graph()
    (repo_index.INDEX_DIR / "repo_graph.json.tmp-99999").write_text(
        "half-written", encoding="utf-8"
    )
    repo_index.LOCK_DIR.mkdir(parents=True, exist_ok=True)
    old = time.time() - 1000.0
    os.utime(repo_index.LOCK_DIR, (old, old))
    _write(sb, "01_core/core/events.py", "class Event:\n    pass\n\n\nclass Envelope:\n    pass\n")
    result = repo_index.refresh()
    assert result["status"] == "READY"
    assert validate_graph(repo_graph.load_graph(), sb) == []
    assert _digest_graph() != before
    assert not repo_index.LOCK_DIR.exists()


def test_corrupted_index_recovery(sb: Path) -> None:
    assert sb.is_dir()
    repo_index.refresh()
    with open(repo_index.LOOKUP_FILE, "r+b") as handle:
        handle.seek(100)
        handle.write(b"\x00\xff\x00\xff")
    assert repo_index.freshness()["status"] == "INVALID"
    payload = repo_index.ensure_fresh()
    hits = repo_index.query_with_maps(payload, "symbol", "BrokerRegistry")
    assert isinstance(hits, list) and len(hits) == 1
    assert repo_index.self_check() == []


def test_stale_index_never_validated_as_fresh(sb: Path) -> None:
    repo_index.refresh()
    _write(sb, "01_core/core/events.py", "class Event:\n    pass\n\n\nclass Extra:\n    pass\n")
    probe = repo_index.freshness()
    assert probe["status"] == "STALE"
    assert repo_index.self_check() == [f"stale: {probe}"]
    payload = repo_index.ensure_fresh()
    assert repo_index.freshness()["status"] == "READY"
    assert repo_index.query_with_maps(payload, "symbol", "Extra") != []


def test_generator_failure_preserves_previous(sb: Path) -> None:
    repo_index.refresh()
    before = _digest_graph()
    _write(sb, "90_brain/ownership_policy.json", "{not json")
    with pytest.raises((repo_graph.GraphError, repo_index.IndexError)):
        repo_index.refresh()
    assert _digest_graph() == before
    assert repo_graph.load_graph()["entity_count"] > 0


def test_concurrent_updates_serialized(sb: Path) -> None:
    repo_index.refresh()
    _write(sb, "01_core/core/events.py", "class Event:\n    pass\n\n\nclass Extra:\n    pass\n")
    outcomes: list[str] = []
    errors: list[str] = []

    def worker() -> None:
        try:
            outcomes.append(repo_index.refresh()["status"])
        except Exception as exc:  # noqa: BLE001 - record, do not fail the thread silently
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert errors == []
    assert outcomes == ["READY", "READY"]
    first = _digest_graph()
    assert repo_index.refresh()["mode"] == "noop"
    assert _digest_graph() == first
    assert validate_graph(repo_graph.load_graph(), sb) == []


def test_lookup_payload_deterministic(sb: Path) -> None:
    assert sb.is_dir()
    repo_index.refresh()
    payload = repo_index.ensure_fresh()
    first = pickle.dumps({"graph": payload["graph"], "maps": payload["maps"]}, protocol=4)
    second = pickle.dumps({"graph": payload["graph"], "maps": payload["maps"]}, protocol=4)
    assert first == second

    def _has_sets(node: object) -> bool:
        if isinstance(node, (set, frozenset)):
            return True
        if isinstance(node, dict):
            return any(_has_sets(v) for v in node.values())
        return isinstance(node, (list, tuple)) and any(_has_sets(v) for v in node)

    assert not _has_sets(payload)


def test_malformed_source_recorded_not_fatal(sb: Path) -> None:
    repo_index.refresh()
    _write(sb, "05_strategy/strategy/broken.py", "def broken(:\n")
    result = repo_index.refresh()
    assert result["mode"] == "incremental"
    assert validate_graph(repo_graph.load_graph(), sb) == []
    graph = repo_graph.load_graph()
    assert any(
        n["kind"] == "parse-failed" and n["ref"] == "05_strategy/strategy/broken.py"
        for n in graph["unresolved"]
    )


def test_missing_module_import_ignored(sb: Path) -> None:
    repo_index.refresh()
    _write(sb, "05_strategy/strategy/ghost.py", "import ghost_module\n")
    assert repo_index.refresh()["mode"] == "incremental"
    graph = repo_graph.load_graph()
    assert validate_graph(graph, sb) == []
    module = next(m for m in graph["modules"] if m["id"] == "05_strategy/strategy")
    assert "ghost_module" not in module["depends_on_actual"]
