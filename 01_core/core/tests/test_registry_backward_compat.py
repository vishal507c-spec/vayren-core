"""Backward compatibility — the existing name-based Registry is untouched."""

import pytest

from core.contracts.capability import CapabilityProvider
from core.contracts.component import ComponentId
from core.registry.capability_registry import CapabilityRegistry
from core.registry.registry import Registry


def test_registry_register_get_list() -> None:
    registry: Registry[object] = Registry()
    registry.register("service", object())
    assert "service" in registry
    assert len(registry) == 1
    assert registry.list() == ["service"]
    assert registry.get("service") is not None


def test_registry_duplicate_registration_raises() -> None:
    registry: Registry[object] = Registry()
    registry.register("service", object())
    with pytest.raises(ValueError, match="already registered"):
        registry.register("service", object())


def test_registry_missing_key_raises() -> None:
    registry: Registry[object] = Registry()
    with pytest.raises(KeyError, match="not found"):
        registry.get("service")


def test_registry_iterates_name_item_pairs() -> None:
    registry: Registry[object] = Registry()
    registry.register("first", 1)
    registry.register("second", 2)
    assert dict(iter(registry)) == {"first": 1, "second": 2}


def test_registry_and_capability_registry_coexist() -> None:
    registry: Registry[object] = Registry()
    capability_registry = CapabilityRegistry()
    registry.register("market_loader", object())
    capability_registry.register(
        "data.query.candles",
        CapabilityProvider(component=ComponentId("market"), implementation=object()),
    )
    assert registry.list() == ["market_loader"]
    assert len(capability_registry) == 1
