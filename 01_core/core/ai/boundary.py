"""AI boundary — what AI may and may not do.

AI may understand, search, reason, plan, suggest, diagnose, generate,
compare, and optimize. AI may never directly execute trades, bypass risk,
delete production data, modify protected systems, deploy unvalidated code,
or override contracts. Deterministic VAYREN policies stay in charge.
"""

import re
from dataclasses import dataclass
from enum import Enum


class ActionKind(Enum):
    """Every AI action the boundary knows about, allowed or forbidden."""

    UNDERSTAND = "understand"
    SEARCH = "search"
    REASON = "reason"
    PLAN = "plan"
    SUGGEST = "suggest"
    DIAGNOSE = "diagnose"
    GENERATE = "generate"
    COMPARE = "compare"
    OPTIMIZE = "optimize"
    EXECUTE_TRADE = "execute_trade"
    BYPASS_RISK = "bypass_risk"
    DELETE_PRODUCTION_DATA = "delete_production_data"
    MODIFY_PROTECTED_SYSTEM = "modify_protected_system"
    DEPLOY_UNVALIDATED = "deploy_unvalidated"
    OVERRIDE_CONTRACT = "override_contract"


ALLOWED_ACTIONS = frozenset(
    {
        ActionKind.UNDERSTAND,
        ActionKind.SEARCH,
        ActionKind.REASON,
        ActionKind.PLAN,
        ActionKind.SUGGEST,
        ActionKind.DIAGNOSE,
        ActionKind.GENERATE,
        ActionKind.COMPARE,
        ActionKind.OPTIMIZE,
    }
)

FORBIDDEN_ACTIONS = frozenset(ActionKind) - ALLOWED_ACTIONS

_ALLOWED_VERBS: dict[str, ActionKind] = {
    "understand": ActionKind.UNDERSTAND,
    "search": ActionKind.SEARCH,
    "reason": ActionKind.REASON,
    "plan": ActionKind.PLAN,
    "suggest": ActionKind.SUGGEST,
    "diagnose": ActionKind.DIAGNOSE,
    "generate": ActionKind.GENERATE,
    "compare": ActionKind.COMPARE,
    "optimize": ActionKind.OPTIMIZE,
}

_FORBIDDEN_VERBS: dict[str, ActionKind] = {
    "execute trade": ActionKind.EXECUTE_TRADE,
    "bypass risk": ActionKind.BYPASS_RISK,
    "delete production data": ActionKind.DELETE_PRODUCTION_DATA,
    "delete data": ActionKind.DELETE_PRODUCTION_DATA,
    "modify protected": ActionKind.MODIFY_PROTECTED_SYSTEM,
    "deploy unvalidated": ActionKind.DEPLOY_UNVALIDATED,
    "override contract": ActionKind.OVERRIDE_CONTRACT,
}


class BoundaryViolation(RuntimeError):  # noqa: N818 -- "violation" is the domain term; RuntimeError (not PermissionError/OSError) so boundary denials are never swallowed by `except OSError` handlers.
    """Raised when AI attempts an action outside its boundary."""


@dataclass(frozen=True)
class BoundaryDecision:
    """Result of checking one AI action request."""

    action: ActionKind | None
    allowed: bool
    reason: str


class AiBoundary:
    """Fail-closed gatekeeper for AI actions."""

    def request(self, action: ActionKind) -> BoundaryDecision:
        """Decide whether one action is inside the AI boundary.

        Fail closed: anything that is not an `ActionKind` member is denied
        (never a crash on ``.value``).
        """
        if not isinstance(action, ActionKind):
            return BoundaryDecision(None, False, f"unrecognized action request: {action!r}")
        if action in FORBIDDEN_ACTIONS:
            return BoundaryDecision(action, False, f"forbidden by AI boundary: {action.value}")
        return BoundaryDecision(action, True, f"allowed by AI boundary: {action.value}")

    def require(self, action: ActionKind) -> None:
        """Raise BoundaryViolation unless the action is allowed."""
        decision = self.request(action)
        if not decision.allowed:
            raise BoundaryViolation(decision.reason)

    def classify(self, text: str) -> ActionKind | None:
        """Classify a free-text action request; unknown requests return None.

        Verbs match on token boundaries (``\\b``), so "planned" does not
        classify as PLAN and "re-execute trades" does not grant anything —
        substring matching would both over- and under-grant.
        """
        lowered = text.lower().strip()
        for verb, action in _FORBIDDEN_VERBS.items():
            if re.search(r"\b" + re.escape(verb) + r"\b", lowered):
                return action
        for verb, action in _ALLOWED_VERBS.items():
            if re.search(r"\b" + re.escape(verb) + r"\b", lowered):
                return action
        return None

    def request_text(self, text: str) -> BoundaryDecision:
        """Decide a free-text action request; unrecognized requests fail closed."""
        action = self.classify(text)
        if action is None:
            return BoundaryDecision(None, False, "unrecognized action request")
        return self.request(action)
