"""AI context builder tests — deterministic context for providers, offline-safe."""

import pytest

from core.ai.context import SECTIONS, ContextBuilder, ContextRequest
from core.contracts.capability import CapabilityId
from core.system.system_model import SystemModel
from core.system.workflow import Workflow, WorkflowRegistry, WorkflowStep
from core.tests.test_component_poc import build_system


def make_system() -> SystemModel:
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


def make_builder() -> ContextBuilder:
    return ContextBuilder(make_system())


def test_default_build_contains_every_section() -> None:
    context = make_builder().build()
    assert tuple(context.sections) == SECTIONS


def test_sections_follow_canonical_order() -> None:
    context = make_builder().build()
    expected = (
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
    assert tuple(context.sections) == expected
    assert expected == SECTIONS


def test_empty_request_produces_empty_context() -> None:
    context = make_builder().build(ContextRequest(sections=()))
    assert context.render() == ""
    assert context.sections == {}


def test_subset_request_renders_only_requested() -> None:
    context = make_builder().build(ContextRequest(sections=("components", "events")))
    assert tuple(context.sections) == ("components", "events")
    rendered = context.render()
    assert "market" in rendered
    assert "event bus" not in rendered.lower()


def test_unknown_section_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown context sections: secrets"):
        ContextRequest(sections=("secrets",))


def test_render_contains_component_and_capability_ground_truth() -> None:
    rendered = make_builder().build().render()
    assert "market" in rendered
    assert "data.query.candles" in rendered
    assert "chart" in rendered
    assert "chart.render" in rendered


def test_system_state_reports_live_counts_and_gaps() -> None:
    context = make_builder().build()
    assert "components: 2" in context.sections["system_state"]
    assert "capabilities: 5" in context.sections["system_state"]
    assert "gap (unproduced_events)" in context.sections["system_state"]


def test_architecture_history_renders_without_memory() -> None:
    context = make_builder().build()
    assert "engineering memory not available" in context.sections["architecture_history"]


def test_dependencies_are_explicit() -> None:
    context = make_builder().build()
    assert "depends on core" in context.sections["dependencies"]
    assert "depends on core, market" in context.sections["dependencies"]


def test_workflows_are_enumerated() -> None:
    context = make_builder().build()
    assert "chart_pipeline" in context.sections["workflows"]
    assert "chart.render" in context.sections["workflows"]


def test_to_json_is_valid_and_section_keyed() -> None:
    import json

    payload = make_builder().build().to_json()
    data = json.loads(payload)
    assert set(data) == set(SECTIONS)


def test_build_is_deterministic() -> None:
    builder = make_builder()
    assert builder.build().render() == builder.build().render()
