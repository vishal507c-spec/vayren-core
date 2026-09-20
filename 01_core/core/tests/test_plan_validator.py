"""Plan validator tests — deterministic policy enforcement.

The core guarantee: AI can propose anything, but deterministic policies
decide. A proposal to modify a protected component must be rejected.
"""

from dataclasses import replace

from core.ai.plan import (
    Plan,
    PlanChange,
    PlanChangeKind,
    PlanRisk,
    Rollback,
)
from core.ai.plan_validator import PlanValidator, Policy
from core.contracts.capability import CapabilityId
from core.system.change_impact import RiskLevel
from core.system.system_model import SystemModel
from core.tests.helpers import build_system


def system() -> SystemModel:
    return SystemModel(build_system())


def make_plan(**overrides: object) -> Plan:
    fields = {
        "id": "modify_market",
        "summary": "tune the market query layer",
        "reused_capabilities": (CapabilityId("data.query.candles"),),
        "components": ("market",),
        "changes": (PlanChange(component="market", kind=PlanChangeKind.MODIFY),),
    }
    fields.update(overrides)
    return Plan(**fields)


def protected_policy() -> Policy:
    return Policy(
        id="protect-critical",
        description="market layer is protected",
        protected_components=("market",),
    )


def test_valid_plan_passes_without_policies() -> None:
    plan = make_plan(id="add_broker", summary="add broker", components=("broker",), changes=())
    result = PlanValidator().validate(plan, system())
    assert result.valid


def test_modifying_protected_component_is_rejected() -> None:
    plan = make_plan()
    result = PlanValidator((protected_policy(),)).validate(plan, system())
    assert not result.valid
    assert "protected component (policy protect-critical): market" in result.errors


def test_reused_capability_must_be_provided() -> None:
    plan = make_plan(
        id="add_broker",
        summary="add broker",
        components=("broker",),
        changes=(),
        reused_capabilities=(CapabilityId("trade.place"),),
    )
    result = PlanValidator().validate(plan, system())
    assert not result.valid
    assert "reused capability not provided: trade.place" in result.errors


def test_existing_capability_cannot_be_created_again() -> None:
    plan = make_plan(
        id="add_broker",
        summary="add broker",
        components=("broker",),
        changes=(),
        new_capabilities=(CapabilityId("data.query.candles"),),
    )
    result = PlanValidator().validate(plan, system())
    assert not result.valid
    assert "capability already provided: data.query.candles" in result.errors


def test_modify_unknown_component_rejected() -> None:
    plan = make_plan(
        components=("ghost",),
        changes=(PlanChange(component="ghost", kind=PlanChangeKind.MODIFY),),
    )
    result = PlanValidator().validate(plan, system())
    assert not result.valid
    assert "unknown component: ghost" in result.errors


def test_add_unknown_component_is_allowed() -> None:
    plan = make_plan(
        id="add_broker",
        summary="add a brand new broker component",
        components=("broker",),
        changes=(PlanChange(component="broker", kind=PlanChangeKind.ADD),),
    )
    result = PlanValidator().validate(plan, system())
    assert result.valid


def test_forbidden_capability_rejected() -> None:
    policy = Policy(
        id="no-trading",
        description="no trade execution capabilities",
        forbidden_capabilities=(CapabilityId("trade.place"),),
    )
    plan = make_plan(
        id="add_broker",
        summary="add broker",
        components=("broker",),
        changes=(),
        new_capabilities=(CapabilityId("trade.place"),),
    )
    result = PlanValidator((policy,)).validate(plan, system())
    assert not result.valid
    assert "forbidden capability (policy no-trading): trade.place" in result.errors


def test_change_kind_not_allowed_rejected() -> None:
    policy = Policy(
        id="no-removals",
        description="components may never be removed",
        allowed_change_kinds=(PlanChangeKind.ADD, PlanChangeKind.MODIFY),
    )
    plan = make_plan(
        id="remove_market",
        summary="remove market",
        components=("market",),
        changes=(PlanChange(component="market", kind=PlanChangeKind.REMOVE),),
    )
    result = PlanValidator((policy,)).validate(plan, system())
    assert not result.valid
    assert "change kind not allowed by policy no-removals: remove" in result.errors


def test_risk_exceeding_policy_limit_rejected() -> None:
    policy = Policy(id="low-risk-only", description="low risk only", max_risk=RiskLevel.LOW)
    plan = make_plan(
        id="add_broker",
        summary="add broker",
        components=("broker",),
        changes=(),
        risks=(PlanRisk(level=RiskLevel.MEDIUM, reason="external dependency"),),
    )
    result = PlanValidator((policy,)).validate(plan, system())
    assert not result.valid
    assert "risk medium exceeds policy limit low (policy low-risk-only)" in result.errors


def test_rollback_required_above_threshold() -> None:
    policy = Policy(
        id="rollback-needed",
        description="medium risk requires rollback",
        requires_rollback_above=RiskLevel.MEDIUM,
    )
    plan = make_plan(
        id="add_broker",
        summary="add broker",
        components=("broker",),
        changes=(),
        risks=(PlanRisk(level=RiskLevel.MEDIUM),),
        rollback=Rollback(),
    )
    result = PlanValidator((policy,)).validate(plan, system())
    assert not result.valid
    assert "rollback required above medium (policy rollback-needed)" in result.errors

    plan_with_rollback = replace(plan, rollback=Rollback(steps=("remove broker",)))
    assert PlanValidator((policy,)).validate(plan_with_rollback, system()).valid


def test_multiple_policies_apply_together() -> None:
    plan = make_plan(
        risks=(PlanRisk(level=RiskLevel.MEDIUM, reason="external dependency"),),
    )
    result = PlanValidator(
        (protected_policy(), Policy(id="strict", description="strict", max_risk=RiskLevel.LOW))
    ).validate(plan, system())
    assert not result.valid
    assert any("protected component" in error for error in result.errors)
    assert any("risk" in error for error in result.errors)
