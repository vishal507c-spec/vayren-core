"""Universal foundation contracts — components, capabilities, manifests, health."""

from core.contracts.capability import (
    BehavioralRules,
    CapabilityContract,
    CapabilityDecl,
    CapabilityId,
    CapabilityProvider,
)
from core.contracts.component import (
    ComponentId,
    ComponentMetadata,
    ComponentStatus,
    ComponentVersion,
)
from core.contracts.contract import ComponentContract
from core.contracts.health import Health, HealthCheck, HealthStatus
from core.contracts.manifest import (
    ComponentManifest,
    ManifestError,
    ManifestValidationResult,
    validate_manifest,
)

__all__ = [
    "BehavioralRules",
    "CapabilityContract",
    "CapabilityDecl",
    "CapabilityId",
    "CapabilityProvider",
    "ComponentContract",
    "ComponentId",
    "ComponentManifest",
    "ComponentMetadata",
    "ComponentStatus",
    "ComponentVersion",
    "Health",
    "HealthCheck",
    "HealthStatus",
    "ManifestError",
    "ManifestValidationResult",
    "validate_manifest",
]
