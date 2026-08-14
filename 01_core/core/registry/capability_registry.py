"""Capability registry — discover implementations by capability.

Complements the existing name-based Registry: instead of ``name → object``
it maps ``capability → implementations``, supporting multiple
implementations of the same capability.
"""

from core.contracts.capability import CapabilityId, CapabilityProvider


class CapabilityRegistry:
    """Maps every capability to its registered implementations."""

    def __init__(self) -> None:
        self._providers: dict[str, list[CapabilityProvider]] = {}

    def register(self, capability: CapabilityId | str, provider: CapabilityProvider) -> None:
        """Register one implementation of a capability.

        A second implementation from the same component is rejected; a
        second implementation from a different component is allowed.
        """
        capability_id = _as_capability(capability)
        providers = self._providers.setdefault(capability_id.value, [])
        for existing in providers:
            if existing.component.name == provider.component.name:
                msg = f"Capability {capability_id} already registered for {provider.component}"
                raise ValueError(msg)
        providers.append(provider)

    def providers(self, capability: CapabilityId | str) -> tuple[CapabilityProvider, ...]:
        """Return every implementation of a capability (registration order)."""
        return tuple(self._providers.get(_as_capability(capability).value, ()))

    def capabilities(self) -> tuple[CapabilityId, ...]:
        """Return all registered capability ids, sorted."""
        return tuple(sorted((CapabilityId(value) for value in self._providers), key=str))

    def find(self, prefix: str) -> tuple[CapabilityId, ...]:
        """Return capability ids starting with the given prefix (e.g. ``"data."``)."""
        return tuple(
            CapabilityId(value) for value in sorted(self._providers) if value.startswith(prefix)
        )

    def has(self, capability: CapabilityId | str) -> bool:
        """True if at least one implementation is registered for the capability."""
        return _as_capability(capability).value in self._providers

    def __contains__(self, capability: CapabilityId | str) -> bool:
        return self.has(capability)

    def __len__(self) -> int:
        return len(self._providers)


def _as_capability(capability: CapabilityId | str) -> CapabilityId:
    return capability if isinstance(capability, CapabilityId) else CapabilityId(capability)
