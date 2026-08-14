"""Component registry — components, manifests, and capability discovery.

The entry point of the universal foundation: register a component with its
manifest and implementations, then discover capabilities and providers.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from core.contracts.capability import CapabilityId, CapabilityProvider
from core.contracts.component import ComponentStatus
from core.contracts.manifest import ComponentManifest, validate_manifest
from core.registry.capability_registry import CapabilityRegistry


class ComponentRegistryError(ValueError):
    """Raised when a component cannot be registered."""


@dataclass
class RegisteredComponent:
    """A registered component: its manifest plus current lifecycle status."""

    manifest: ComponentManifest
    status: ComponentStatus = ComponentStatus.REGISTERED


class ComponentRegistry:
    """Registers components and answers component and capability discovery queries."""

    def __init__(self) -> None:
        self._components: dict[str, RegisteredComponent] = {}
        self._capabilities = CapabilityRegistry()

    def register(
        self,
        manifest: ComponentManifest,
        implementations: Mapping[CapabilityId | str, object] | None = None,
    ) -> None:
        """Register a component: validate its manifest, then wire its capabilities.

        Every declared capability must have an implementation and every
        implementation must be declared.
        """
        validation = validate_manifest(manifest)
        if not validation.valid:
            validation.raise_if_invalid()
        if manifest.identity.name in self._components:
            msg = f"Component already registered: {manifest.identity}"
            raise ComponentRegistryError(msg)
        normalized = {str(key): value for key, value in (implementations or {}).items()}
        declared = {decl.id.value for decl in manifest.capabilities}
        provided = set(normalized)
        missing = declared - provided
        if missing:
            msg = "Missing implementations for: " + ", ".join(sorted(missing))
            raise ComponentRegistryError(msg)
        extra = provided - declared
        if extra:
            msg = "Undeclared implementations: " + ", ".join(sorted(extra))
            raise ComponentRegistryError(msg)
        for decl in manifest.capabilities:
            self._capabilities.register(
                decl.id,
                CapabilityProvider(
                    component=manifest.identity,
                    implementation=normalized[decl.id.value],
                    description=decl.description,
                ),
            )
        self._components[manifest.identity.name] = RegisteredComponent(manifest=manifest)

    def component(self, name: str) -> RegisteredComponent:
        """Return the registered component record, or raise KeyError."""
        if name not in self._components:
            msg = f"Component not found: {name}"
            raise KeyError(msg)
        return self._components[name]

    def components(self) -> tuple[RegisteredComponent, ...]:
        """Return all registered components, sorted by name."""
        return tuple(self._components[name] for name in sorted(self._components))

    def capabilities(self, name: str) -> tuple[CapabilityId, ...]:
        """Return the capability ids declared by one component."""
        return tuple(decl.id for decl in self.component(name).manifest.capabilities)

    def providers(self, capability: CapabilityId | str) -> tuple[CapabilityProvider, ...]:
        """Return every implementation of a capability, across all components."""
        return self._capabilities.providers(capability)

    def find(self, prefix: str) -> tuple[CapabilityId, ...]:
        """Return capability ids starting with the given prefix (e.g. ``"data."``)."""
        return self._capabilities.find(prefix)

    def summary(self) -> str:
        """Human and AI readable summary of the registered system."""
        lines = [f"components: {len(self._components)}"]
        for registered in self.components():
            manifest = registered.manifest
            lines.append(f"- {manifest.identity} v{manifest.version} [{manifest.type}]")
            for decl in manifest.capabilities:
                lines.append(f"    provides {decl.id}")
        return "\n".join(lines)

    def __contains__(self, name: str) -> bool:
        return name in self._components

    def __len__(self) -> int:
        return len(self._components)
