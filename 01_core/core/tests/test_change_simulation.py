"""Change simulation tests — Part 2 impact analysis integrated before plans are accepted."""

from core.ai.change_simulation import simulate_plan
from core.ai.plan import Plan, PlanChange, PlanChangeKind, PlanRisk
from core.contracts.capability import CapabilityId
from core.system.change_impact import RiskLevel
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
        "id": "touch_market",
        "summary": "modify market query layer",
        "components": ("market",),
        "changes": (PlanChange(component="market", kind=PlanChangeKind.MODIFY),),
    }
    fields.update(overrides)
    return Plan(**fields)


def test_market_change_reaches_chart() -> None:
    simulation = simulate_plan(make_system(), make_plan())
    assert simulation.affected_components == ("chart", "market")
    assert "data.query.candles" in simulation.affected_capabilities
    assert "chart.render" in simulation.affected_capabilities


def test_workflow_impact_is_reported() -> None:
    simulation = simulate_plan(make_system(), make_plan())
    assert simulation.affected_workflows == ("chart_pipeline",)


def test_risk_aggregates_to_highest() -> None:
    simulation = simulate_plan(make_system(), make_plan())
    assert simulation.estimated_risk is RiskLevel.HIGH
    simulation_low = simulate_plan(
        make_system(),
        make_plan(
            components=("probe",),
            changes=(PlanChange(component="probe", kind=PlanChangeKind.ADD),),
            risks=(PlanRisk(level=RiskLevel.LOW),),
        ),
    )
    assert simulation_low.estimated_risk is RiskLevel.LOW


def test_new_component_affects_nothing() -> None:
    simulation = simulate_plan(
        make_system(),
        make_plan(
            components=("broker",),
            changes=(PlanChange(component="broker", kind=PlanChangeKind.ADD),),
            new_capabilities=(CapabilityId("trade.place"),),
        ),
    )
    assert simulation.affected_components == ("broker",)
    assert simulation.estimated_risk is RiskLevel.LOW
    assert "trade.place" in simulation.affected_capabilities


def test_required_tests_cover_every_affected_component() -> None:
    simulation = simulate_plan(make_system(), make_plan())
    assert "regression:chart" in simulation.required_tests
    assert "regression:market" in simulation.required_tests


def test_plan_tests_are_included() -> None:
    simulation = simulate_plan(make_system(), make_plan(tests=("test_quotes.py",)))
    assert "test_quotes.py" in simulation.required_tests


def test_reasons_are_deterministic() -> None:
    first = simulate_plan(make_system(), make_plan())
    second = simulate_plan(make_system(), make_plan())
    assert first.reasons == second.reasons
    assert first.reasons
