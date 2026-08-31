"""System model — the machine-readable intelligence layer of VAYREN.

A queryable, read-only view of the entire registered system: components,
capabilities, contracts, dependencies, data flows, events, workflows,
and change impact. Built in-process from a ComponentRegistry; never
scans the repository and never touches production behavior.
"""

from core.contracts.capability import CapabilityId, CapabilityProvider
from core.contracts.component import ComponentId
from core.contracts.manifest import ComponentManifest
from core.registry.component_registry import ComponentRegistry
from core.system.capability_graph import CapabilityGraph
from core.system.change_impact import (
    ChangeImpact,
    analyze_change,
)
from core.system.component_graph import ComponentGraph
from core.system.data_flow import DataFlowModel
from core.system.event_graph import EventGraph
from core.system.snapshot import SystemSnapshot, build_snapshot
from core.system.workflow import Workflow, WorkflowRegistry


class SystemModel:
    """Queryable system model assembled from a component registry."""

    def __init__(
        self,
        registry: ComponentRegistry,
        workflows: WorkflowRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._manifests = tuple(component.manifest for component in registry.components())
        self._component_graph = ComponentGraph(self._manifests)
        self._capability_graph = CapabilityGraph(self._manifests)
        self._event_graph = EventGraph(self._manifests)
        self._workflows = workflows if workflows is not None else WorkflowRegistry()
        self._data_flow = DataFlowModel(self._manifests, self._workflows)

    @property
    def manifests(self) -> tuple[ComponentManifest, ...]:
        """All registered component manifests, ordered by name."""
        return self._manifests

    @property
    def component_graph(self) -> ComponentGraph:
        """The component dependency graph."""
        return self._component_graph

    @property
    def capability_graph(self) -> CapabilityGraph:
        """The capability provider/consumer graph."""
        return self._capability_graph

    @property
    def event_graph(self) -> EventGraph:
        """The event producer/consumer graph."""
        return self._event_graph

    @property
    def data_flow(self) -> DataFlowModel:
        """The component input/output model."""
        return self._data_flow

    @property
    def workflows(self) -> WorkflowRegistry:
        """The workflow registry."""
        return self._workflows

    def components(self) -> tuple[ComponentManifest, ...]:
        """All registered components, ordered by name."""
        return self._manifests

    def has_component(self, name: str) -> bool:
        """True if a component with this name is registered."""
        return name in self._component_graph.names()

    def find_component(self, name: str) -> ComponentManifest:
        """Return one component manifest, or raise KeyError."""
        for manifest in self._manifests:
            if manifest.identity.name == name:
                return manifest
        msg = f"Component not found: {name}"
        raise KeyError(msg)

    def capabilities(self) -> tuple[CapabilityId, ...]:
        """Every provided capability id, sorted."""
        return tuple(CapabilityId(value) for value in self._capability_graph.capabilities())

    def find_capability(self, prefix: str) -> tuple[CapabilityId, ...]:
        """Capability ids starting with the given prefix (e.g. ``"data."``)."""
        return tuple(
            CapabilityId(value)
            for value in self._capability_graph.capabilities()
            if value.startswith(prefix)
        )

    def find_implementations(
        self, capability: CapabilityId | str
    ) -> tuple[CapabilityProvider, ...]:
        """Providers of a capability; the registry keeps the actual objects."""
        return self._registry.providers(capability)

    def find_dependents(self, name: str) -> tuple[str, ...]:
        """Components directly depending on this component."""
        return self._component_graph.dependents(name)

    def find_dependencies(self, name: str) -> tuple[str, ...]:
        """Components this component directly depends on."""
        return self._component_graph.dependencies(name)

    def find_consumers(self, capability: CapabilityId | str) -> tuple[str, ...]:
        """Components consuming this capability."""
        return self._capability_graph.consumers(str(capability))

    def find_workflows(self, capability: CapabilityId | str | None = None) -> tuple[Workflow, ...]:
        """All workflows, or only those referencing the given capability."""
        if capability is None:
            return self._workflows.list()
        return self._workflows.find_by_capability(capability)

    def analyze_change(self, target: ComponentId | CapabilityId | str) -> ChangeImpact:
        """Analyze the impact of changing a component, capability, or workflow."""
        return analyze_change(target, self)

    def snapshot(self) -> SystemSnapshot:
        """Build a deterministic architecture snapshot."""
        return build_snapshot(self)
