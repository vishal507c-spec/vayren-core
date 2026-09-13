"""Scanner + graph: real repository discovery and dependency-safe order."""

from __future__ import annotations

from scripts.migration import graph, scanner


def test_scan_finds_rust_owned_python_and_bridges() -> None:
    inv = scanner.scan()
    assert inv.python_files, "repository has python files"
    assert inv.rust_owned_python, "rust-owned python must be detected"
    assert inv.rust_files, "rust sources must be detected"
    assert inv.bridges, "ffi bridges must be detected"


def test_scan_detects_still_authoritative_units() -> None:
    from scripts.migration.validator import production_uses_rust

    inv = scanner.scan()
    assert (
        "risk.engine.evaluate" in inv.python_authoritative
        or production_uses_rust("risk.engine.evaluate")[0]
    )
    assert "chart.viewport.math" in inv.python_authoritative
    assert "execution.session.lifecycle" in inv.python_authoritative


def test_graph_has_no_cycles_and_orders_deps_first() -> None:
    nodes = graph.build_graph()
    assert graph.find_cycle(nodes) is None
    order = graph.migration_order(nodes)
    position = {uid: index for index, uid in enumerate(order)}
    for uid, node in nodes.items():
        for dep in node.dependencies:
            assert position[dep] < position[uid], f"{dep} must precede {uid}"


def test_blockers_explain_why() -> None:
    nodes = graph.build_graph()
    states = dict.fromkeys(nodes, "DISCOVERED")
    blockers = graph.blockers_for("execution.session.lifecycle", states, nodes)
    assert "execution.order_lifecycle" in blockers
    assert "risk.engine.evaluate" in blockers


def test_rust_impls_are_used_by_production() -> None:
    inv = scanner.scan()
    assert inv.rust_unused == [], f"orphaned rust implementations: {inv.rust_unused}"
