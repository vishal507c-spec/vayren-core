"""Capability definition tests."""

import pytest

from core.contracts.capability import (
    BehavioralRules,
    CapabilityContract,
    CapabilityDecl,
    CapabilityId,
    CapabilityProvider,
)
from core.contracts.component import ComponentId


@pytest.mark.parametrize(
    "value",
    ["data.acquire", "data.query.candles", "chart.render", "order.execute", "a.b", "ai.analyze"],
)
def test_capability_id_accepts_valid_values(value: str) -> None:
    capability_id = CapabilityId(value)
    assert str(capability_id) == value


@pytest.mark.parametrize(
    "value",
    ["", "data", "Data.query", "data.Query", "data.query!", "data..query", "data.query.candles1-"],
)
def test_capability_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        CapabilityId(value)


def test_capability_id_parts() -> None:
    assert CapabilityId("data.query.candles").parts == ("data", "query", "candles")
    assert CapabilityId("chart.render").parts == ("chart", "render")


def test_capability_id_prefix_matching() -> None:
    capability_id = CapabilityId("data.query.candles")
    assert capability_id.startswith("data.")
    assert capability_id.startswith("data.query")
    assert not capability_id.startswith("chart.")


def test_capability_id_is_hashable_and_equal() -> None:
    assert CapabilityId("data.query") == CapabilityId("data.query")
    assert CapabilityId("data.query") != CapabilityId("data.acquire")


def test_capability_decl_defaults() -> None:
    decl = CapabilityDecl(id=CapabilityId("probe.run"))
    assert decl.description == ""
    assert decl.inputs == ()
    assert decl.outputs == ()
    assert decl.contract is None


def test_capability_decl_with_contract() -> None:
    contract = CapabilityContract(
        capability=CapabilityId("data.query.candles"),
        inputs=("symbol: str",),
        outputs=("bars: tuple[Bar, ...]",),
        rules=BehavioralRules(guarantees=("ascending timestamps",)),
    )
    decl = CapabilityDecl(id=contract.capability, contract=contract)
    assert decl.contract is not None
    assert decl.contract.rules.guarantees == ("ascending timestamps",)


def test_behavioral_rules_defaults() -> None:
    rules = BehavioralRules()
    assert rules.can == ()
    assert rules.must == ()
    assert rules.must_not == ()
    assert rules.guarantees == ()
    assert rules.failure_modes == ()


def test_capability_contract_holds_rules() -> None:
    contract = CapabilityContract(
        capability=CapabilityId("data.acquire"),
        rules=BehavioralRules(
            must=("preserve timestamp integrity",),
            must_not=("silently overwrite valid data",),
        ),
    )
    assert contract.rules.must == ("preserve timestamp integrity",)
    assert contract.version == "1.0.0"


def test_capability_provider_pairs_component_and_implementation() -> None:
    provider = CapabilityProvider(
        component=ComponentId("market"),
        implementation="the-implementation",
        description="reads candles",
    )
    assert provider.component.name == "market"
    assert provider.implementation == "the-implementation"
