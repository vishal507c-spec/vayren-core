"""Intent model tests — classification, validation, and spec examples."""

from core.ai.intent import (
    Intent,
    IntentKind,
    IntentValidationError,
    classify,
    parse_intent,
    validate_intent,
)
from core.contracts.capability import CapabilityId
from core.system.change_impact import RiskLevel


def test_classifies_spec_examples() -> None:
    assert classify("Add a broker") is IntentKind.ADD_COMPONENT
    assert classify("Create a research workflow") is IntentKind.CREATE_WORKFLOW
    assert classify("Improve performance") is IntentKind.IMPROVE_PERFORMANCE
    assert classify("Add a new data source") is IntentKind.ADD_DATA_SOURCE
    assert classify("Create a new strategy component") is IntentKind.CREATE_STRATEGY


def test_classify_unknown_goal_is_other() -> None:
    assert classify("What time is it?") is IntentKind.OTHER


def test_parse_intent_builds_kind() -> None:
    intent = parse_intent("Add a broker")
    assert intent.goal == "Add a broker"
    assert intent.kind is IntentKind.ADD_COMPONENT


def test_empty_goal_is_rejected() -> None:
    intent = Intent(goal="   ")
    result = validate_intent(intent)
    assert not result.valid
    assert "goal must not be empty" in result.errors


def test_raise_if_invalid() -> None:
    try:
        parse_intent("")
    except IntentValidationError as error:
        assert "goal must not be empty" in str(error)
    else:
        raise AssertionError("expected IntentValidationError")


def test_valid_intent_passes() -> None:
    intent = Intent(
        goal="Create a research workflow",
        constraints=("read-only",),
        requested_capabilities=(CapabilityId("data.query.candles"),),
        inputs=("symbol: str",),
        expected_outputs=("model: ChartModel",),
        risk_level=RiskLevel.MEDIUM,
    )
    result = validate_intent(intent)
    assert result.valid
    assert result.errors == ()


def test_risk_level_defaults_to_low() -> None:
    intent = parse_intent("Create a research workflow")
    assert intent.risk_level is RiskLevel.LOW


def test_constraints_and_capabilities_round_trip() -> None:
    intent = Intent(
        goal="Add a data feed",
        constraints=("no production access",),
        requested_capabilities=(CapabilityId("data.query.quotes"),),
    )
    assert intent.constraints == ("no production access",)
    assert [str(capability) for capability in intent.requested_capabilities] == [
        "data.query.quotes"
    ]
