"""Sandbox tests — strict lifecycle, no skipped stages, no self-modification."""

import pytest

from core.ai.plan import Plan, PlanChange, PlanChangeKind
from core.ai.plan_validator import Policy
from core.ai.sandbox import Sandbox, SandboxDeployment, SandboxError, SandboxStage
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


def make_plan(**overrides: object) -> Plan:
    fields = {
        "id": "add_broker",
        "summary": "add a broker component",
        "components": ("broker",),
        "changes": (PlanChange(component="broker", kind=PlanChangeKind.ADD),),
    }
    fields.update(overrides)
    return Plan(**fields)


def run_to_validate(sandbox: Sandbox) -> None:
    sandbox.simulate()
    sandbox.test()
    sandbox.benchmark()
    sandbox.validate()


def test_happy_path_reaches_deploy() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    run_to_validate(sandbox)
    sandbox.approve()
    deployment = sandbox.deploy()
    assert sandbox.stage is SandboxStage.DEPLOY
    assert isinstance(deployment, SandboxDeployment)
    assert deployment.plan_id == "add_broker"
    assert deployment.stage is SandboxStage.DEPLOY


def test_deployment_is_a_record_not_execution() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    run_to_validate(sandbox)
    sandbox.approve()
    deployment = sandbox.deploy()
    assert "deterministic runtime remains authoritative" in deployment.note
    assert "recorded decision" in deployment.note


def test_stages_cannot_be_skipped() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    with pytest.raises(SandboxError, match="stages cannot be skipped"):
        sandbox.test()
    with pytest.raises(SandboxError, match="stages cannot be skipped"):
        sandbox.approve()
    with pytest.raises(SandboxError, match="stages cannot be skipped"):
        sandbox.deploy()


def test_validate_requires_benchmark() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    sandbox.simulate()
    with pytest.raises(SandboxError, match="stages cannot be skipped"):
        sandbox.validate()


def test_approve_requires_validate() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    sandbox.simulate()
    sandbox.test()
    sandbox.benchmark()
    with pytest.raises(SandboxError, match="stages cannot be skipped"):
        sandbox.approve()


def test_invalid_plan_cannot_be_approved() -> None:
    policies = (
        Policy(
            id="no-broker",
            description="broker is protected",
            protected_components=("broker",),
        ),
    )
    sandbox = Sandbox(make_plan(), make_system(), policies)
    run_to_validate(sandbox)
    validation = sandbox.validation
    assert validation is not None
    assert not validation.valid
    with pytest.raises(SandboxError, match="cannot approve an invalid plan"):
        sandbox.approve()


def test_simulate_returns_simulation_and_advances() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    simulation = sandbox.simulate()
    assert sandbox.stage is SandboxStage.SANDBOX
    assert simulation.affected_components == ("broker",)


def test_history_records_every_transition() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    run_to_validate(sandbox)
    sandbox.approve()
    sandbox.deploy()
    assert sandbox.history == tuple(stage.value for stage in SandboxStage)


def test_simulation_and_validation_are_lazy() -> None:
    sandbox = Sandbox(make_plan(), make_system())
    assert sandbox.simulation is None
    assert sandbox.validation is None
