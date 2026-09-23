"""Change simulation — impact analysis before a plan is accepted.

Integrates Part 2 change-impact analysis: for every target component the
affected components, affected capabilities, affected workflows, required
tests, and estimated risk are calculated before a plan can be accepted.
"""

from dataclasses import dataclass

from core.ai.plan import Plan, plan_risk, risk_rank

# DEBT: retained unwired imports (see 90_brain/ai_memory.md).
from core.system.change_impact import RiskLevel  # pyright: ignore[reportMissingImports]
from core.system.system_model import SystemModel  # pyright: ignore[reportMissingImports]


@dataclass(frozen=True)
class ChangeSimulation:
    """Calculated impact of a plan before it is accepted."""

    affected_components: tuple[str, ...]
    affected_capabilities: tuple[str, ...]
    affected_workflows: tuple[str, ...]
    required_tests: tuple[str, ...]
    estimated_risk: RiskLevel
    reasons: tuple[str, ...]


def simulate_plan(system: SystemModel, plan: Plan) -> ChangeSimulation:
    """Simulate every target component of the plan and aggregate the impact."""
    affected: set[str] = set(plan.components)
    capabilities: set[str] = set()
    workflows: set[str] = set()
    reasons: set[str] = set()
    highest = plan_risk(plan)
    for component in plan.components:
        impact = system.analyze_change(component)
        affected.update(impact.direct_dependents)
        affected.update(impact.indirect_dependents)
        capabilities.update(impact.affected_capabilities)
        workflows.update(impact.affected_workflows)
        reasons.update(impact.reasons)
        if risk_rank(impact.risk) > risk_rank(highest):
            highest = impact.risk
    capabilities.update(str(capability) for capability in plan.new_capabilities)
    tests = set(plan.tests)
    tests.update(f"regression:{component}" for component in affected)
    return ChangeSimulation(
        affected_components=tuple(sorted(affected)),
        affected_capabilities=tuple(sorted(capabilities)),
        affected_workflows=tuple(sorted(workflows)),
        required_tests=tuple(sorted(tests)),
        estimated_risk=highest,
        reasons=tuple(sorted(reasons)),
    )
