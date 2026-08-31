"""Capability graph — who provides and who consumes every capability.

Component → provides → Capability → consumed_by → Component.
One capability may have many providers; consumption is declared in
manifests via ``capabilities_consumed``.
"""

from core.contracts.manifest import ComponentManifest


class CapabilityGraph:
    """Capability providers and consumers derived from component manifests."""

    def __init__(self, manifests: tuple[ComponentManifest, ...]) -> None:
        providers: dict[str, set[str]] = {}
        consumers: dict[str, set[str]] = {}
        capabilities_of: dict[str, set[str]] = {}
        for manifest in manifests:
            name = manifest.identity.name
            capabilities_of[name] = {decl.id.value for decl in manifest.capabilities}
            for decl in manifest.capabilities:
                providers.setdefault(decl.id.value, set()).add(name)
            for capability in manifest.capabilities_consumed:
                consumers.setdefault(capability.value, set()).add(name)
        self._providers = {cap: tuple(sorted(names)) for cap, names in providers.items()}
        self._consumers = {cap: tuple(sorted(names)) for cap, names in consumers.items()}
        self._capabilities_of = {
            name: tuple(sorted(caps)) for name, caps in capabilities_of.items()
        }
        self._capabilities: tuple[str, ...] = tuple(sorted(providers))

    def capabilities(self) -> tuple[str, ...]:
        """Every provided capability id, sorted."""
        return self._capabilities

    def providers(self, capability: str) -> tuple[str, ...]:
        """Components providing this capability (sorted)."""
        return self._providers.get(capability, ())

    def consumers(self, capability: str) -> tuple[str, ...]:
        """Components declaring consumption of this capability (sorted)."""
        return self._consumers.get(capability, ())

    def capabilities_of(self, component: str) -> tuple[str, ...]:
        """Capabilities provided by one component (sorted)."""
        return self._capabilities_of.get(component, ())

    def consumed_by(self, component: str) -> tuple[str, ...]:
        """Capabilities one component consumes (sorted)."""
        return tuple(sorted(cap for cap, names in self._consumers.items() if component in names))

    def unresolved_consumers(self) -> tuple[str, ...]:
        """Consumed capabilities with no registered provider (sorted)."""
        return tuple(sorted(set(self._consumers) - set(self._providers)))
