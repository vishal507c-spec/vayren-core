"""AI boundary tests — AI can propose, but never bypass deterministic policies."""

import pytest

from core.ai.boundary import (
    ALLOWED_ACTIONS,
    FORBIDDEN_ACTIONS,
    ActionKind,
    AiBoundary,
    BoundaryViolation,
)


def test_every_allowed_action_passes() -> None:
    boundary = AiBoundary()
    for action in ALLOWED_ACTIONS:
        decision = boundary.request(action)
        assert decision.allowed, f"{action} should be allowed"
        assert action.value in decision.reason


def test_every_forbidden_action_is_denied() -> None:
    boundary = AiBoundary()
    for action in FORBIDDEN_ACTIONS:
        decision = boundary.request(action)
        assert not decision.allowed, f"{action} must be forbidden"
        assert "forbidden by AI boundary" in decision.reason


def test_forbidden_actions_cover_the_spec() -> None:
    expected = {
        ActionKind.EXECUTE_TRADE,
        ActionKind.BYPASS_RISK,
        ActionKind.DELETE_PRODUCTION_DATA,
        ActionKind.MODIFY_PROTECTED_SYSTEM,
        ActionKind.DEPLOY_UNVALIDATED,
        ActionKind.OVERRIDE_CONTRACT,
    }
    assert expected <= FORBIDDEN_ACTIONS


def test_require_raises_for_forbidden_actions() -> None:
    boundary = AiBoundary()
    with pytest.raises(BoundaryViolation, match="forbidden by AI boundary: execute_trade"):
        boundary.require(ActionKind.EXECUTE_TRADE)
    boundary.require(ActionKind.UNDERSTAND)


def test_classify_understands_verbs() -> None:
    boundary = AiBoundary()
    assert boundary.classify("Understand the architecture") is ActionKind.UNDERSTAND
    assert boundary.classify("Compare two candidates") is ActionKind.COMPARE
    assert boundary.classify("execute trade") is ActionKind.EXECUTE_TRADE
    assert boundary.classify("deploy unvalidated code") is ActionKind.DEPLOY_UNVALIDATED
    assert boundary.classify("override contract") is ActionKind.OVERRIDE_CONTRACT


def test_unrecognized_request_fails_closed() -> None:
    boundary = AiBoundary()
    assert boundary.classify("do whatever you want") is None
    decision = boundary.request_text("do whatever you want")
    assert not decision.allowed
    assert "unrecognized action request" in decision.reason


def test_banned_verb_wins_over_allowed_verb() -> None:
    boundary = AiBoundary()
    decision = boundary.request_text("optimize and then bypass risk")
    assert not decision.allowed
    assert decision.action is ActionKind.BYPASS_RISK


def test_ai_cannot_bypass_policies_end_to_end() -> None:
    from core.ai.plan import Plan, PlanChange, PlanChangeKind
    from core.ai.plan_validator import PlanValidator, Policy
    from core.ai.sandbox import Sandbox, SandboxError
    from core.system.system_model import SystemModel
    from core.tests.helpers import build_system

    boundary = AiBoundary()
    with pytest.raises(BoundaryViolation):
        boundary.require(ActionKind.EXECUTE_TRADE)

    system = SystemModel(build_system())
    plan = Plan(
        id="modify_market",
        summary="AI wants to tune the market layer",
        components=("market",),
        changes=(PlanChange(component="market", kind=PlanChangeKind.MODIFY),),
    )
    policy = Policy(
        id="protect-market",
        description="market layer is protected",
        protected_components=("market",),
    )
    validator = PlanValidator((policy,))
    result = validator.validate(plan, system)
    assert not result.valid
    assert "protected component" in result.errors[0]

    sandbox = Sandbox(plan, system, (policy,))
    sandbox.simulate()
    sandbox.test()
    sandbox.benchmark()
    sandbox.validate()
    with pytest.raises(SandboxError, match="cannot approve an invalid plan"):
        sandbox.approve()
