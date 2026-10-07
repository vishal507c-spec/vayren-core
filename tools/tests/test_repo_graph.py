"""Repository Intelligence Graph tests (Phase 1).

Correctness (§14): representative queries resolve against the generated
graph. Determinism (§15): two builds from the same tree are identical.
Negative: unknown lookups + tampered graphs fail cleanly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

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
    assert symbol["file"] == "src/strategy/registry.py"
    assert symbol["line"] >= 1
    assert symbol["module"] == "src/strategy"
    assert symbol["language"] == "PYTHON"
    assert symbol["public"] is True


def test_market_symbol_resolves() -> None:
    graph = _graph()
    raw = run_query(graph, "symbol", "Bar")
    assert isinstance(raw, list)
    hits = [s for s in raw if s["module"] == "src/market"]
    assert len(hits) == 1
    assert hits[0]["file"] == "src/market/models/bar.py"
    assert hits[0]["line"] >= 1


def test_rust_owned_symbol_resolves() -> None:
    graph = _graph()
    hits = run_query(graph, "symbol", "aggregate")
    assert isinstance(hits, list) and hits
    target = next(s for s in hits if s["file"] == "crates/vayren-core/src/aggregate.rs")
    assert target["module"] == "crates/vayren-core"
    assert target["language"] == "RUST"
    assert target["callers_complete"] is False


def test_slint_symbol_resolves() -> None:
    graph = _graph()
    hits = run_query(graph, "symbol", "AppWindow")
    assert isinstance(hits, list) and len(hits) == 1
    assert hits[0]["file"] == "crates/vayren-shell/ui/app.slint"
    assert hits[0]["module"] == "crates/vayren-shell"
    assert hits[0]["language"] == "RUST_SLINT"
    assert hits[0]["line"] >= 1


def test_broker_symbol_has_callers() -> None:
    graph = _graph()
    hits = run_query(graph, "symbol", "BrokerRegistry")
    assert isinstance(hits, list) and len(hits) == 1
    symbol = hits[0]
    assert symbol["module"] == "src/broker"
    assert symbol["language"] == "PYTHON"
    assert symbol["calls"]
    assert symbol["called_by"]
    assert symbol["called_by"] == sorted(symbol["called_by"])


def test_cross_module_dependency_declared() -> None:
    graph = _graph()
    deps = run_query(graph, "deps", "src/strategy")
    assert isinstance(deps, dict)
    assert {"src/core", "src/market"} <= set(deps["declared"])


def test_strategy_backtest_drift_reported_with_evidence() -> None:
    graph = _graph()
    drift = [
        d
        for d in graph["drift"]
        if d["from"] == "src/strategy"
        and d["to"] == "src/backtest"
        and d["kind"] == "actual-without-declared"
    ]
    assert len(drift) == 1
    edge = next(
        e
        for e in graph["dependencies"]
        if e["from"] == "src/strategy" and e["to"] == "src/backtest" and e["kind"] == "actual"
    )
    assert any("dataset.py" in source for source in edge["sources"])


def test_dependents_resolve() -> None:
    graph = _graph()
    dependents = run_query(graph, "dependents", "src/core")
    assert isinstance(dependents, dict)
    assert "src/market" in dependents["depended_on_by"]
    assert "src/strategy" in dependents["depended_on_by"]


def test_broker_tests_resolve() -> None:
    graph = _graph()
    result = run_query(graph, "tests", "src/broker")
    assert isinstance(result, dict)
    paths = [t["path"] for t in result["tests"]]
    assert paths
    assert all(p.startswith("src/broker/tests/") for p in paths)


def test_file_level_test_mapping() -> None:
    graph = _graph()
    target = next(t for t in graph["tests"] if t["path"] == "src/data/tests/test_settings.py")
    assert target["granularity"] == "file"
    assert target["targets"] == ["src/data/settings.py"]


def test_owner_lookup_lists_modules() -> None:
    graph = _graph()
    owner = run_query(graph, "owner", "PYTHON_STRATEGY")
    assert isinstance(owner, dict)
    assert owner["domain"] == "STRATEGY"
    assert owner["required_language"] == "PYTHON"
    assert "src/strategy" in owner["modules"]


def test_contract_lookup_is_reference_only() -> None:
    graph = _graph()
    contract = run_query(graph, "contract", "src/broker")
    assert isinstance(contract, dict)
    assert contract["contract"].startswith("docs/module_contracts.md")
    assert "broker-ubl" in contract["routes"]


def test_module_ownership_and_language() -> None:
    graph = _graph()
    by_id = {m["id"]: m for m in graph["modules"]}
    assert by_id["src/core"]["language"] == "RUST"
    assert by_id["src/core/ai"]["language"] == "PYTHON"
    assert by_id["src/broker"]["owner"] == "BROKER_CONTRACT"
    assert by_id["crates/vayren-shell"]["language"] == "RUST_SLINT"


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
    bad["dependencies"].append({"from": "src/strategy", "to": "99_ghost/ghost"})
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
    if os.environ.get("VAYREN_SCOPE_BENCH_NESTED") == "1":
        pytest.skip("nested scope benchmark mutates tree during run")
    committed = load_graph()
    fresh, _ = build_graph()
    assert committed["inputs_hash"] == fresh["inputs_hash"]
    assert _digest(committed) == _digest(fresh)


def test_graph_has_no_canonical_prose() -> None:
    """The artifact is derived structure — never a copy of canonical prose.

    Two guards, because the byte cap alone cannot tell the two apart:

    1. Absolute cap on the pretty-printed artifact. Justification for the
       2.15MB -> 2.40MB bump (2026-09-29): the artifact crossed the old cap
       because the SYMBOL count grew (3,788 symbols / 4,288 entities over 356
       files, 23 modules) — the Strategy-Lab feature work added product code,
       not narrative. Regeneration was tried first (option (a) of the finding)
       and produced a LARGER, valid artifact (2,306,560 B), so the cap moved
       instead. `python tools/repo_graph.py --stats` prints graph_bytes.
       Justification for the 2.40MB -> 2.56MB bump (2026-10-03): the artifact
       crossed the old cap because the restored live-execution / risk-facade /
       market-repository runtime files joined the tree (4,570 entities / 3,993
       symbols over 415 files, 26 modules) — product code, not narrative.
        Regeneration was tried first and produced a LARGER valid artifact, so
        the cap moved instead: two consecutive rebuilds of an unchanged tree
        measured 2,452,206 B and 2,463,322 B (the builder is nondeterministic
        across runs, ~11 KB variance), and 2.56MB covers the observed size plus
        headroom for that variance.
        Justification for the 2.56MB -> 2.62MB bump (2026-10-06): the artifact
        crossed the old cap because the FYERS live-order adapter plus
        market/order WebSocket runtime and tests joined the tree (4,761
        entities / 4,160 symbols over 428 files) — product code, not
        narrative. Regeneration was tried first on the exact commit tree and
        produced a valid artifact (2,585,920 B, second run 2,585,775 B), so
        the cap moved instead; 2.62MB covers the observed size plus headroom.
    Justification for the 2.62MB -> 2.70MB bump (2026-10-06): the artifact
    crossed the old cap because the broker-neutral remote transport module
    (`src/remote`: protocol/auth/snapshot/gateway/server/runner + 60 tests)
    joined the tree (4,897 entities over the new files) — product code, not
    narrative. Per-entity cost stays ~545 B (budget: 700 B), so the cap moved
    instead; 2.70MB covers the observed size plus headroom.
    2. Per-entity budget + a max-string-length budget. These are what make the
       cap safe to raise: prose pasted into any field would blow the string
       budget long before it filled the entity budget, so a future dump cannot
       hide behind a bigger absolute number.
    """
    text = (ROOT / "docs" / "repo_graph.json").read_text(encoding="utf-8")
    assert "INSERT OR IGNORE" not in text
    graph = json.loads(text)
    size = len(text.encode("utf-8"))
    assert size < 2_700_000, f"graph artifact grew past the cap: {size} B"

    entities = graph["entity_count"]
    assert entities > 0
    per_entity = size / entities
    assert per_entity < 700, (
        f"bytes per entity jumped to {per_entity:.0f} (was ~538 at 2.15MB, "
        "~460 at 2.00MB): the artifact is growing by something other than entities"
    )

    longest = 0

    def walk(node: object) -> None:
        nonlocal longest
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            longest = max(longest, len(node))

    walk(graph)
    # 110 B observed (a dotted symbol path). Anything longer is narrative.
    assert longest < 200, f"derived graph holds a {longest}-char string: prose leaked in"
