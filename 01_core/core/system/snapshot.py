"""System snapshot — a deterministic, machine-readable view of the architecture.

Built entirely from the in-memory system model; no repository scanning.
The snapshot is JSON-ready for debugging, documentation, and AI context.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.system.system_model import SystemModel


@dataclass(frozen=True)
class SystemSnapshot:
    """Read-only architecture snapshot."""

    components: tuple[dict[str, Any], ...]
    capabilities: dict[str, list[str]]
    events: dict[str, dict[str, list[str]]]
    workflows: tuple[dict[str, Any], ...]
    gaps: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        """Return the snapshot as plain JSON-ready data."""
        return {
            "components": list(self.components),
            "capabilities": self.capabilities,
            "events": self.events,
            "workflows": list(self.workflows),
            "gaps": self.gaps,
        }

    def to_json(self) -> str:
        """Serialize the snapshot as sorted, indented JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def render(self) -> str:
        """Render the snapshot as deterministic human/AI readable text."""
        lines: list[str] = []
        lines.append(
            f"system model - components: {len(self.components)}, "
            f"capabilities: {len(self.capabilities)}, "
            f"events: {len(self.events)}, workflows: {len(self.workflows)}"
        )
        for component in self.components:
            name = str(component["name"])
            lines.append(f"- {name} v{component['version']} [{component['type']}]")
            lines.append(f"    provides: {', '.join(component['capabilities'])}")
            consumed = component["capabilities_consumed"]
            if consumed:
                lines.append(f"    consumes: {', '.join(consumed)}")
            deps = component["dependencies"]
            if deps:
                lines.append(f"    depends on: {', '.join(deps)}")
            produced = component["events_produced"]
            if produced:
                lines.append(f"    emits: {', '.join(produced)}")
            consumed_events = component["events_consumed"]
            if consumed_events:
                lines.append(f"    listens: {', '.join(consumed_events)}")
        for capability in sorted(self.capabilities):
            lines.append(f"capability {capability} -> {', '.join(self.capabilities[capability])}")
        for event in sorted(self.events):
            entry = self.events[event]
            lines.append(
                f"event {event}: produced by {', '.join(entry['producers'])}; "
                f"consumed by {', '.join(entry['consumers'])}"
            )
        for workflow in self.workflows:
            steps = "; ".join(f"{step['id']}({step['capability']})" for step in workflow["steps"])
            lines.append(f"workflow {workflow['id']}: {steps}")
        gap_labels = {
            "unresolved_consumers": "unresolved capability consumers",
            "unproduced_events": "consumed events with no producer",
            "unconsumed_events": "produced events with no consumer",
            "dependency_cycles": "dependency cycles",
        }
        for key, label in gap_labels.items():
            items = self.gaps.get(key, [])
            if items:
                lines.append(f"gap ({label}): {', '.join(items)}")
        return "\n".join(lines)


def build_snapshot(system: "SystemModel") -> SystemSnapshot:
    """Build a deterministic snapshot from the in-memory system model."""
    components = tuple(
        {
            "name": manifest.identity.name,
            "version": str(manifest.version),
            "type": manifest.type,
            "capabilities": [decl.id.value for decl in manifest.capabilities],
            "capabilities_consumed": [cap.value for cap in manifest.capabilities_consumed],
            "dependencies": [dep.name for dep in manifest.dependencies],
            "optional_dependencies": [dep.name for dep in manifest.optional_dependencies],
            "inputs": list(manifest.inputs),
            "outputs": list(manifest.outputs),
            "events_consumed": list(manifest.events_consumed),
            "events_produced": list(manifest.events_produced),
            "resource_requirements": list(manifest.resource_requirements),
            "side_effects": list(manifest.side_effects),
            "description": manifest.metadata.description,
        }
        for manifest in system.manifests
    )
    capabilities = {
        capability: list(system.capability_graph.providers(capability))
        for capability in system.capability_graph.capabilities()
    }
    events = {
        event: {
            "producers": list(system.event_graph.producers(event)),
            "consumers": list(system.event_graph.consumers(event)),
        }
        for event in system.event_graph.events()
    }
    workflows = tuple(
        {
            "id": workflow.id,
            "description": workflow.description,
            "inputs": list(workflow.inputs),
            "outputs": list(workflow.outputs),
            "constraints": list(workflow.constraints),
            "steps": [
                {
                    "id": step.id,
                    "capability": step.capability.value,
                    "inputs": list(step.inputs),
                    "outputs": list(step.outputs),
                }
                for step in workflow.steps
            ],
        }
        for workflow in system.workflows.list()
    )
    cycle = system.component_graph.find_cycle()
    gaps = {
        "unresolved_consumers": list(system.capability_graph.unresolved_consumers()),
        "unproduced_events": list(system.event_graph.unproduced()),
        "unconsumed_events": list(system.event_graph.unconsumed()),
        "dependency_cycles": list(cycle) if cycle else [],
    }
    return SystemSnapshot(
        components=components,
        capabilities=capabilities,
        events=events,
        workflows=workflows,
        gaps=gaps,
    )
