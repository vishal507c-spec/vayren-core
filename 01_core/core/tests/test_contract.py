"""Component contract tests."""

from core.contracts.capability import CapabilityContract, CapabilityId
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.contract import ComponentContract


def test_component_contract_defaults() -> None:
    contract = ComponentContract(
        component=ComponentId("market"),
        version=ComponentVersion.parse("1.0.0"),
    )
    assert contract.capabilities == ()
    assert contract.invariants == ()


def test_component_contract_holds_invariants() -> None:
    contract = ComponentContract(
        component=ComponentId("market"),
        version=ComponentVersion.parse("1.0.0"),
        invariants=("no duplicate candle timestamps", "bars ascending by timestamp"),
    )
    assert contract.invariants == ("no duplicate candle timestamps", "bars ascending by timestamp")


def test_component_contract_carries_capability_contracts() -> None:
    capability_contract = CapabilityContract(
        capability=CapabilityId("data.query.candles"),
        inputs=("symbol: str",),
        outputs=("bars: tuple[Bar, ...]",),
    )
    contract = ComponentContract(
        component=ComponentId("market"),
        version=ComponentVersion.parse("1.0.0"),
        capabilities=(capability_contract,),
    )
    assert contract.capabilities[0].capability.value == "data.query.candles"
    assert contract.version == ComponentVersion(1, 0, 0)


def test_component_contract_is_frozen() -> None:
    contract = ComponentContract(
        component=ComponentId("market"),
        version=ComponentVersion.parse("1.0.0"),
    )
    try:
        contract.invariants = ("changed",)  # type: ignore[misc]
    except (AttributeError, ValueError):
        pass
    else:
        raise AssertionError("ComponentContract must be immutable")
