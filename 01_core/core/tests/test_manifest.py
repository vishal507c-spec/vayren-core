"""Component manifest validation tests."""

import pytest

from core.contracts.capability import CapabilityDecl, CapabilityId
from core.contracts.component import ComponentId, ComponentMetadata, ComponentVersion
from core.contracts.health import Health, HealthStatus
from core.contracts.manifest import (
    ComponentManifest,
    ManifestError,
    ManifestValidationResult,
    validate_manifest,
)


def make_manifest(**overrides: object) -> ComponentManifest:
    base: dict[str, object] = {
        "identity": ComponentId("probe"),
        "version": ComponentVersion.parse("1.0.0"),
        "type": "service",
        "capabilities": (CapabilityDecl(id=CapabilityId("probe.run")),),
    }
    base.update(overrides)
    return ComponentManifest(**base)  # type: ignore[arg-type]


def test_valid_manifest_validates() -> None:
    result = validate_manifest(make_manifest())
    assert result.valid
    assert result.errors == ()


def test_manifest_validation_result_raise_on_invalid() -> None:
    result = ManifestValidationResult(valid=False, errors=("type must not be empty",))
    with pytest.raises(ManifestError, match="Invalid component manifest"):
        result.raise_if_invalid()


def test_manifest_validation_result_is_silent_when_valid() -> None:
    ManifestValidationResult(valid=True).raise_if_invalid()


def test_empty_type_is_rejected() -> None:
    result = validate_manifest(make_manifest(type="   "))
    assert not result.valid
    assert "type must not be empty" in result.errors


def test_duplicate_capabilities_are_rejected() -> None:
    decl = CapabilityDecl(id=CapabilityId("probe.run"))
    result = validate_manifest(make_manifest(capabilities=(decl, decl)))
    assert not result.valid
    assert "duplicate capability: probe.run" in result.errors


def test_duplicate_consumed_capabilities_are_rejected() -> None:
    consumed = (CapabilityId("data.query"), CapabilityId("data.query"))
    result = validate_manifest(make_manifest(capabilities_consumed=consumed))
    assert not result.valid
    assert "duplicate consumed capability: data.query" in result.errors


def test_consuming_own_capability_is_rejected() -> None:
    result = validate_manifest(
        make_manifest(
            capabilities=(CapabilityDecl(id=CapabilityId("probe.run")),),
            capabilities_consumed=(CapabilityId("probe.run"),),
        )
    )
    assert not result.valid
    assert "component must not consume its own capability: probe.run" in result.errors


def test_duplicate_consumed_events_are_rejected() -> None:
    result = validate_manifest(make_manifest(events_consumed=("DataLoaded", "DataLoaded")))
    assert not result.valid
    assert "duplicate events_consumed entry: DataLoaded" in result.errors


def test_duplicate_produced_events_are_rejected() -> None:
    result = validate_manifest(make_manifest(events_produced=("ChartReady", "ChartReady")))
    assert not result.valid
    assert "duplicate events_produced entry: ChartReady" in result.errors


def test_self_dependency_is_rejected() -> None:
    result = validate_manifest(make_manifest(dependencies=(ComponentId("probe"),)))
    assert not result.valid
    assert "component must not depend on itself: probe" in result.errors


def test_self_optional_dependency_is_rejected() -> None:
    result = validate_manifest(make_manifest(optional_dependencies=(ComponentId("probe"),)))
    assert not result.valid
    assert "component must not optionally depend on itself: probe" in result.errors


def test_overlapping_dependencies_are_rejected() -> None:
    result = validate_manifest(
        make_manifest(
            dependencies=(ComponentId("market"),),
            optional_dependencies=(ComponentId("market"),),
        )
    )
    assert not result.valid
    assert "dependencies and optional_dependencies overlap: market" in result.errors


def test_manifest_is_frozen() -> None:
    manifest = make_manifest()
    try:
        manifest.type = "changed"  # type: ignore[misc]
    except (AttributeError, ValueError):
        pass
    else:
        raise AssertionError("ComponentManifest must be immutable")


def test_manifest_metadata_defaults() -> None:
    manifest = make_manifest()
    assert manifest.metadata == ComponentMetadata()
    assert manifest.health is None
    assert manifest.contract is None


def test_check_health_without_declared_check() -> None:
    manifest = make_manifest()
    health = manifest.check_health()
    assert health.status == HealthStatus.UNKNOWN
    assert health.message == "no health check declared"


def test_check_health_runs_declared_check() -> None:
    manifest = make_manifest(health=lambda: Health(status=HealthStatus.OK, message="healthy"))
    assert manifest.check_health() == Health(status=HealthStatus.OK, message="healthy")
