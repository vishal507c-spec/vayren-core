"""Change impact analysis tests — risk levels and reasons."""

from core.contracts.capability import CapabilityDecl, CapabilityId
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.manifest import ComponentManifest
from core.registry.component_registry import ComponentRegistry
from core.system.change_impact import (
    RiskLevel,
    analyze_capability_change,
    analyze_change,
    analyze_component_change,
    analyze_workflow_change,
)
from core.system.system_model import SystemModel
from core.system.workflow import Workflow, WorkflowRegistry, WorkflowStep
from core.tests.helpers import build_system


def make_manifest(
    name: str,
    dependencies: tuple[str, ...] = (),
) -> ComponentManifest:
    return ComponentManifest(
        identity=ComponentId(name),
        version=ComponentVersion.parse("1.0.0"),
        type="service",
        dependencies=tuple(ComponentId(dep) for dep in dependencies),
    )


def make_system() -> SystemModel:
    workflows = WorkflowRegistry()
    workflows.register(
        Workflow(
            id="data_pipeline",
            description="load candles, download history",
            steps=(
                WorkflowStep(
                    id="load",
                    capability=CapabilityId("data.query.candles"),
                    outputs=("candles",),
                ),
                WorkflowStep(
                    id="ingest",
                    capability=CapabilityId("historical_data.download"),
                    outputs=("new_rows",),
                ),
            ),
        )
    )
    return SystemModel(build_system(), workflows)


def probe_system() -> SystemModel:
    registry = ComponentRegistry()
    registry.register(
        ComponentManifest(
            identity=ComponentId("probe"),
            version=ComponentVersion.parse("1.0.0"),
            type="service",
            capabilities=(CapabilityDecl(id=CapabilityId("probe.run")),),
        ),
        implementations={"probe.run": object},
    )
    return SystemModel(registry)


def test_component_used_by_workflow_is_high_risk() -> None:
    impact = analyze_component_change("historical_data", make_system())
    assert impact.risk is RiskLevel.HIGH
    assert impact.affected_workflows == ("data_pipeline",)
    assert "used by workflows: data_pipeline" in impact.reasons


def test_component_with_indirect_dependents_is_high_risk() -> None:
    registry = ComponentRegistry()
    registry.register(make_manifest("leaf"), implementations={})
    registry.register(make_manifest("middle", ("leaf",)), implementations={})
    registry.register(make_manifest("root", ("middle",)), implementations={})
    impact = analyze_component_change("leaf", SystemModel(registry))
    assert impact.risk is RiskLevel.HIGH
    assert impact.direct_dependents == ("middle",)
    assert impact.indirect_dependents == ("root",)


def test_component_with_direct_dependents_only_is_medium_risk() -> None:
    registry = ComponentRegistry()
    registry.register(make_manifest("store"), implementations={})
    registry.register(make_manifest("cache", ("store",)), implementations={})
    impact = analyze_component_change("store", SystemModel(registry))
    assert impact.risk is RiskLevel.MEDIUM
    assert impact.direct_dependents == ("cache",)
    assert impact.indirect_dependents == ()
    assert "direct dependents: cache" in impact.reasons


def test_unused_component_is_low_risk() -> None:
    impact = analyze_component_change("probe", probe_system())
    assert impact.risk is RiskLevel.LOW
    assert "no dependents, consumers, or workflows" in impact.reasons[0]


def test_capability_used_by_workflow_is_high_risk() -> None:
    impact = analyze_capability_change("data.query.candles", make_system())
    assert impact.risk is RiskLevel.HIGH
    assert impact.affected_workflows == ("data_pipeline",)


def test_capability_with_consumers_only_is_medium_risk() -> None:
    registry = build_system()
    registry.register(
        ComponentManifest(
            identity=ComponentId("audit"),
            version=ComponentVersion.parse("1.0.0"),
            type="service",
            capabilities_consumed=(CapabilityId("data.query.candles"),),
        ),
        implementations={},
    )
    impact = analyze_capability_change("data.query.candles", SystemModel(registry))
    assert impact.risk is RiskLevel.MEDIUM
    assert impact.affected_consumers == ("audit",)


def test_capability_without_consumers_is_low_risk() -> None:
    impact = analyze_capability_change("data.query.quotes", SystemModel(build_system()))
    assert impact.risk is RiskLevel.LOW
    assert "implemented but not consumed" in impact.reasons[0]


def test_unknown_capability_is_low_risk() -> None:
    impact = analyze_capability_change("missing.cap", SystemModel(build_system()))
    assert impact.risk is RiskLevel.LOW
    assert "no provider, consumer, or workflow" in impact.reasons[0]


def test_workflow_change_is_low_risk() -> None:
    impact = analyze_workflow_change("data_pipeline", make_system())
    assert impact.risk is RiskLevel.LOW
    assert impact.affected_workflows == ("data_pipeline",)


def test_analyze_change_dispatch() -> None:
    system = make_system()
    assert analyze_change("data.query.candles", system).target == "capability:data.query.candles"
    assert analyze_change("data_pipeline", system).target == "workflow:data_pipeline"
    assert analyze_change("historical_data", system).target == "component:historical_data"
