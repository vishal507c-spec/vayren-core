"""Intent model — the structured representation of what a user wants.

A deterministic first step before any AI is involved: free-text goals are
classified by rules, validated, and converted into a structured ``Intent``.
"""

from dataclasses import dataclass
from enum import Enum

from core.contracts.capability import CapabilityId
from core.system.change_impact import RiskLevel


class IntentKind(Enum):
    """Deterministic classification of a user goal."""

    ADD_COMPONENT = "add_component"
    CREATE_WORKFLOW = "create_workflow"
    IMPROVE_PERFORMANCE = "improve_performance"
    ADD_DATA_SOURCE = "add_data_source"
    CREATE_STRATEGY = "create_strategy"
    OTHER = "other"


@dataclass(frozen=True)
class Intent:
    """Structured user intent: goal, constraints, and capability expectations."""

    goal: str
    constraints: tuple[str, ...] = ()
    requested_capabilities: tuple[CapabilityId, ...] = ()
    inputs: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    risk_level: RiskLevel = RiskLevel.LOW
    kind: IntentKind = IntentKind.OTHER


class IntentValidationError(ValueError):
    """Raised when an intent cannot be represented deterministically."""


@dataclass(frozen=True)
class IntentValidationResult:
    """Result of validating an intent."""

    valid: bool
    errors: tuple[str, ...] = ()

    def raise_if_invalid(self) -> None:
        """Raise IntentValidationError when the intent is invalid."""
        if not self.valid:
            msg = "Invalid intent: " + "; ".join(self.errors)
            raise IntentValidationError(msg)


def classify(goal: str) -> IntentKind:
    """Classify a free-text goal by deterministic keyword rules."""
    text = goal.lower().strip()
    if "workflow" in text:
        return IntentKind.CREATE_WORKFLOW
    if any(word in text for word in ("performance", "latency", "faster", "slow")):
        return IntentKind.IMPROVE_PERFORMANCE
    if any(phrase in text for phrase in ("data source", "data feed", "market data")):
        return IntentKind.ADD_DATA_SOURCE
    if "strategy" in text:
        return IntentKind.CREATE_STRATEGY
    if any(
        word in text
        for word in ("add", "new", "create", "broker", "component", "module", "indicator")
    ):
        return IntentKind.ADD_COMPONENT
    return IntentKind.OTHER


def validate_intent(intent: Intent) -> IntentValidationResult:
    """Validate an intent: the goal must be present."""
    errors: list[str] = []
    if not intent.goal.strip():
        errors.append("goal must not be empty")
    return IntentValidationResult(valid=not errors, errors=tuple(errors))


def parse_intent(goal: str) -> Intent:
    """Build and validate an intent from a free-text goal."""
    intent = Intent(goal=goal, kind=classify(goal))
    validate_intent(intent).raise_if_invalid()
    return intent
