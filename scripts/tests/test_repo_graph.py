"""Repository Intelligence Graph tests (Phase 1).

Correctness (§14): representative queries resolve against the generated
graph. Determinism (§15): two builds from the same tree are identical.
Negative: unknown lookups + tampered graphs fail cleanly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_graph import (  # noqa: E402
    build_graph,
    load_graph,
    run_query,
)
from validate_repo_graph import validate_graph  # noqa: E402

ROOT = SCRIPTS_DIR.parent

_CACHED: dict | None = None


def _graph() -> dict:
    """One shared build for read-only tests (fresh builds stay in determinism tests)."""
    global _CACHED
    if _CACHED is None:
        _CACHED, _ = build_graph()
    return _CACHED


def test_strategy_symbol_resolves() -> None:
    graph = _graph()
    hits = run_query(graph, "symbol", "StrategyRegistry")
    assert isinstance(hits, list) and len(hits) == 1
    symbol = hits[0]
    assert symbol["file"] == "05_strategy/strategy/registry.py"
    assert symbol["line"] >= 1
    assert symbol["module"] == "05_strategy/strategy"
    assert symbol["language"] == "PYTHON"
    assert symbol["public"] is True


def test_market_symbol_resolves() -> None:
    graph = _graph()
    raw = run_query(graph, "symbol", "Bar")
    assert isinstance(raw, list)
    hits = [s for s in raw if s["module"] == "03_market/market"]
    assert len(hits) == 1
    assert hits[0]["file"] == "03_market/market/models/bar.py"
    assert hits[0]["line"] >= 1


def test_rust_owned_symbol_resolves() -> None:
    graph = _graph()
    hits = run_query(graph, "symbol", "aggregate")
    assert isinstance(hits, list) and hits
    target = next(s for s in hits if s["file"] == "rust/vayren-core/src/aggregate.rs")
    assert target["module"] == "rust/vayren-core"
    assert target["language"] == "RUST"
    assert target["callers_complete"] is False


def test_slint_symbol_resolves() -> None:
    graph = _graph()
    hits = run_query(graph, "symbol", "AppWindow")
    assert isinstance(hits, list) and len(hits) == 1
    assert hits[0]["file"] == "rust/vayren-shell/ui/app.slint"
    assert hits[0]["module"] == "rust/vayren-shell"
    assert hits[0]["language"] == "RUST_SLINT"
    assert hits[0]["line"] >= 1


def test_broker_symbol_has_callers() -> None:
    graph = _graph()
    hits = run_query(graph, "symbol", "BrokerRegistry")
    assert isinstance(hits, list) and len(hits) == 1
    symbol = hits[0]
    assert symbol["module"] == "09_broker/broker"
    assert symbol["language"] == "PYTHON"
    assert symbol["calls"]
    assert symbol["called_by"]
    assert symbol["called_by"] == sorted(symbol["called_by"])


def test_cross_module_dependency_declared() -> None:
    graph = _graph()
    deps = run_query(graph, "deps", "05_strategy/strategy")
    assert isinstance(deps, dict)
    assert {"01_core/core", "03_market/market"} <= set(deps["declared"])


def test_strategy_backtest_drift_reported_with_evidence() -> None:
    graph = _graph()
    drift = [
        d
        for d in graph["drift"]
        if d["from"] == "05_strategy/strategy"
        and d["to"] == "06_backtest/backtest"
        and d["kind"] == "actual-without-declared"
    ]
    assert len(drift) == 1
    edge = next(
        e
        for e in graph["dependencies"]
        if e["from"] == "05_strategy/strategy"
        and e["to"] == "06_backtest/backtest"
        and e["kind"] == "actual"
    )
    assert any("dataset.py" in source for source in edge["sources"])


def test_dependents_resolve() -> None:
    graph = _graph()
    dependents = run_query(graph, "dependents", "01_core/core")
    assert isinstance(dependents, dict)
    assert "03_market/market" in dependents["depended_on_by"]
    assert "05_strategy/strategy" in dependents["depended_on_by"]


def test_broker_tests_resolve() -> None:
    graph = _graph()
    result = run_query(graph, "tests", "09_broker/broker")
    assert isinstance(result, dict)
    paths = [t["path"] for t in result["tests"]]
    assert paths
    assert all(p.startswith("09_broker/broker/tests/") for p in paths)


def test_file_level_test_mapping() -> None:
    graph = _graph()
    target = next(t for t in graph["tests"] if t["path"] == "02_data/data/tests/test_settings.py")
    assert target["granularity"] == "file"
    assert target["targets"] == ["02_data/data/settings.py"]


def test_owner_lookup_lists_modules() -> None:
    graph = _graph()
    owner = run_query(graph, "owner", "PYTHON_STRATEGY")
    assert isinstance(owner, dict)
    assert owner["domain"] == "STRATEGY"
    assert owner["required_language"] == "PYTHON"
    assert "05_strategy/strategy" in owner["modules"]


def test_contract_lookup_is_reference_only() -> None:
    graph = _graph()
    contract = run_query(graph, "contract", "09_broker/broker")
    assert isinstance(contract, dict)
    assert contract["contract"].startswith("90_brain/module_contracts.md")
    assert "broker-ubl" in contract["routes"]


def test_module_ownership_and_language() -> None:
    graph = _graph()
    by_id = {m["id"]: m for m in graph["modules"]}
    assert by_id["01_core/core"]["language"] == "RUST"
    assert by_id["01_core/core/ai"]["language"] == "PYTHON"
    assert by_id["09_broker/broker"]["owner"] == "BROKER_CONTRACT"
    assert by_id["rust/vayren-shell"]["language"] == "RUST_SLINT"


def test_unknown_symbol_returns_empty() -> None:
    graph = _graph()
    assert run_query(graph, "symbol", "NoSuchSymbolAnywhere") == []
    assert run_query(graph, "module", "99_ghost/ghost") is None
    assert run_query(graph, "file", "99_ghost/ghost.py") is None


def test_generated_graph_validates_clean() -> None:
    graph = _graph()
    assert validate_graph(graph) == []


def test_tampered_graph_fails_validation() -> None:
    graph = _graph()
    bad = copy.deepcopy(graph)
    bad["dependencies"].append({"from": "05_strategy/strategy", "to": "99_ghost/ghost"})
    errors = validate_graph(bad)
    assert any("99_ghost/ghost" in error for error in errors)


def test_broken_symbol_line_fails_validation() -> None:
    graph = _graph()
    bad = copy.deepcopy(graph)
    bad["symbols"][0]["line"] = 10**9
    errors = validate_graph(bad)
    assert any("broken line" in error for error in errors)


def _digest(graph: dict) -> str:
    """Short comparable fingerprint (avoids multi-MB pytest diffs on failure)."""
    return hashlib.sha256(json.dumps(graph, sort_keys=True).encode("utf-8")).hexdigest()


def test_determinism_double_build_identical() -> None:
    first, _ = build_graph()
    second, _ = build_graph()
    assert _digest(first) == _digest(second)


def test_committed_artifact_matches_fresh_build() -> None:
    committed = load_graph()
    fresh, _ = build_graph()
    assert committed["inputs_hash"] == fresh["inputs_hash"]
    assert _digest(committed) == _digest(fresh)


def test_graph_has_no_canonical_prose() -> None:
    text = (ROOT / "90_brain" / "repo_graph.json").read_text(encoding="utf-8")
    assert "INSERT OR IGNORE" not in text
    assert len(text.encode("utf-8")) < 2_000_000
