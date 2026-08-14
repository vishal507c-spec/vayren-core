"""Sandbox model — isolated experimentation before any deployment.

A proposed change moves through a strict, non-skippable stage sequence:

    PLAN → SANDBOX → TEST → BENCHMARK → VALIDATE → APPROVE → DEPLOY

Nothing is ever executed here: deployment is a recorded decision and the
deterministic VAYREN runtime remains authoritative. There is no
self-modifying loop.
"""

from dataclasses import dataclass
from enum import Enum

from core.ai.change_simulation import ChangeSimulation, simulate_plan
from core.ai.plan import Plan, PlanValidationResult
from core.ai.plan_validator import PlanValidator, Policy
from core.system.system_model import SystemModel


class SandboxStage(Enum):
    """Strict lifecycle stage of a sandboxed plan."""

    PLAN = "plan"
    SANDBOX = "sandbox"
    TEST = "test"
    BENCHMARK = "benchmark"
    VALIDATE = "validate"
    APPROVE = "approve"
    DEPLOY = "deploy"


_STAGE_ORDER: tuple[SandboxStage, ...] = tuple(SandboxStage)


class SandboxError(ValueError):
    """Raised when a sandbox transition is not allowed."""


@dataclass(frozen=True)
class SandboxDeployment:
    """Recorded outcome of an approved deployment. Nothing executes."""

    plan_id: str
    stage: SandboxStage = SandboxStage.DEPLOY
    note: str = "deployment is a recorded decision; the deterministic runtime remains authoritative"


class Sandbox:
    """Deterministic lifecycle for one plan. No stage can be skipped."""

    def __init__(
        self,
        plan: Plan,
        system: SystemModel,
        policies: tuple[Policy, ...] = (),
    ) -> None:
        self._plan = plan
        self._system = system
        self._validator = PlanValidator(policies)
        self._stage = SandboxStage.PLAN
        self._simulation: ChangeSimulation | None = None
        self._validation: PlanValidationResult | None = None
        self._history: list[str] = [SandboxStage.PLAN.value]

    @property
    def plan(self) -> Plan:
        """The plan under evaluation."""
        return self._plan

    @property
    def stage(self) -> SandboxStage:
        """The current lifecycle stage."""
        return self._stage

    @property
    def simulation(self) -> ChangeSimulation | None:
        """The change simulation, once calculated."""
        return self._simulation

    @property
    def validation(self) -> PlanValidationResult | None:
        """The policy validation result, once calculated."""
        return self._validation

    @property
    def history(self) -> tuple[str, ...]:
        """Every stage the plan has passed through, in order."""
        return tuple(self._history)

    def simulate(self) -> ChangeSimulation:
        """Calculate change impact and move from PLAN to SANDBOX."""
        self._require(SandboxStage.PLAN)
        self._simulation = simulate_plan(self._system, self._plan)
        self._advance()
        return self._simulation

    def test(self) -> None:
        """Move from SANDBOX to TEST after the simulation passed."""
        self._require(SandboxStage.SANDBOX)
        self._advance()

    def benchmark(self) -> None:
        """Move from TEST to BENCHMARK."""
        self._require(SandboxStage.TEST)
        self._advance()

    def validate(self) -> PlanValidationResult:
        """Run policy validation and move from BENCHMARK to VALIDATE."""
        self._require(SandboxStage.BENCHMARK)
        self._validation = self._validator.validate(self._plan, self._system)
        self._advance()
        return self._validation

    def approve(self) -> None:
        """Approve only a validated plan, moving from VALIDATE to APPROVE."""
        self._require(SandboxStage.VALIDATE)
        if self._validation is None or not self._validation.valid:
            reasons = "; ".join(self._validation.errors) if self._validation else "not validated"
            raise SandboxError(f"cannot approve an invalid plan: {reasons}")
        self._advance()

    def deploy(self) -> SandboxDeployment:
        """Record the deployment decision, moving from APPROVE to DEPLOY."""
        self._require(SandboxStage.APPROVE)
        deployment = SandboxDeployment(plan_id=self._plan.id)
        self._advance()
        return deployment

    def _require(self, expected: SandboxStage) -> None:
        if self._stage is not expected:
            msg = (
                f"cannot transition to {expected.value} from {self._stage.value}; "
                "stages cannot be skipped"
            )
            raise SandboxError(msg)

    def _advance(self) -> None:
        index = _STAGE_ORDER.index(self._stage) + 1
        self._stage = _STAGE_ORDER[index]
        self._history.append(self._stage.value)
