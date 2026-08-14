"""AI context builder — selective, deterministic context for AI consumption.

Provides relevant subsets of: components, capabilities, contracts,
dependencies, workflows, events, system state, architecture history, and
engineering memory. Never dumps the whole repository: only requested
sections are built, and each section can be filtered.
"""

import json
from dataclasses import dataclass

from core.ai.memory.engineering import EngineeringMemory
from core.ai.memory.performance import PerformanceMemory
from core.system.snapshot import build_snapshot
from core.system.system_model import SystemModel

SECTIONS = (
    "components",
    "capabilities",
    "contracts",
    "dependencies",
    "workflows",
    "events",
    "system_state",
    "architecture_history",
    "engineering_memory",
)


@dataclass(frozen=True)
class ContextRequest:
    """Which context subsets to build, and their filters."""

    sections: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    capabilities_prefix: str = ""
    workflows: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    memory_query: str = ""

    def __post_init__(self) -> None:
        unknown = set(self.sections) - set(SECTIONS)
        if unknown:
            msg = "unknown context sections: " + ", ".join(sorted(unknown))
            raise ValueError(msg)


@dataclass
class AiContext:
    """The built context: one deterministic text block per section."""

    sections: dict[str, str]

    def render(self) -> str:
        """Render every section in the canonical order."""
        return "\n\n".join(
            f"[{section}]\n{self.sections[section]}"
            for section in SECTIONS
            if section in self.sections
        )

    def to_json(self) -> str:
        """Serialize the context as sorted, indented JSON."""
        data = {section: self.sections[section] for section in SECTIONS if section in self.sections}
        return json.dumps(data, indent=2, sort_keys=True)

    def __len__(self) -> int:
        return len(self.sections)


class ContextBuilder:
    """Builds selective context subsets from the system model and memories."""

    def __init__(
        self,
        system: SystemModel,
        engineering: EngineeringMemory | None = None,
        performance: PerformanceMemory | None = None,
    ) -> None:
        self._system = system
        self._engineering = engineering
        self._performance = performance

    def build(self, request: ContextRequest | None = None) -> AiContext:
        """Build exactly the requested sections, in canonical order.

        Without a request, every section is built.
        """
        if request is None:
            request = ContextRequest(sections=SECTIONS)
        sections: dict[str, str] = {}
        if "components" in request.sections:
            sections["components"] = self._render_components(request.components)
        if "capabilities" in request.sections:
            sections["capabilities"] = self._render_capabilities(request.capabilities_prefix)
        if "contracts" in request.sections:
            sections["contracts"] = self._render_contracts(request.components)
        if "dependencies" in request.sections:
            sections["dependencies"] = self._render_dependencies(request.components)
        if "workflows" in request.sections:
            sections["workflows"] = self._render_workflows(request.workflows)
        if "events" in request.sections:
            sections["events"] = self._render_events(request.events)
        if "system_state" in request.sections:
            sections["system_state"] = self._render_system_state()
        if "architecture_history" in request.sections:
            sections["architecture_history"] = self._render_architecture_history()
        if "engineering_memory" in request.sections:
            sections["engineering_memory"] = self._render_engineering_memory(request.memory_query)
        return AiContext(sections=sections)

    def _render_components(self, names: tuple[str, ...]) -> str:
        lines: list[str] = []
        for manifest in self._system.manifests:
            if names and manifest.identity.name not in names:
                continue
            provides = ", ".join(decl.id.value for decl in manifest.capabilities)
            consumes = ", ".join(cap.value for cap in manifest.capabilities_consumed)
            line = f"- {manifest.identity.name} v{manifest.version} [{manifest.type}]"
            if provides:
                line += f": provides {provides}"
            if consumes:
                line += f"; consumes {consumes}"
            lines.append(line)
        return "\n".join(lines)

    def _render_capabilities(self, prefix: str) -> str:
        lines = []
        for capability in self._system.capability_graph.capabilities():
            if capability.startswith(prefix):
                providers = ", ".join(self._system.capability_graph.providers(capability))
                lines.append(f"capability {capability} -> {providers}")
        return "\n".join(lines)

    def _render_contracts(self, names: tuple[str, ...]) -> str:
        lines: list[str] = []
        for manifest in self._system.manifests:
            if names and manifest.identity.name not in names:
                continue
            contract = manifest.contract
            if contract is None:
                continue
            lines.append(f"- {manifest.identity.name}:")
            for invariant in contract.invariants:
                lines.append(f"    invariant: {invariant}")
            for capability_contract in contract.capabilities:
                rules = capability_contract.rules
                lines.append(f"    capability {capability_contract.capability.value}:")
                if rules.guarantees:
                    lines.append(f"        guarantees: {', '.join(rules.guarantees)}")
                if rules.failure_modes:
                    lines.append(f"        failure modes: {', '.join(rules.failure_modes)}")
        return "\n".join(lines)

    def _render_dependencies(self, names: tuple[str, ...]) -> str:
        lines: list[str] = []
        for manifest in self._system.manifests:
            if names and manifest.identity.name not in names:
                continue
            hard = ", ".join(dep.name for dep in manifest.dependencies) or "none"
            optional = ", ".join(dep.name for dep in manifest.optional_dependencies) or "none"
            lines.append(f"- {manifest.identity.name}: depends on {hard}; optional {optional}")
        return "\n".join(lines)

    def _render_workflows(self, ids: tuple[str, ...]) -> str:
        lines: list[str] = []
        for workflow in self._system.workflows.list():
            if ids and workflow.id not in ids:
                continue
            steps = "; ".join(f"{step.id}({step.capability.value})" for step in workflow.steps)
            lines.append(f"- {workflow.id}: {steps}")
        return "\n".join(lines)

    def _render_events(self, names: tuple[str, ...]) -> str:
        lines: list[str] = []
        for event in self._system.event_graph.events():
            if names and event not in names:
                continue
            lines.append(
                f"- {event}: produced by {', '.join(self._system.event_graph.producers(event))}; "
                f"consumed by {', '.join(self._system.event_graph.consumers(event))}"
            )
        return "\n".join(lines)

    def _render_system_state(self) -> str:
        snapshot = build_snapshot(self._system)
        lines = [
            f"components: {len(snapshot.components)}, "
            f"capabilities: {len(snapshot.capabilities)}, "
            f"events: {len(snapshot.events)}, "
            f"workflows: {len(snapshot.workflows)}"
        ]
        for key, items in snapshot.gaps.items():
            if items:
                lines.append(f"gap ({key}): {', '.join(items)}")
        return "\n".join(lines)

    def _render_architecture_history(self) -> str:
        if self._engineering is None:
            return "engineering memory not available"
        lines = [
            f"- {entry.id}: {entry.problem} | {entry.decision.value} | {entry.reason}"
            for entry in self._engineering.decisions()
        ]
        return "\n".join(lines) or "no architecture decisions recorded"

    def _render_engineering_memory(self, query: str) -> str:
        if self._engineering is None:
            return "engineering memory not available"
        lines = []
        for entry in self._engineering.search(query):
            summary = f"{entry.problem} | {entry.hypothesis} | {entry.result}"
            lines.append(f"- {entry.id}: {summary} | {entry.decision.value}")
        return "\n".join(lines) or "no matching entries"
