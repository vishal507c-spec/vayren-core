"""Change impact analysis — read-only, graph-derived risk assessment.

Risk levels follow deterministic rules:
- HIGH: the change reaches indirect dependents, or affects workflows.
- MEDIUM: the change has direct dependents/consumers only.
- LOW: the change affects nothing that depends on it.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from core.contracts.capability import CapabilityId
from core.contracts.component import ComponentId

if TYPE_CHECKING:
    from core.system.system_model import SystemModel


class RiskLevel(Enum):
    """Risk of a proposed change."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ChangeImpact:
    """Result of analyzing a proposed change."""

    target: str
    direct_dependents: tuple[str, ...]
    indirect_dependents: tuple[str, ...]
    affected_capabilities: tuple[str, ...]
    affected_consumers: tuple[str, ...]
    affected_workflows: tuple[str, ...]
    risk: RiskLevel
    reasons: tuple[str, ...]


def analyze_component_change(name: str, system: "SystemModel") -> ChangeImpact:
    """Analyze the impact of changing one component."""
    graph = system.component_graph
    direct = graph.dependents(name)
    indirect = tuple(dep for dep in graph.transitive_dependents(name) if dep not in direct)
    affected_components = {name, *direct, *indirect}
    affected_capabilities = tuple(
        sorted(
            {
                capability
                for component in affected_components
                for capability in system.capability_graph.capabilities_of(component)
            }
        )
    )
    consumers = {
        consumer
        for capability in affected_capabilities
        for consumer in system.capability_graph.consumers(capability)
        if consumer not in affected_components
    }
    affected_workflows = tuple(
        workflow.id
        for workflow in system.workflows.list()
        if any(step.capability.value in affected_capabilities for step in workflow.steps)
    )
    reasons: list[str] = []
    if indirect:
        reasons.append(f"indirect dependents: {', '.join(indirect)}")
    if affected_workflows:
        reasons.append(f"used by workflows: {', '.join(affected_workflows)}")
    elif direct:
        reasons.append(f"direct dependents: {', '.join(direct)}")
    if not reasons:
        reasons.append("no dependents, consumers, or workflows reference this component")
    if indirect or affected_workflows:
        risk = RiskLevel.HIGH
    elif direct:
        risk = RiskLevel.MEDIUM
    else:
        risk = RiskLevel.LOW
    return ChangeImpact(
        target=f"component:{name}",
        direct_dependents=direct,
        indirect_dependents=indirect,
        affected_capabilities=affected_capabilities,
        affected_consumers=tuple(sorted(consumers)),
        affected_workflows=affected_workflows,
        risk=risk,
        reasons=tuple(reasons),
    )


def analyze_capability_change(capability: str, system: "SystemModel") -> ChangeImpact:
    """Analyze the impact of changing one capability."""
    graph = system.capability_graph
    providers = graph.providers(capability)
    consumers = graph.consumers(capability)
    workflows = system.workflows.find_by_capability(capability)
    affected_workflows = tuple(workflow.id for workflow in workflows)
    reasons: list[str] = []
    risk = RiskLevel.LOW
    if affected_workflows:
        risk = RiskLevel.HIGH
        reasons.append(f"used by workflows: {', '.join(affected_workflows)}")
    elif consumers:
        risk = RiskLevel.MEDIUM
        reasons.append(f"consumers: {', '.join(consumers)}")
    elif providers:
        reasons.append("implemented but not consumed; only implementors affected")
    else:
        reasons.append("no provider, consumer, or workflow references this capability")
    return ChangeImpact(
        target=f"capability:{capability}",
        direct_dependents=consumers,
        indirect_dependents=(),
        affected_capabilities=(capability,),
        affected_consumers=consumers,
        affected_workflows=affected_workflows,
        risk=risk,
        reasons=tuple(reasons),
    )


def analyze_workflow_change(workflow_id: str, _system: "SystemModel") -> ChangeImpact:
    """Analyze the impact of changing one workflow."""
    reasons = ["no component or workflow references workflows"]
    return ChangeImpact(
        target=f"workflow:{workflow_id}",
        direct_dependents=(),
        indirect_dependents=(),
        affected_capabilities=(),
        affected_consumers=(),
        affected_workflows=(workflow_id,),
        risk=RiskLevel.LOW,
        reasons=tuple(reasons),
    )


def analyze_change(
    target: "ComponentId | CapabilityId | str", system: "SystemModel"
) -> ChangeImpact:
    """Auto-detect the change kind and analyze its impact.

    Component and workflow ids never contain dots; capability ids always do.
    """
    text = str(target)
    if "." in text:
        return analyze_capability_change(text, system)
    if system.workflows.has(text):
        return analyze_workflow_change(text, system)
    return analyze_component_change(text, system)
