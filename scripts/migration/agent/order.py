"""Exact migration order for BLOCKED units from the dependency graph.

Low-level dependencies migrate before higher-level consumers; live-critical
units are explicit. Every entry explains WHY it holds its position and what
unblocks it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..graph import blockers_for, build_graph, migration_order
from ..manifest_store import load_all


@dataclass(frozen=True)
class OrderEntry:
    position: int
    unit_id: str
    depth: int
    live_critical: bool
    state: str
    blocked_by: tuple
    why: str


def blocked_order() -> list[OrderEntry]:
    """BLOCKED units in the exact order the agent must attempt them."""
    manifests = load_all()
    graph = build_graph()
    states = {uid: m.state for uid, m in manifests.items()}
    entries: list[OrderEntry] = []
    for uid in migration_order(graph):
        manifest = manifests.get(uid)
        if manifest is None or manifest.state != "BLOCKED":
            continue
        node = graph[uid]
        blockers = tuple(blockers_for(uid, states, graph))
        if blockers:
            why = f"after dependencies migrate: {', '.join(blockers)}"
        elif not manifest.target:
            why = "no Rust target yet — needs analysis-generated target"
        else:
            why = "dependencies satisfied — candidate for autonomous migration"
        entries.append(
            OrderEntry(
                position=len(entries) + 1,
                unit_id=uid,
                depth=node.depth,
                live_critical=manifest.live_critical,
                state=manifest.state,
                blocked_by=blockers,
                why=why,
            )
        )
    return entries


__all__ = ["OrderEntry", "blocked_order"]
