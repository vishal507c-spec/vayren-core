"""Semantic dependency graph over behavioral migration units.

Low-level dependencies migrate before higher-level consumers. The graph is
built from the unit registry plus real production import edges observed by
the scanner, then topologically ordered so the planner can explain exactly
which dependency must migrate first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .registry import seed_units


@dataclass
class DependencyNode:
    unit_id: str
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    depth: int = 0
    criticality: str = "standard"


def build_graph() -> dict[str, DependencyNode]:
    units = seed_units()
    nodes: dict[str, DependencyNode] = {}
    for unit in units:
        nodes[unit.unit_id] = DependencyNode(
            unit_id=unit.unit_id,
            dependencies=list(unit.dependencies),
            criticality="live-critical" if unit.live_critical else "standard",
        )
    for unit_id, node in nodes.items():
        for dep in node.dependencies:
            if dep in nodes and unit_id not in nodes[dep].dependents:
                nodes[dep].dependents.append(unit_id)
    _assign_depths(nodes)
    return nodes


def _assign_depths(nodes: dict[str, DependencyNode]) -> None:
    resolved: dict[str, int] = {}

    def depth(unit_id: str, trail: tuple[str, ...]) -> int:
        if unit_id in resolved:
            return resolved[unit_id]
        if unit_id in trail:
            return 0
        node = nodes.get(unit_id)
        if node is None or not node.dependencies:
            resolved[unit_id] = 0
            return 0
        value = 1 + max(depth(dep, trail + (unit_id,)) for dep in node.dependencies)
        resolved[unit_id] = value
        return value

    for unit_id in nodes:
        nodes[unit_id].depth = depth(unit_id, ())


def migration_order(nodes: dict[str, DependencyNode]) -> list[str]:
    """Safe migration order: dependencies first, live-critical last."""
    return sorted(
        nodes,
        key=lambda uid: (nodes[uid].depth, nodes[uid].criticality == "live-critical", uid),
    )


def find_cycle(nodes: dict[str, DependencyNode]) -> list[str] | None:
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(uid: str) -> list[str] | None:
        if uid in visiting:
            return visiting[visiting.index(uid) :] + [uid]
        if uid in visited or uid not in nodes:
            return None
        visiting.append(uid)
        for dep in nodes[uid].dependencies:
            cycle = visit(dep)
            if cycle:
                return cycle
        visiting.pop()
        visited.add(uid)
        return None

    for uid in nodes:
        cycle = visit(uid)
        if cycle:
            return cycle
    return None


def blockers_for(
    unit_id: str, states: dict[str, str], nodes: dict[str, DependencyNode] | None = None
) -> list[str]:
    """Dependencies that are not yet usable from Rust.

    INTEGRATED (parity + shadow fresh, production on the Rust path) already
    satisfies dependents — the later states only retire the Python side.
    """
    graph = nodes or build_graph()
    node = graph.get(unit_id)
    if node is None:
        return [f"unknown unit: {unit_id}"]
    satisfied = (
        "INTEGRATED",
        "RUST_CANONICAL",
        "PYTHON_DEPRECATED",
        "PYTHON_QUARANTINED",
        "PYTHON_REMOVED",
        "FINAL_VERIFIED",
        "MIGRATED",
    )
    blocked: list[str] = []
    for dep in node.dependencies:
        if states.get(dep, "DISCOVERED") not in satisfied:
            blocked.append(dep)
    return blocked


def graph_dict(nodes: dict[str, DependencyNode]) -> dict:
    return {
        uid: {
            "dependencies": node.dependencies,
            "dependents": node.dependents,
            "depth": node.depth,
            "criticality": node.criticality,
        }
        for uid, node in nodes.items()
    }


__all__ = [
    "DependencyNode",
    "build_graph",
    "migration_order",
    "find_cycle",
    "blockers_for",
    "graph_dict",
]
