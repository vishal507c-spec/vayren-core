"""Component manifest — the machine-readable Component DNA.

The manifest answers: what is this component, what can it do, what does it
require, what does it produce, what does it depend on, which events it
consumes and emits, and what side effects it can cause.
"""

from dataclasses import dataclass, field

from core.contracts.capability import CapabilityDecl, CapabilityId
from core.contracts.component import ComponentId, ComponentMetadata, ComponentVersion
from core.contracts.contract import ComponentContract
from core.contracts.health import Health, HealthCheck, HealthStatus


class ManifestError(ValueError):
    """Raised when a component manifest is invalid."""


@dataclass(frozen=True)
class ManifestValidationResult:
    """Result of validating a component manifest."""

    valid: bool
    errors: tuple[str, ...] = ()

    def raise_if_invalid(self) -> None:
        """Raise ManifestError when the manifest is invalid."""
        if not self.valid:
            msg = "Invalid component manifest: " + "; ".join(self.errors)
            raise ManifestError(msg)


@dataclass(frozen=True)
class ComponentManifest:
    """Machine-readable self-description (Component DNA) of a component."""

    identity: ComponentId
    version: ComponentVersion
    type: str
    capabilities: tuple[CapabilityDecl, ...] = ()
    capabilities_consumed: tuple[CapabilityId, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    dependencies: tuple[ComponentId, ...] = ()
    optional_dependencies: tuple[ComponentId, ...] = ()
    events_consumed: tuple[str, ...] = ()
    events_produced: tuple[str, ...] = ()
    health: HealthCheck | None = None
    resource_requirements: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    metadata: ComponentMetadata = field(default_factory=ComponentMetadata)
    contract: ComponentContract | None = None

    def check_health(self) -> Health:
        """Run the declared health check, or report UNKNOWN when none is declared."""
        if self.health is None:
            return Health(status=HealthStatus.UNKNOWN, message="no health check declared")
        return self.health()


def validate_manifest(manifest: ComponentManifest) -> ManifestValidationResult:
    """Validate a component manifest; returns a result, never raises."""
    errors: list[str] = []
    if not manifest.type.strip():
        errors.append("type must not be empty")
    seen_capabilities: set[str] = set()
    for decl in manifest.capabilities:
        if decl.id.value in seen_capabilities:
            errors.append(f"duplicate capability: {decl.id}")
        seen_capabilities.add(decl.id.value)
    seen_consumed: set[str] = set()
    for capability in manifest.capabilities_consumed:
        if capability.value in seen_consumed:
            errors.append(f"duplicate consumed capability: {capability}")
        seen_consumed.add(capability.value)
    own_overlap = seen_capabilities & seen_consumed
    if own_overlap:
        errors.append(
            "component must not consume its own capability: " + ", ".join(sorted(own_overlap))
        )
    _check_unique(manifest.events_consumed, "events_consumed", errors)
    _check_unique(manifest.events_produced, "events_produced", errors)
    dependency_names = {dep.name for dep in manifest.dependencies}
    if manifest.identity.name in dependency_names:
        errors.append(f"component must not depend on itself: {manifest.identity}")
    optional_names = {dep.name for dep in manifest.optional_dependencies}
    if manifest.identity.name in optional_names:
        errors.append(f"component must not optionally depend on itself: {manifest.identity}")
    overlap = dependency_names & optional_names
    if overlap:
        errors.append(
            "dependencies and optional_dependencies overlap: " + ", ".join(sorted(overlap))
        )
    return ManifestValidationResult(valid=not errors, errors=tuple(errors))


def _check_unique(items: tuple[str, ...], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for item in items:
        if item in seen:
            errors.append(f"duplicate {label} entry: {item}")
        seen.add(item)
