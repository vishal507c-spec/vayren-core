"""Plan model tests — structural validation and risk helpers."""

from core.ai.plan import (
    Plan,
    PlanChange,
    PlanChangeKind,
    PlanRisk,
    Rollback,
    plan_risk,
    risk_rank,
    validate_plan,
)
from core.contracts.capability import CapabilityId
from core.system.change_impact import RiskLevel


def make_plan(**overrides: object) -> Plan:
    fields = {
        "id": "add_broker",
        "summary": "add a broker component",
        "requirements": ("broker API",),
        "reused_capabilities": (CapabilityId("data.query.quotes"),),
        "new_capabilities": (CapabilityId("trade.place"),),
        "components": ("broker",),
        "changes": (PlanChange(component="broker", kind=PlanChangeKind.ADD),),
        "tests": ("test_broker.py",),
        "benchmarks": ("broker-latency",),
        "risks": (PlanRisk(level=RiskLevel.MEDIUM, reason="external API"),),
        "rollback": Rollback(steps=("remove broker",)),
    }
    fields.update(overrides)
    return Plan(**fields)


def test_valid_plan_passes() -> None:
    result = validate_plan(make_plan())
    assert result.valid
    assert result.errors == ()


def test_invalid_id_rejected() -> None:
    result = validate_plan(make_plan(id="Bad Id"))
    assert not result.valid
    assert "invalid plan id" in result.errors[0]


def test_empty_summary_rejected() -> None:
    result = validate_plan(make_plan(summary="  "))
    assert not result.valid
    assert "summary must not be empty" in result.errors


def test_reused_and_new_overlap_rejected() -> None:
    result = validate_plan(
        make_plan(
            reused_capabilities=(CapabilityId("data.query.quotes"),),
            new_capabilities=(CapabilityId("data.query.quotes"),),
        )
    )
    assert not result.valid
    assert "must not be both reused and new" in result.errors[0]


def test_changes_without_components_rejected() -> None:
    result = validate_plan(make_plan(components=(), changes=()))
    assert result.valid
    result = validate_plan(
        make_plan(components=(), changes=(PlanChange(component="x", kind=PlanChangeKind.ADD),))
    )
    assert not result.valid
    assert "changes require target components" in result.errors


def test_change_target_must_be_listed() -> None:
    plan = make_plan(
        components=("broker",),
        changes=(PlanChange(component="risk", kind=PlanChangeKind.MODIFY),),
    )
    result = validate_plan(plan)
    assert not result.valid
    assert "change target not listed in components: risk" in result.errors


def test_empty_change_component_rejected() -> None:
    plan = make_plan(
        components=("broker",),
        changes=(PlanChange(component=" ", kind=PlanChangeKind.ADD),),
    )
    result = validate_plan(plan)
    assert not result.valid
    assert "change component must not be empty" in result.errors


def test_plan_risk_returns_highest_declared() -> None:
    assert plan_risk(make_plan(risks=())) is RiskLevel.LOW
    assert plan_risk(make_plan()) is RiskLevel.MEDIUM
    assert (
        plan_risk(
            make_plan(
                risks=(
                    PlanRisk(level=RiskLevel.LOW),
                    PlanRisk(level=RiskLevel.HIGH),
                )
            )
        )
        is RiskLevel.HIGH
    )


def test_risk_rank_orders_levels() -> None:
    assert risk_rank(RiskLevel.LOW) < risk_rank(RiskLevel.MEDIUM) < risk_rank(RiskLevel.HIGH)
