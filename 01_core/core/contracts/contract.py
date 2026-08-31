"""Component-level contracts — invariants and capability contracts of a component."""

from dataclasses import dataclass

from core.contracts.capability import CapabilityContract
from core.contracts.component import ComponentId, ComponentVersion


@dataclass(frozen=True)
class ComponentContract:
    """Declared contract of a component: invariants it guarantees and contracts it honors."""

    component: ComponentId
    version: ComponentVersion
    capabilities: tuple[CapabilityContract, ...] = ()
    invariants: tuple[str, ...] = ()
