"""Plan model — the structured representation of a proposed change.

AI may eventually generate this plan; VAYREN must be able to validate it
independently. A plan is declarative: it describes what would change, it
never executes anything.
"""

import re
from dataclasses import dataclass
from enum import Enum

from core.contracts.capability import CapabilityId
from core.system.change_impact import RiskLevel

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

_RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
_RISK_BY_RANK = {0: RiskLevel.LOW, 1: RiskLevel.MEDIUM, 2: RiskLevel.HIGH}


class PlanValidationError(ValueError):
    """Raised when a plan cannot be represented deterministically."""


@dataclass(frozen=True)
class PlanValidationResult:
    """Result of validating a plan's structure."""

    valid: bool
    errors: tuple[str, ...] = ()

    def raise_if_invalid(self) -> None:
        """Raise PlanValidationError when the plan is invalid."""
        if not self.valid:
            msg = "Invalid plan: " + "; ".join(self.errors)
            raise PlanValidationError(msg)


class PlanChangeKind(Enum):
    """Kind of change a plan proposes for a component."""

    ADD = "add"
    MODIFY = "modify"
    REMOVE = "remove"


@dataclass(frozen=True)
class PlanChange:
    """One component change within a plan."""

    component: str
    kind: PlanChangeKind
    description: str = ""


@dataclass(frozen=True)
class PlanRisk:
    """A risk declared by the plan."""

    level: RiskLevel
    reason: str = ""


@dataclass(frozen=True)
class Rollback:
    """Deterministic rollback steps for a plan."""

    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """Declarative description of a proposed change."""

    id: str
    summary: str
    requirements: tuple[str, ...] = ()
    reused_capabilities: tuple[CapabilityId, ...] = ()
    new_capabilities: tuple[CapabilityId, ...] = ()
    components: tuple[str, ...] = ()
    changes: tuple[PlanChange, ...] = ()
    tests: tuple[str, ...] = ()
    benchmarks: tuple[str, ...] = ()
    risks: tuple[PlanRisk, ...] = ()
    rollback: Rollback = Rollback()


def risk_rank(level: RiskLevel) -> int:
    """Rank of a risk level (LOW < MEDIUM < HIGH)."""
    return _RISK_RANK[level]


def plan_risk(plan: Plan) -> RiskLevel:
    """The highest risk declared by the plan (LOW when none are declared)."""
    highest = max((risk_rank(risk.level) for risk in plan.risks), default=0)
    return _RISK_BY_RANK[highest]


def validate_plan(plan: Plan) -> PlanValidationResult:
    """Validate the structural integrity of a plan."""
    errors: list[str] = []
    if not _ID_PATTERN.match(plan.id):
        errors.append(f"invalid plan id: {plan.id!r}")
    if not plan.summary.strip():
        errors.append("summary must not be empty")
    overlap = {str(cap) for cap in plan.new_capabilities} & {
        str(cap) for cap in plan.reused_capabilities
    }
    if overlap:
        errors.append("capability must not be both reused and new: " + ", ".join(sorted(overlap)))
    if plan.changes and not plan.components:
        errors.append("changes require target components")
    for change in plan.changes:
        if not change.component.strip():
            errors.append("change component must not be empty")
        elif change.component not in plan.components:
            errors.append(f"change target not listed in components: {change.component}")
    return PlanValidationResult(valid=not errors, errors=tuple(errors))
