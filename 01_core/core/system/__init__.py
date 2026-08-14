"""System intelligence layer — the machine-readable architecture model.

Observes and describes the registered system: components, capabilities,
dependencies, data flows, events, workflows, change impact, and
architecture snapshots. Purely observational; never modifies behavior.
"""

from core.system.capability_graph import CapabilityGraph
from core.system.change_impact import (
    ChangeImpact,
    RiskLevel,
    analyze_capability_change,
    analyze_change,
    analyze_component_change,
    analyze_workflow_change,
)
from core.system.component_graph import ComponentGraph
from core.system.data_flow import DataFlowModel
from core.system.event_graph import EventGraph
from core.system.snapshot import SystemSnapshot, build_snapshot
from core.system.system_model import SystemModel
from core.system.workflow import Workflow, WorkflowRegistry, WorkflowStep

__all__ = [
    "SystemModel",
    "ComponentGraph",
    "CapabilityGraph",
    "EventGraph",
    "DataFlowModel",
    "Workflow",
    "WorkflowRegistry",
    "WorkflowStep",
    "ChangeImpact",
    "RiskLevel",
    "analyze_change",
    "analyze_component_change",
    "analyze_capability_change",
    "analyze_workflow_change",
    "SystemSnapshot",
    "build_snapshot",
]
