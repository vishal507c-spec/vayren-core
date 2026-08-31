"""Plan validator — deterministic policy enforcement on AI-generated plans.

Before any plan can be executed it must pass structural checks plus every
configured policy: contracts, capabilities, dependencies, permissions, and
risk. Invalid plans are rejected with explicit reasons.
"""

from dataclasses import dataclass

from core.ai.plan import (
    Plan,
    PlanChangeKind,
    PlanValidationResult,
    plan_risk,
    risk_rank,
    validate_plan,
)
from core.contracts.capability import CapabilityId
from core.system.change_impact import RiskLevel
from core.system.system_model import SystemModel


@dataclass(frozen=True)
class Policy:
    """Deterministic policy applied to every plan."""

    id: str
    description: str
    protected_components: tuple[str, ...] = ()
    forbidden_capabilities: tuple[CapabilityId, ...] = ()
    max_risk: RiskLevel = RiskLevel.HIGH
    allowed_change_kinds: tuple[PlanChangeKind, ...] = (
        PlanChangeKind.ADD,
        PlanChangeKind.MODIFY,
        PlanChangeKind.REMOVE,
    )
    requires_rollback_above: RiskLevel | None = None


class PlanValidator:
    """Validates a plan against the system model and configured policies."""

    def __init__(self, policies: tuple[Policy, ...] = ()) -> None:
        self._policies = policies

    @property
    def policies(self) -> tuple[Policy, ...]:
        """The policies this validator enforces."""
        return self._policies

    def validate(self, plan: Plan, system: SystemModel) -> PlanValidationResult:
        """Validate the plan structurally, against the system, and against policies."""
        errors = list(validate_plan(plan).errors)
        risk = plan_risk(plan)
        for capability in plan.reused_capabilities:
            if not system.capability_graph.providers(str(capability)):
                errors.append(f"reused capability not provided: {capability}")
        for capability in plan.new_capabilities:
            if str(capability) in system.capability_graph.capabilities():
                errors.append(f"capability already provided: {capability}")
        for change in plan.changes:
            if change.kind is not PlanChangeKind.ADD and not system.has_component(change.component):
                errors.append(f"unknown component: {change.component}")
        for policy in self._policies:
            errors.extend(self._check_policy(plan, policy, risk))
        return PlanValidationResult(valid=not errors, errors=tuple(errors))

    def _check_policy(self, plan: Plan, policy: Policy, risk: RiskLevel) -> list[str]:
        errors: list[str] = []
        for change in plan.changes:
            if change.component in policy.protected_components:
                errors.append(f"protected component (policy {policy.id}): {change.component}")
            if change.kind not in policy.allowed_change_kinds:
                errors.append(f"change kind not allowed by policy {policy.id}: {change.kind.value}")
        forbidden = {str(cap) for cap in policy.forbidden_capabilities}
        for capability in (*plan.reused_capabilities, *plan.new_capabilities):
            if str(capability) in forbidden:
                errors.append(f"forbidden capability (policy {policy.id}): {capability}")
        if risk_rank(risk) > risk_rank(policy.max_risk):
            errors.append(
                f"risk {risk.value} exceeds policy limit {policy.max_risk.value} "
                f"(policy {policy.id})"
            )
        if (
            policy.requires_rollback_above is not None
            and risk_rank(risk) >= risk_rank(policy.requires_rollback_above)
            and not plan.rollback.steps
        ):
            errors.append(
                f"rollback required above {policy.requires_rollback_above.value} "
                f"(policy {policy.id})"
            )
        return errors
