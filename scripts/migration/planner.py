"""Migration planner: concrete READY/BLOCKED plan from the dependency graph.

For every unit the planner shows current owner, target owner, dependencies,
blockers, implementation/parity/shadow/integration/removal status and risk
level — and explains WHY something is blocked plus the exact unblocking
action.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import blockers_for, build_graph, migration_order
from .manifest_store import load_all
from .models import MigrationManifest
from .validator import production_uses_rust


@dataclass
class PlanEntry:
    unit_id: str
    owner: str
    target: str
    dependencies: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    state: str = "DISCOVERED"
    parity: str = "INCONCLUSIVE"
    shadow: str = "INCONCLUSIVE"
    integration: str = "INCONCLUSIVE"
    removal: str = "INCONCLUSIVE"
    risk: str = "standard"
    verdict: str = "READY"
    required_action: str = ""


def _required_action(entry: PlanEntry, manifest: MigrationManifest) -> str:
    if entry.blockers:
        first = entry.blockers[0]
        return f"migrate dependency first: {first} (run: migration parity {first})"
    if not manifest.target:
        return f"define the Rust target for {entry.unit_id}, then implement it"
    if manifest.parity.status != "PASS":
        return f"run differential suite: migration parity {entry.unit_id}"
    if manifest.shadow.status != "PASS":
        return f"run shadow comparison: migration shadow {entry.unit_id}"
    if not manifest.integration.production_uses_rust:
        return f"wire production path through Rust bridge for {entry.unit_id}"
    if manifest.state in ("DISCOVERED", "ANALYZED", "PLANNED", "BLOCKED", "READY"):
        return f"implement Rust target: {manifest.target or 'define target first'}"
    if manifest.authority == "rust" and manifest.state in ("RUST_CANONICAL",):
        return f"quarantine Python shadow: migration quarantine {entry.unit_id}"
    return "verify evidence freshness: migration verify"


def build_plan() -> list[PlanEntry]:
    manifests = load_all()
    graph = build_graph()
    states = {uid: m.state for uid, m in manifests.items()}
    order = migration_order(graph)
    entries: list[PlanEntry] = []
    for unit_id in order:
        manifest = manifests.get(unit_id)
        if manifest is None:
            continue
        node = graph[unit_id]
        uses_rust, _ = production_uses_rust(unit_id)
        blockers = blockers_for(unit_id, states, graph)
        if manifest.state == "MIGRATED":
            verdict = "MIGRATED"
        elif manifest.state in (
            "RUST_CANONICAL",
            "PYTHON_DEPRECATED",
            "PYTHON_QUARANTINED",
            "PYTHON_REMOVED",
            "FINAL_VERIFIED",
        ):
            verdict = "CANONICAL"
        elif (
            manifest.state == "REGRESSION"
            or blockers
            or manifest.parity.status == "FAIL"
            or manifest.shadow.status == "FAIL"
        ):
            verdict = "BLOCKED"
        elif manifest.parity.status == "PASS" and manifest.shadow.status == "PASS" and uses_rust:
            verdict = "READY"
        else:
            verdict = "BLOCKED"
        entry = PlanEntry(
            unit_id=unit_id,
            owner=manifest.authority,
            target="rust" if manifest.target else "tbd",
            dependencies=list(node.dependencies),
            blockers=blockers,
            state=manifest.state,
            parity=manifest.parity.status,
            shadow=manifest.shadow.status,
            integration="PASS" if uses_rust else "INCONCLUSIVE",
            removal=manifest.removal.status,
            risk="live-critical" if manifest.live_critical else "standard",
            verdict=verdict,
            required_action="",
        )
        entry.required_action = (
            "none — genuinely migrated"
            if verdict == "MIGRATED"
            else "hold canonical — retire Python via quarantine/finalize"
            if verdict == "CANONICAL"
            else _required_action(entry, manifest)
        )
        entries.append(entry)
    return entries


def ready_units(entries: list[PlanEntry] | None = None) -> list[PlanEntry]:
    return [e for e in (entries or build_plan()) if e.verdict == "READY"]


def blocked_units(entries: list[PlanEntry] | None = None) -> list[PlanEntry]:
    return [e for e in (entries or build_plan()) if e.verdict == "BLOCKED"]


__all__ = ["PlanEntry", "build_plan", "ready_units", "blocked_units"]
