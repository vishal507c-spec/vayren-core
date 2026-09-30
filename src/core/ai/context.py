"""AI context builder — selective, deterministic context for AI consumption.

Provides relevant subsets of: components, capabilities, contracts,
dependencies, workflows, events, system state, architecture history, and
engineering memory. Never dumps the whole repository: only requested
sections are built, and each section can be filtered.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from core.ai._vocab import SystemModel, build_snapshot
from core.ai.memory.engineering import EngineeringMemory
from core.ai.memory.performance import PerformanceMemory

_log = logging.getLogger(__name__)

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


# Sections built when build() gets no request: a limited, cheap default, never
# the whole dump (architecture_history and engineering_memory can be large and
# need an explicit opt-in).
DEFAULT_SECTIONS = ("components", "capabilities", "system_state")


@dataclass(frozen=True)
class AiContext:
    """The built context: one deterministic text block per section."""

    sections: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", MappingProxyType(dict(self.sections)))

    def with_section(self, name: str, text: str) -> AiContext:
        """Return a new context with one section added or replaced."""
        merged = dict(self.sections)
        merged[name] = text
        return AiContext(sections=merged)

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

        Without a request, only `DEFAULT_SECTIONS` are built — never the
        whole dump.
        """
        if request is None:
            request = ContextRequest(sections=DEFAULT_SECTIONS)
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
        for manifest in getattr(self._system, "manifests", ()):
            identity = getattr(manifest, "identity", None)
            name = getattr(identity, "name", None)
            if name is None:
                _log.warning("skipping a manifest with no identity.name in components section")
                continue
            if names and name not in names:
                continue
            provides = ", ".join(
                getattr(decl_id, "value", decl_id)
                for decl in (getattr(manifest, "capabilities", ()) or ())
                for decl_id in (getattr(decl, "id", None),)
                if decl_id is not None
            )
            consumes = ", ".join(
                getattr(cap, "value", cap)
                for cap in (getattr(manifest, "capabilities_consumed", ()) or ())
            )
            line = (
                f"- {name} v{getattr(manifest, 'version', '?')} [{getattr(manifest, 'type', '?')}]"
            )
            if provides:
                line += f": provides {provides}"
            if consumes:
                line += f"; consumes {consumes}"
            lines.append(line)
        return "\n".join(lines)

    def _render_capabilities(self, prefix: str) -> str:
        graph = getattr(self._system, "capability_graph", None)
        if graph is None:
            _log.warning("skipping capabilities section: system has no capability_graph")
            return ""
        lines = []
        for capability in graph.capabilities():
            if capability.startswith(prefix):
                providers = ", ".join(graph.providers(capability))
                lines.append(f"capability {capability} -> {providers}")
        return "\n".join(lines)

    def _render_contracts(self, names: tuple[str, ...]) -> str:
        lines: list[str] = []
        for manifest in getattr(self._system, "manifests", ()):
            identity = getattr(manifest, "identity", None)
            name = getattr(identity, "name", None)
            if name is None:
                _log.warning("skipping a manifest with no identity.name in contracts section")
                continue
            if names and name not in names:
                continue
            contract = getattr(manifest, "contract", None)
            if contract is None:
                continue
            lines.append(f"- {name}:")
            for invariant in getattr(contract, "invariants", ()) or ():
                lines.append(f"    invariant: {invariant}")
            for capability_contract in getattr(contract, "capabilities", ()) or ():
                capability = getattr(capability_contract, "capability", None)
                rules = getattr(capability_contract, "rules", None)
                if capability is None or rules is None:
                    _log.warning(
                        "skipping a capability contract with no capability/rules in %s", name
                    )
                    continue
                lines.append(f"    capability {getattr(capability, 'value', capability)}:")
                if getattr(rules, "guarantees", ()):
                    lines.append(f"        guarantees: {', '.join(rules.guarantees)}")
                if getattr(rules, "failure_modes", ()):
                    lines.append(f"        failure modes: {', '.join(rules.failure_modes)}")
        return "\n".join(lines)

    def _render_dependencies(self, names: tuple[str, ...]) -> str:
        lines: list[str] = []
        for manifest in getattr(self._system, "manifests", ()):
            identity = getattr(manifest, "identity", None)
            name = getattr(identity, "name", None)
            if name is None:
                _log.warning("skipping a manifest with no identity.name in dependencies section")
                continue
            if names and name not in names:
                continue
            hard = (
                ", ".join(
                    getattr(dep, "name", dep)
                    for dep in (getattr(manifest, "dependencies", ()) or ())
                )
                or "none"
            )
            optional = (
                ", ".join(
                    getattr(dep, "name", dep)
                    for dep in (getattr(manifest, "optional_dependencies", ()) or ())
                )
                or "none"
            )
            lines.append(f"- {name}: depends on {hard}; optional {optional}")
        return "\n".join(lines)

    def _render_workflows(self, ids: tuple[str, ...]) -> str:
        workflows = getattr(self._system, "workflows", None)
        if workflows is None:
            _log.warning("skipping workflows section: system has no workflows")
            return ""
        lines: list[str] = []
        for workflow in workflows.list():
            workflow_id = getattr(workflow, "id", None)
            if workflow_id is None:
                _log.warning("skipping a workflow with no id")
                continue
            if ids and workflow_id not in ids:
                continue
            steps = "; ".join(
                f"{getattr(step, 'id', '?')}("
                f"{getattr(getattr(step, 'capability', None), 'value', '?')})"
                for step in (getattr(workflow, "steps", ()) or ())
            )
            lines.append(f"- {workflow_id}: {steps}")
        return "\n".join(lines)

    def _render_events(self, names: tuple[str, ...]) -> str:
        graph = getattr(self._system, "event_graph", None)
        if graph is None:
            _log.warning("skipping events section: system has no event_graph")
            return ""
        lines: list[str] = []
        for event in graph.events():
            if names and event not in names:
                continue
            lines.append(
                f"- {event}: produced by {', '.join(graph.producers(event))}; "
                f"consumed by {', '.join(graph.consumers(event))}"
            )
        return "\n".join(lines)

    def _render_system_state(self) -> str:
        try:
            snapshot = build_snapshot(self._system)
        except NotImplementedError:
            _log.warning("skipping system_state section: no snapshot builder available")
            return "system snapshot not available"
        lines = [
            f"components: {len(getattr(snapshot, 'components', ()) or ())}, "
            f"capabilities: {len(getattr(snapshot, 'capabilities', ()) or ())}, "
            f"events: {len(getattr(snapshot, 'events', ()) or ())}, "
            f"workflows: {len(getattr(snapshot, 'workflows', ()) or ())}"
        ]
        for key, items in (getattr(snapshot, "gaps", {}) or {}).items():
            if items:
                lines.append(f"gap ({key}): {', '.join(items)}")
        return "\n".join(lines)

    def _render_architecture_history(self) -> str:
        if self._engineering is None:
            return "engineering memory not available"
        lines = [
            f"- {getattr(entry, 'id', '?')}: {getattr(entry, 'problem', '?')} | "
            f"{getattr(getattr(entry, 'decision', None), 'value', '?')} | "
            f"{getattr(entry, 'reason', '')}"
            for entry in self._engineering.decisions()
        ]
        return "\n".join(lines) or "no architecture decisions recorded"

    def _render_engineering_memory(self, query: str) -> str:
        if self._engineering is None:
            return "engineering memory not available"
        lines = []
        for entry in self._engineering.search(query):
            summary = (
                f"{getattr(entry, 'problem', '')} | {getattr(entry, 'hypothesis', '')} | "
                f"{getattr(entry, 'result', '')}"
            )
            decision = getattr(getattr(entry, "decision", None), "value", "?")
            lines.append(f"- {getattr(entry, 'id', '?')}: {summary} | {decision}")
        return "\n".join(lines) or "no matching entries"
