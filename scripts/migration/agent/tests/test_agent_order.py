"""Agent order: exact BLOCKED sequence from the dependency graph."""

from __future__ import annotations

from scripts.migration.agent.order import blocked_order
from scripts.migration.config import ROOT
from scripts.migration.graph import blockers_for, build_graph, migration_order
from scripts.migration.registry import seed_units
from scripts.migration.validator import BRIDGES

EXPECTED_BLOCKED_SEQUENCE = [
    "core.event_bus.dispatch",
    "data.download.orchestration",
    "market.storage.sqlite",
    "risk.engine.evaluate",
    "execution.session.lifecycle",
    "chart.viewport.math",
]


def test_graph_order_is_dependency_safe() -> None:
    nodes = build_graph()
    order = migration_order(nodes)
    positions = {uid: index for index, uid in enumerate(order)}
    sequence = [uid for uid in order if uid in EXPECTED_BLOCKED_SEQUENCE]
    assert sequence == EXPECTED_BLOCKED_SEQUENCE
    for uid in EXPECTED_BLOCKED_SEQUENCE:
        for dep in nodes[uid].dependencies:
            assert positions[dep] < positions[uid], (dep, uid)


def test_session_waits_for_risk() -> None:
    nodes = build_graph()
    states = dict.fromkeys(nodes, "DISCOVERED")
    session_blockers = blockers_for("execution.session.lifecycle", states, nodes)
    assert "risk.engine.evaluate" in session_blockers
    assert blockers_for("risk.engine.evaluate", states, nodes) == []
    satisfied = {**states, "market.timeframe.aggregate": "INTEGRATED"}
    assert blockers_for("chart.viewport.math", satisfied, nodes) == []


def test_blocked_order_lists_current_blocked() -> None:
    ids = [e.unit_id for e in blocked_order()]
    assert set(ids) <= set(EXPECTED_BLOCKED_SEQUENCE)
    assert ids == sorted(ids, key=EXPECTED_BLOCKED_SEQUENCE.index)


def test_existing_rust_targets_have_bridge_proof() -> None:
    """Any unit whose Rust target exists on disk must have bridge proof."""
    for unit in seed_units():
        if unit.rust_target and (ROOT / unit.rust_target).is_file():
            assert unit.unit_id in BRIDGES, unit.unit_id
