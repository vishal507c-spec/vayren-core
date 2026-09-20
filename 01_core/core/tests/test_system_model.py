"""System model tests — the queryable architecture facade."""

import pytest

from core.contracts.capability import CapabilityId
from core.system.system_model import SystemModel
from core.system.workflow import Workflow, WorkflowRegistry, WorkflowStep
from core.tests.helpers import build_system


def make_system(with_workflows: bool = True) -> SystemModel:
    workflows = None
    if with_workflows:
        workflows = WorkflowRegistry()
        workflows.register(
            Workflow(
                id="data_pipeline",
                steps=(
                    WorkflowStep(id="load", capability=CapabilityId("data.query.candles")),
                    WorkflowStep(id="ingest", capability=CapabilityId("historical_data.download")),
                ),
            )
        )
    return SystemModel(build_system(), workflows)


def test_manifests_and_components() -> None:
    system = make_system()
    assert [manifest.identity.name for manifest in system.components()] == [
        "historical_data",
        "market",
    ]
    assert system.has_component("market")
    assert not system.has_component("missing")
    assert system.find_component("historical_data").type == "ingest"


def test_find_component_raises_key_error() -> None:
    with pytest.raises(KeyError):
        make_system().find_component("missing")


def test_capabilities_query() -> None:
    system = make_system()
    assert [str(capability) for capability in system.capabilities()] == [
        "data.query.candles",
        "data.query.quotes",
        "data.query.timeframes",
        "data.transform.aggregate",
        "historical_data.coverage",
        "historical_data.download",
        "historical_data.status",
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
    assert system.find_dependents("market") == ()
    assert system.find_dependencies("historical_data") == ("core",)
    assert system.find_consumers("data.query.candles") == ()
    assert system.find_consumers("historical_data.download") == ()


def test_workflow_queries() -> None:
    system = make_system()
    assert [workflow.id for workflow in system.find_workflows()] == ["data_pipeline"]
    by_capability = [workflow.id for workflow in system.find_workflows("data.query.candles")]
    assert by_capability == ["data_pipeline"]
    by_ingest = [workflow.id for workflow in system.find_workflows("historical_data.download")]
    assert by_ingest == ["data_pipeline"]


def test_workflow_queries_without_workflows() -> None:
    system = make_system(with_workflows=False)
    assert system.find_workflows() == ()


def test_analyze_change_and_snapshot() -> None:
    system = make_system()
    impact = system.analyze_change("historical_data")
    assert impact.target == "component:historical_data"
    snapshot = system.snapshot()
    assert len(snapshot.components) == 2
