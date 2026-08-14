"""Component registry tests — manifest registration, discovery, rejection."""

import pytest

from core.contracts.capability import CapabilityDecl, CapabilityId
from core.contracts.component import ComponentId, ComponentStatus, ComponentVersion
from core.contracts.manifest import ComponentManifest, ManifestError
from core.registry.component_registry import ComponentRegistry, ComponentRegistryError


def make_manifest(name: str = "probe", **overrides: object) -> ComponentManifest:
    base: dict[str, object] = {
        "identity": ComponentId(name),
        "version": ComponentVersion.parse("1.0.0"),
        "type": "service",
        "capabilities": (CapabilityDecl(id=CapabilityId("probe.run")),),
    }
    base.update(overrides)
    return ComponentManifest(**base)  # type: ignore[arg-type]


def test_register_valid_component() -> None:
    registry = ComponentRegistry()
    registry.register(make_manifest(), implementations={"probe.run": object()})
    assert "probe" in registry
    assert len(registry) == 1
    assert registry.component("probe").manifest.identity.name == "probe"


def test_register_with_capability_id_keys() -> None:
    registry = ComponentRegistry()
    registry.register(
        make_manifest(),
        implementations={CapabilityId("probe.run"): object()},
    )
    assert registry.component("probe").status == ComponentStatus.REGISTERED


def test_missing_implementation_for_declared_capability_is_rejected() -> None:
    registry = ComponentRegistry()
    with pytest.raises(ComponentRegistryError, match="Missing implementations"):
        registry.register(make_manifest())


def test_undeclared_implementation_is_rejected() -> None:
    registry = ComponentRegistry()
    with pytest.raises(ComponentRegistryError, match="Undeclared implementations"):
        registry.register(
            make_manifest(),
            implementations={"probe.run": object(), "probe.unknown": object()},
        )


def test_duplicate_component_is_rejected() -> None:
    registry = ComponentRegistry()
    registry.register(make_manifest(), implementations={"probe.run": object()})
    with pytest.raises(ComponentRegistryError, match="already registered"):
        registry.register(make_manifest(), implementations={"probe.run": object()})


def test_invalid_manifest_is_rejected() -> None:
    registry = ComponentRegistry()
    invalid = make_manifest(dependencies=(ComponentId("probe"),))
    with pytest.raises(ManifestError, match="must not depend on itself"):
        registry.register(invalid, implementations={"probe.run": object()})


def test_missing_component_raises_key_error() -> None:
    registry = ComponentRegistry()
    with pytest.raises(KeyError):
        registry.component("nope")


def test_capabilities_of_component() -> None:
    registry = ComponentRegistry()
    manifest = make_manifest(
        capabilities=(
            CapabilityDecl(id=CapabilityId("probe.run")),
            CapabilityDecl(id=CapabilityId("probe.check")),
        )
    )
    registry.register(
        manifest,
        implementations={"probe.run": object(), "probe.check": object()},
    )
    assert [str(capability) for capability in registry.capabilities("probe")] == [
        "probe.run",
        "probe.check",
    ]


def test_providers_across_components() -> None:
    registry = ComponentRegistry()
    registry.register(
        make_manifest(
            name="zerodha",
            capabilities=(CapabilityDecl(id=CapabilityId("data.acquire")),),
        ),
        implementations={"data.acquire": object()},
    )
    registry.register(
        make_manifest(
            name="delta",
            capabilities=(CapabilityDecl(id=CapabilityId("data.acquire")),),
        ),
        implementations={"data.acquire": object()},
    )
    found = registry.providers("data.acquire")
    assert [provider.component.name for provider in found] == ["zerodha", "delta"]


def test_find_returns_matching_capabilities() -> None:
    registry = ComponentRegistry()
    registry.register(
        make_manifest(
            capabilities=(
                CapabilityDecl(id=CapabilityId("data.query.candles")),
                CapabilityDecl(id=CapabilityId("data.query.quotes")),
            )
        ),
        implementations={"data.query.candles": object(), "data.query.quotes": object()},
    )
    assert [str(capability) for capability in registry.find("data.")] == [
        "data.query.candles",
        "data.query.quotes",
    ]


def test_summary_is_deterministic_and_readable() -> None:
    registry = ComponentRegistry()
    registry.register(
        make_manifest(
            name="market",
            capabilities=(CapabilityDecl(id=CapabilityId("data.query.candles")),),
        ),
        implementations={"data.query.candles": object()},
    )
    registry.register(
        make_manifest(
            name="chart",
            type="presentation",
            capabilities=(CapabilityDecl(id=CapabilityId("chart.render")),),
        ),
        implementations={"chart.render": object()},
    )
    first = registry.summary()
    second = registry.summary()
    assert first == second
    assert first.startswith("components: 2")
    assert "- chart v1.0.0 [presentation]" in first
    assert "- market v1.0.0 [service]" in first
    assert "    provides data.query.candles" in first
    assert "    provides chart.render" in first


def test_components_sorted_by_name() -> None:
    registry = ComponentRegistry()
    registry.register(make_manifest(name="zulu"), implementations={"probe.run": object()})
    registry.register(make_manifest(name="alpha"), implementations={"probe.run": object()})
    assert [component.manifest.identity.name for component in registry.components()] == [
        "alpha",
        "zulu",
    ]
