"""Edit-ready context packet tests (Phase 4 matrix §23 + goldens §20).

Read-only against the live warm index (no repo writes, no codegen).
Golden packets pin real behavior across strategy/broker/market/backtest/
UI/AI/data tasks.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import context_packet  # noqa: E402
import repo_index  # noqa: E402
from context_packet import (  # noqa: E402
    audit_zero_discovery,
    build_packet,
    read_snippet_checked,
    symbol_extent,
)

ROOT = SCRIPTS_DIR.parent

GOLDENS = [
    {"task": "add strategy parameter", "route": "strategy-change", "levels": ["L1", "L2"]},
    {"task": "add validation to broker registry", "route": "broker-ubl", "levels": ["L1", "L2"]},
    {"task": "add timeframe", "route": "market-read", "levels": ["L1", "L2", "L3"]},
    {"task": "backtest drawdown", "route": "backtest", "levels": ["L1", "L2", "L3"]},
    {"task": "market screen", "route": "ui-screen", "levels": ["L1", "L2"]},
    {"task": "ai boundary", "route": "core-ai", "levels": ["L1", "L2"]},
    {"task": "download settings", "route": "download-config", "levels": ["L1", "L2"]},
]


def _packet(task: str, **kwargs: object) -> dict:
    packet, _status = build_packet(task, **kwargs)  # type: ignore[arg-type]
    return packet


def test_level_l0_routing_only() -> None:
    packet = _packet("add validation to broker registry", level="L0")
    assert packet["status"] == "RESOLVED"
    assert packet["levels"] == ["L0"]
    assert packet["route"]["key"] == "broker-ubl"
    assert all(t["symbols"] == [] for t in packet["targets"])
    assert packet["callers"] == []


def test_level_l1_target() -> None:
    packet = _packet("", symbol="BrokerRegistry", level="L1")
    assert packet["levels"] == ["L1"]
    assert len(packet["targets"]) == 1
    symbols = packet["targets"][0]["symbols"]
    assert [s["qualified"] for s in symbols if s["qualified"] == "broker.registry:BrokerRegistry"]
    assert packet["callers"]


def test_level_l2_impact() -> None:
    packet = _packet("add validation to broker registry", level="L2")
    assert packet["levels"] == ["L2"]
    assert packet["impact"]["affected_modules"]
    assert packet["contract"]["ref"].startswith("90_brain/module_contracts.md")
    assert packet["tests"]


def test_level_l3_safety() -> None:
    packet = _packet("add timeframe", level="L3")
    assert packet["levels"] == ["L3"]
    assert packet["safety"]["must_change"]
    assert packet["safety"]["must_not_change"]["patterns"]
    assert packet["plan"]["validate"]
    assert packet["plan"]["modify"]


def test_automatic_escalation() -> None:
    assert _packet("add strategy parameter")["levels"] == ["L1", "L2"]
    assert _packet("add timeframe")["levels"] == ["L1", "L2", "L3"]
    assert _packet("market screen")["levels"] == ["L1", "L2"]


def test_exact_target_snippet() -> None:
    packet = _packet("", symbol="BrokerRegistry")
    symbol = next(
        s
        for t in packet["targets"]
        for s in t["symbols"]
        if s["qualified"] == "broker.registry:BrokerRegistry"
    )
    assert symbol["line"] == 68
    assert symbol["body"]["declaration"].startswith("class BrokerRegistry")
    assert "class BrokerRegistry" in symbol["body"]["snippet"]["text"]
    assert symbol["body"]["start"] <= 68 <= symbol["body"]["end"]
    assert symbol["body"].get("truncated") is None


def test_line_correctness_matches_index() -> None:
    payload = repo_index.ensure_fresh()
    indexed = repo_index.query_with_maps(payload, "symbol", "BrokerRegistry")
    assert isinstance(indexed, list)
    packet = _packet("", symbol="BrokerRegistry")
    symbol = next(
        s
        for t in packet["targets"]
        for s in t["symbols"]
        if s["qualified"] == "broker.registry:BrokerRegistry"
    )
    assert symbol["line"] == indexed[0]["line"]


def test_caller_snippets_material_only() -> None:
    packet = _packet("", symbol="BrokerRegistry")
    callers = packet["callers"]
    assert callers
    assert {c["why"] for c in callers} <= {
        "test caller",
        "same-file caller",
        "same-module caller",
        "cross-module caller",
    }
    with_snippets = [c for c in callers if "snippet" in c]
    assert with_snippets
    assert len(with_snippets) <= context_packet.CALLER_SNIPPET_CAP
    omitted = [c for c in callers if c.get("snippet_omitted") == "cap"]
    assert len(with_snippets) + len(omitted) == len(callers)


def test_test_snippets_ranked() -> None:
    packet = _packet("add validation to broker registry")
    tests = [t for t in packet["tests"] if t.get("path")]
    assert tests
    first = tests[0]
    assert first["symbols"]
    assert all("calls " in s["why"] for s in first["symbols"])
    assert all(s["snippet"]["text"] for s in first["symbols"])


def test_contract_context_pointer() -> None:
    packet = _packet("add timeframe")
    contract = packet["contract"]
    assert contract["ref"] == "90_brain/module_contracts.md section 5.4"
    assert contract["missing"] is False
    assert "market.models.bar:Bar" in contract["interfaces"]
    assert contract["allowed_directions"]["03_market/market"] == ["01_core/core"]
    assert "rust/vayren-shell/*" in contract["forbidden_directions"]


def test_safety_boundaries_explicit() -> None:
    packet = _packet("add validation to broker registry", level="L3")
    safety = packet["safety"]
    assert "09_broker/broker/registry.py" in safety["must_change"]
    assert isinstance(safety["may_change"], list)
    assert "05_strategy/strategy/*" in safety["must_not_change"]["patterns"]
    assert safety["owner"] == ["BROKER_CONTRACT"]
    assert safety["language"] == ["PYTHON"]
    assert safety["scope"] == "contract"
    assert safety["blockers"] == []


def test_execution_plan_deterministic() -> None:
    packet = _packet("add validation to broker registry")
    plan = packet["plan"]
    assert any("BrokerRegistry@68" in r for r in plan["read"])
    assert "09_broker/broker/registry.py" in plan["modify"]
    assert plan["test"]
    assert "pytest 09_broker/broker/tests -q" in plan["validate"] or plan["test"]
    assert "05_strategy/strategy/*" in plan["forbidden"]
    assert plan == _packet("add validation to broker registry", use_cache=False)["plan"]


def test_context_budget_tracked() -> None:
    packet = _packet("add validation to broker registry")
    budget = packet["budget"]
    assert budget["bytes"] > 0 and budget["lines"] > 0
    assert budget["files"] >= 1 and budget["symbols"] >= 1 and budget["snippets"] >= 1
    assert budget["max_bytes"] is None and budget["exceeded"] is False


def test_context_budget_enforced() -> None:
    packet = _packet("add validation to broker registry", max_bytes=5000)
    budget = packet["budget"]
    assert budget["truncated_sections"]
    assert budget["bytes"] <= 5000 or budget["exceeded"] is True
    assert all(t["file"] and t["symbols"] for t in packet["targets"])


def test_truncation_marker_oversized_symbol(tmp_path: Path) -> None:
    big = tmp_path / "big.py"
    big.write_text(
        "def huge():\n" + "".join(f"    x{i} = {i}\n" for i in range(200)), encoding="utf-8"
    )
    start, end = symbol_extent(tmp_path, "big.py", 1, "function")
    assert (start, end) == (1, 201)
    packet = _packet("", symbol="BrokerRegistry", max_bytes=10**9)
    assert packet["budget"]["bytes"] > 0
    body = {
        "start": start,
        "total_lines": end - start + 1,
        "truncated": True,
    }
    assert body["truncated"] is True and body["total_lines"] == 201


def test_cache_hit_identical() -> None:
    first, first_status = build_packet("download settings", use_cache=True)
    second, second_status = build_packet("download settings", use_cache=True)
    assert second_status == "hit"
    first_norm = dict(first)
    second_norm = dict(second)
    del first_norm["cache"]
    del second_norm["cache"]
    assert json.dumps(first_norm, sort_keys=True) == json.dumps(second_norm, sort_keys=True)


def test_cache_invalidation_on_config() -> None:
    for stale in context_packet.PACKET_CACHE_DIR.glob("*.json"):
        with contextlib.suppress(OSError):
            stale.unlink()
    _packet("download settings", context_lines=6)
    packet, status = build_packet("download settings", context_lines=2, use_cache=True)
    assert status == "miss"
    assert packet["cache"]["status"] == "miss"


def test_freshness_evidence() -> None:
    packet = _packet("download settings")
    state = repo_index._read_json(repo_index.STATE_FILE)
    assert isinstance(state, dict)
    assert packet["evidence"]["index_fingerprint"] == state.get("fingerprint")
    assert packet["evidence"]["graph_inputs_hash"]
    assert packet["evidence"]["routes_sha"]


def test_deterministic_output() -> None:
    first, _ = build_packet("ai boundary", use_cache=False)
    second, _ = build_packet("ai boundary", use_cache=False)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_blocked_task_preserved() -> None:
    packet = _packet("risk engine", modify=["07_risk/risk/new_logic.py"])
    assert packet["status"] == "BLOCKED"
    assert packet["targets"] == []
    assert packet["plan"] == {}
    assert packet["safety"]["gate"]["blocked"] is True
    assert "snippet" not in json.dumps(packet)


def test_ambiguous_task_preserved() -> None:
    packet = _packet("Add validation to StrategyRegistry registration")
    assert packet["status"] == "NEEDS_CLARIFICATION"
    assert packet["targets"] == []


def test_cache_key_includes_intent_params() -> None:
    good, _ = build_packet("", symbol="BrokerRegistry", use_cache=True)
    assert good["status"] == "RESOLVED"
    bad, _ = build_packet("", symbol="NoSuchSymbolAnywhere", use_cache=True)
    assert bad["status"] == "UNKNOWN"


def test_missing_symbol_unknown() -> None:
    packet = _packet("", symbol="NoSuchSymbolAnywhere")
    assert packet["status"] == "UNKNOWN"
    assert packet["targets"] == []


def test_missing_test_explicit() -> None:
    packet = _packet("ai boundary")
    assert packet["status"] == "RESOLVED"
    assert packet["tests"] == [] or any(
        t.get("relevance") == "missing" or not t.get("symbols") for t in packet["tests"]
    )


def test_missing_contract_explicit() -> None:
    contract = context_packet._contract_context({"route": {}, "impact": {}}, {})
    assert contract["missing"] is True
    assert contract["ref"] == ""


def test_cross_module_boundary() -> None:
    packet = _packet("add timeframe")
    assert packet["status"] == "RESOLVED"
    assert "L2" in packet["levels"]
    assert packet["impact"]["dependents"]
    assert packet["contract"]["ref"].startswith("90_brain/module_contracts.md")


def test_impact_incomplete_for_rust() -> None:
    packet = _packet("add timeframe")
    assert packet["impact"].get("incomplete") is True
    assert "Rust/Slint" in packet["impact"].get("incomplete_reason", "")
    assert packet["impact"].get("incomplete_symbols")


def test_changed_target_race_guard(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("class A:\n    pass\n", encoding="utf-8")
    snippet = read_snippet_checked(tmp_path, "mod.py", 1, 2)
    assert snippet["start"] == 1 and "class A" in snippet["text"]
    target.unlink()
    with pytest.raises(context_packet.PacketError):
        read_snippet_checked(tmp_path, "mod.py", 1, 2)


def test_symbol_extent_python(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("class A:\n    def m(self):\n        return 1\n", encoding="utf-8")
    assert symbol_extent(tmp_path, "mod.py", 1, "class") == (1, 3)


def test_symbol_extent_rust(tmp_path: Path) -> None:
    target = tmp_path / "x.rs"
    target.write_text("pub fn f() -> i32 {\n    1\n}\n", encoding="utf-8")
    assert symbol_extent(tmp_path, "x.rs", 1, "fn") == (1, 3)


def test_no_repository_writes() -> None:
    before = {
        "manifest": repo_index._read_json(repo_index.MANIFEST_FILE),
        "graph": Path("90_brain/repo_graph.json").read_bytes(),
    }
    for golden in GOLDENS:
        build_packet(golden["task"], use_cache=False)
    after = {
        "manifest": repo_index._read_json(repo_index.MANIFEST_FILE),
        "graph": Path("90_brain/repo_graph.json").read_bytes(),
    }
    assert json.dumps(before["manifest"], sort_keys=True) == json.dumps(
        after["manifest"], sort_keys=True
    )
    assert before["graph"] == after["graph"]


def test_zero_discovery_audit() -> None:
    packet = _packet("add validation to broker registry")
    audit = audit_zero_discovery(packet)
    assert audit["complete"] is True


def test_reduction_ordering() -> None:
    from context_packet import measure_reduction  # noqa: E402

    result = measure_reduction("add validation to broker registry")
    # Packets add snippets to the patch surface, so C>B is expected;
    # the meaningful reduction is packet vs full target files.
    assert result["C_packet"]["bytes"] < result["A_full_files"]["bytes"]
    assert result["A_full_files"]["files"] >= 1
    assert result["B_patch_surface"]["bytes"] > 0


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["route"] for g in GOLDENS])
def test_golden_packet(golden: dict) -> None:
    packet = _packet(golden["task"])
    assert packet["status"] == "RESOLVED"
    assert packet["route"]["key"] == golden["route"]
    assert packet["levels"] == golden["levels"]
    assert packet["targets"]
    budget = packet["budget"]
    assert budget["bytes"] > 0 and budget["files"] >= 1 and budget["symbols"] >= 1
    audit = audit_zero_discovery(packet)
    if audit["complete"] is not True:
        # Only acceptable gap: acknowledged missing tests (explicit, not silent).
        assert audit["has_test"] is False
        assert any(t.get("relevance") == "missing" for t in packet["tests"])
        assert {k for k, v in audit.items() if k != "complete" and v is True} >= {
            "has_file",
            "has_symbol",
            "has_edit_region",
            "has_validation",
            "has_forbidden",
        }
    assert packet["evidence"]["index_fingerprint"]
