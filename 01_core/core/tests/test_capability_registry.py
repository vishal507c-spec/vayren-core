"""Capability registry tests — registration, discovery, multiple implementations."""

import pytest

from core.contracts.capability import CapabilityId, CapabilityProvider
from core.contracts.component import ComponentId
from core.registry.capability_registry import CapabilityRegistry


def provider(component: str, implementation: str = "impl") -> CapabilityProvider:
    return CapabilityProvider(component=ComponentId(component), implementation=implementation)


def test_register_and_providers() -> None:
    registry = CapabilityRegistry()
    registry.register("data.query.candles", provider("market"))
    found = registry.providers("data.query.candles")
    assert len(found) == 1
    assert found[0].component.name == "market"


def test_register_accepts_capability_id_objects() -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilityId("data.query.candles"), provider("market"))
    assert registry.has(CapabilityId("data.query.candles"))


def test_multiple_implementations_from_different_components() -> None:
    registry = CapabilityRegistry()
    registry.register("data.acquire", provider("zerodha"))
    registry.register("data.acquire", provider("delta"))
    registry.register("data.acquire", provider("future"))
    found = registry.providers("data.acquire")
    assert [p.component.name for p in found] == ["zerodha", "delta", "future"]


def test_duplicate_implementation_from_same_component_is_rejected() -> None:
    registry = CapabilityRegistry()
    registry.register("data.acquire", provider("zerodha"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register("data.acquire", provider("zerodha"))


def test_providers_for_missing_capability_are_empty() -> None:
    registry = CapabilityRegistry()
    assert registry.providers("data.acquire") == ()


def test_capabilities_are_sorted() -> None:
    registry = CapabilityRegistry()
    registry.register("chart.render", provider("chart"))
    registry.register("data.acquire", provider("zerodha"))
    registry.register("data.query", provider("market"))
    assert [str(capability) for capability in registry.capabilities()] == [
        "chart.render",
        "data.acquire",
        "data.query",
    ]


def test_find_by_prefix() -> None:
    registry = CapabilityRegistry()
    registry.register("data.acquire", provider("zerodha"))
    registry.register("data.query.candles", provider("market"))
    registry.register("chart.render", provider("chart"))
    assert [str(capability) for capability in registry.find("data.")] == [
        "data.acquire",
        "data.query.candles",
    ]
    assert registry.find("chart.") == (CapabilityId("chart.render"),)
    assert registry.find("unknown.") == ()


def test_has_contains_and_len() -> None:
    registry = CapabilityRegistry()
    registry.register("data.acquire", provider("zerodha"))
    registry.register("data.query", provider("market"))
    assert registry.has("data.acquire")
    assert "data.query" in registry
    assert "chart.render" not in registry
    assert len(registry) == 2
