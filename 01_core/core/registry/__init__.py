"""Registries — name-based services and capability-based components."""

from core.registry.capability_registry import CapabilityRegistry
from core.registry.component_registry import (
    ComponentRegistry,
    ComponentRegistryError,
    RegisteredComponent,
)
from core.registry.registry import Registry

__all__ = [
    "Registry",
    "CapabilityRegistry",
    "ComponentRegistry",
    "ComponentRegistryError",
    "RegisteredComponent",
]
