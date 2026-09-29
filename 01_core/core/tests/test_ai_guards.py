"""AI-layer guard pins (boundary, providers, intent, plan, context, sandbox)."""

from __future__ import annotations

import sys
from pathlib import Path

CHAPTER = Path(__file__).resolve().parents[2]
if str(CHAPTER) not in sys.path:
    sys.path.insert(0, str(CHAPTER))

import pytest  # noqa: E402

from core.ai._vocab import SystemModel  # noqa: E402
from core.ai.boundary import ActionKind, AiBoundary, BoundaryViolation  # noqa: E402
from core.ai.context import AiContext, ContextBuilder, ContextRequest  # noqa: E402
from core.ai.intent import (  # noqa: E402
    Intent,  # noqa: E402
    IntentKind,
    classify,
    parse_intent,
    validate_intent,
)
from core.ai.plan import Plan, validate_plan  # noqa: E402
from core.ai.providers import AiProviderRegistry, OfflineProvider  # noqa: E402
from core.ai.sandbox import Sandbox, SandboxError  # noqa: E402


def test_request_rejects_non_action_without_crashing() -> None:
    decision = AiBoundary().request("execute_trade")  # type: ignore[arg-type]
    assert decision.allowed is False
    assert decision.action is None


def test_classify_uses_token_boundaries() -> None:
    assert AiBoundary().classify("planned maintenance") is None
    assert AiBoundary().classify("please plan the rollout") is ActionKind.PLAN
    assert AiBoundary().classify("execute trade now") is ActionKind.EXECUTE_TRADE


def test_boundary_violation_is_not_oserror() -> None:
    assert issubclass(BoundaryViolation, RuntimeError)
    assert not issubclass(BoundaryViolation, OSError)
    with pytest.raises(BoundaryViolation):
        AiBoundary().require(ActionKind.EXECUTE_TRADE)


def test_select_falls_back_when_preferred_unavailable() -> None:
    registry = AiProviderRegistry()
    assert registry.select(preferred="offline") is None  # only offline: nothing to fall back to

    class _Up(OfflineProvider):
        @property
        def name(self) -> str:
            return "up"

        def available(self) -> bool:
            return True

    registry.register(_Up())
    assert registry.select(preferred="offline") is not None
    assert registry.select(preferred="offline").name == "up"  # type: ignore[union-attr]


def test_empty_goal_classifies_other_and_fails_validation() -> None:
    assert classify("") is IntentKind.OTHER
    assert classify("   ") is IntentKind.OTHER
    result = validate_intent(Intent(goal="   "))
    assert result.valid is False
    with pytest.raises(Exception, match="goal must not be empty"):
        parse_intent("   ")


def test_plan_id_length_capped() -> None:
    plan = Plan(id="p" * 129, summary="too long")
    errors = validate_plan(plan).errors
    assert any("128" in error for error in errors)


def test_sandbox_terminal_advance_is_sandbox_error() -> None:
    sandbox = Sandbox.__new__(Sandbox)
    from core.ai.sandbox import SandboxStage

    sandbox._stage = SandboxStage.DEPLOY
    sandbox._history = [stage.value for stage in SandboxStage]
    with pytest.raises(SandboxError, match="terminal"):
        sandbox._advance()


def test_ai_context_frozen_with_section_copy() -> None:
    first = AiContext()
    second = first.with_section("components", "x")
    assert len(first) == 0
    assert second.sections["components"] == "x"
    with pytest.raises(TypeError):
        first.sections["components"] = "mutated"  # type: ignore[index]


def test_build_without_request_stays_limited() -> None:
    context = ContextBuilder(system=SystemModel()).build()
    assert set(context.sections) <= {"components", "capabilities", "system_state"}
    assert "engineering_memory" not in context.sections
    full = ContextBuilder(system=SystemModel()).build(ContextRequest(sections=("events",)))
    assert set(full.sections) == {"events"}
