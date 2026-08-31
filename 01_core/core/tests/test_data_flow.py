"""Data flow model tests — inputs, outputs, and workflow paths."""

from core.contracts.capability import CapabilityId
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.manifest import ComponentManifest
from core.system.data_flow import DataFlowModel
from core.system.workflow import Workflow, WorkflowRegistry, WorkflowStep


def manifest(
    name: str,
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
) -> ComponentManifest:
    return ComponentManifest(
        identity=ComponentId(name),
        version=ComponentVersion.parse("1.0.0"),
        type="service",
        inputs=inputs,
        outputs=outputs,
    )


def test_inputs_and_outputs() -> None:
    model = DataFlowModel(
        (
            manifest("market", outputs=("candles: tuple[Bar, ...]",)),
            manifest(
                "chart",
                inputs=("bars: tuple[Bar, ...]",),
                outputs=("model: ChartModel",),
            ),
        )
    )
    assert model.inputs("chart") == ("bars: tuple[Bar, ...]",)
    assert model.outputs("market") == ("candles: tuple[Bar, ...]",)
    assert model.inputs("market") == ()


def test_components_includes_only_declared() -> None:
    model = DataFlowModel((manifest("market", outputs=("x",)), manifest("silent")))
    assert model.components() == ("market",)


def test_data_path_from_workflow() -> None:
    workflow = Workflow(
        id="build_chart",
        steps=(
            WorkflowStep(
                id="load",
                capability=CapabilityId("data.query"),
                outputs=("candles",),
            ),
            WorkflowStep(
                id="render",
                capability=CapabilityId("chart.render"),
                outputs=("model",),
            ),
        ),
    )
    model = DataFlowModel((), WorkflowRegistry())
    assert model.data_path(workflow) == (
        ("load", "data.query", "candles"),
        ("render", "chart.render", "model"),
    )


def test_workflow_paths_ordered_by_id() -> None:
    registry = WorkflowRegistry()
    registry.register(
        Workflow(
            id="beta",
            steps=(WorkflowStep(id="s", capability=CapabilityId("cap.beta")),),
        )
    )
    registry.register(
        Workflow(
            id="alpha",
            steps=(
                WorkflowStep(
                    id="s",
                    capability=CapabilityId("cap.alpha"),
                    outputs=("out",),
                ),
            ),
        )
    )
    model = DataFlowModel((), registry)
    assert model.workflow_paths() == (
        ("alpha", (("s", "cap.alpha", "out"),)),
        ("beta", ()),
    )


def test_without_workflow_registry() -> None:
    model = DataFlowModel((manifest("market", outputs=("x",)),))
    assert model.workflow_paths() == ()
