"""Workflow model tests — validation, registry, and capability lookup."""

import pytest

from core.contracts.capability import CapabilityId
from core.system.workflow import Workflow, WorkflowRegistry, WorkflowStep


def step(step_id: str = "s", capability: str = "data.query") -> WorkflowStep:
    return WorkflowStep(id=step_id, capability=CapabilityId(capability))


def test_step_id_must_match_pattern() -> None:
    with pytest.raises(ValueError):
        WorkflowStep(id="Bad Id", capability=CapabilityId("data.query"))


def test_workflow_id_must_match_pattern() -> None:
    with pytest.raises(ValueError):
        Workflow(id="Bad Id")


def test_duplicate_step_ids_are_rejected() -> None:
    with pytest.raises(ValueError):
        Workflow(id="wf", steps=(step("s"), step("s")))


def test_registry_rejects_duplicates() -> None:
    registry = WorkflowRegistry()
    registry.register(Workflow(id="wf", steps=(step(),)))
    with pytest.raises(ValueError):
        registry.register(Workflow(id="wf"))


def test_registry_get_and_contains() -> None:
    registry = WorkflowRegistry()
    registry.register(Workflow(id="wf", steps=(step(),)))
    assert registry.get("wf").id == "wf"
    assert "wf" in registry
    assert "missing" not in registry
    with pytest.raises(KeyError):
        registry.get("missing")


def test_list_is_sorted() -> None:
    registry = WorkflowRegistry()
    registry.register(Workflow(id="zeta", steps=(step(),)))
    registry.register(Workflow(id="alpha", steps=(step(),)))
    assert [workflow.id for workflow in registry.list()] == ["alpha", "zeta"]


def test_find_by_capability() -> None:
    registry = WorkflowRegistry()
    registry.register(Workflow(id="a", steps=(step(capability="data.query"),)))
    registry.register(Workflow(id="b", steps=(step(capability="chart.render"),)))
    assert [workflow.id for workflow in registry.find_by_capability("data.query")] == ["a"]
    assert [
        workflow.id for workflow in registry.find_by_capability(CapabilityId("chart.render"))
    ] == ["b"]
    assert registry.find_by_capability("missing.cap") == ()


def test_len() -> None:
    registry = WorkflowRegistry()
    assert len(registry) == 0
    registry.register(Workflow(id="wf"))
    assert len(registry) == 1
