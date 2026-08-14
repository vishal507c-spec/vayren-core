"""System model tests — the queryable architecture facade."""

import pytest

from core.contracts.capability import CapabilityId
from core.system.system_model import SystemModel
from core.system.workflow import Workflow, WorkflowRegistry, WorkflowStep
from core.tests.test_component_poc import build_system


def make_system(with_workflows: bool = True) -> SystemModel:
    workflows = None
    if with_workflows:
        workflows = WorkflowRegistry()
        workflows.register(
            Workflow(
                id="chart_pipeline",
                steps=(
                    WorkflowStep(id="load", capability=CapabilityId("data.query.candles")),
                    WorkflowStep(id="render", capability=CapabilityId("chart.render")),
                ),
            )
        )
    return SystemModel(build_system(), workflows)


def test_manifests_and_components() -> None:
    system = make_system()
    assert [manifest.identity.name for manifest in system.components()] == ["chart", "market"]
    assert system.has_component("market")
    assert not system.has_component("missing")
    assert system.find_component("chart").type == "presentation"


def test_find_component_raises_key_error() -> None:
    with pytest.raises(KeyError):
        make_system().find_component("missing")


def test_capabilities_query() -> None:
    system = make_system()
    assert [str(capability) for capability in system.capabilities()] == [
        "chart.render",
        "data.query.candles",
        "data.query.quotes",
        "data.query.timeframes",
        "data.transform.aggregate",
    ]
    found = [str(capability) for capability in system.find_capability("data.query")]
    assert found == ["data.query.candles", "data.query.quotes", "data.query.timeframes"]


def test_find_implementations_returns_objects() -> None:
    from market.repository.candle_repository import CandleRepository

    system = make_system()
    providers = system.find_implementations("data.query.candles")
    assert len(providers) == 1
    assert providers[0].component.name == "market"
    assert providers[0].implementation is CandleRepository


def test_dependency_queries() -> None:
    system = make_system()
    assert system.find_dependents("market") == ("chart",)
    assert system.find_dependencies("chart") == ("core", "market")
    assert system.find_consumers("data.query.candles") == ("chart",)
    assert system.find_consumers("chart.render") == ()


def test_workflow_queries() -> None:
    system = make_system()
    assert [workflow.id for workflow in system.find_workflows()] == ["chart_pipeline"]
    by_capability = [workflow.id for workflow in system.find_workflows("data.query.candles")]
    assert by_capability == ["chart_pipeline"]
    by_render = [workflow.id for workflow in system.find_workflows("chart.render")]
    assert by_render == ["chart_pipeline"]


def test_workflow_queries_without_workflows() -> None:
    system = make_system(with_workflows=False)
    assert system.find_workflows() == ()


def test_analyze_change_and_snapshot() -> None:
    system = make_system()
    impact = system.analyze_change("chart")
    assert impact.target == "component:chart"
    snapshot = system.snapshot()
    assert len(snapshot.components) == 2
