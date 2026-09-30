"""Shared AI-layer vocabulary with fail-closed canonical fallbacks.

``core.contracts`` and ``core.system`` are Rust-owned and not present in this
tree, so importing them directly crashes every ``core.ai`` module at import
time. The AI layer itself is Python-owned (AI_ENTRY.md section 1), so the
local fallback definitions below keep the layer importable, and the
canonical-first overwrite at the bottom wins automatically when the canonical
modules land. Nothing here decides policy — these are identifier and level
shapes only (``.value`` stringification everywhere, never ``str()``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class CapabilityId:
    """Opaque capability identifier (local fallback until core.contracts lands)."""

    value: str


class RiskLevel(Enum):
    """Deterministic risk tiers, low to high (local fallback)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SystemModel:
    """Marker for the system-model surface (local fallback).

    Documents the surface the AI layer consumes; every member fails closed
    with `NotImplementedError` until the canonical ``core.system`` module
    lands and replaces this class. Callers pass duck-typed fakes or the real
    model — this base carries no behavior.
    """

    def analyze_change(self, component: str) -> Any:
        """Impact of changing one component (dependents, caps, workflows, risk)."""
        raise NotImplementedError("core.system.system_model is not present in this tree")

    def has_component(self, component: str) -> bool:
        """True when the named component exists in the model."""
        raise NotImplementedError("core.system.system_model is not present in this tree")

    capability_graph: Any = None
    """Capability graph (``providers()`` / ``capabilities()``).

    `None` until the canonical module lands, so ``getattr`` guards treat a
    bare fallback exactly like a model without a graph (skip + log).
    """


def build_snapshot(system: Any) -> Any:
    """Fail closed until ``core.system.snapshot`` lands."""
    raise NotImplementedError(
        "core.system.snapshot is not present in this tree; "
        "pass an explicit snapshot or wait for the canonical module"
    )


try:  # Canonical vocabulary wins when it lands.
    from core.contracts.capability import (  # pyright: ignore[reportMissingImports]
        CapabilityId,  # noqa: F401
    )
    from core.system.change_impact import (  # pyright: ignore[reportMissingImports]
        RiskLevel,  # noqa: F401
    )
    from core.system.snapshot import (  # pyright: ignore[reportMissingImports]
        build_snapshot,  # noqa: F401
    )
    from core.system.system_model import (  # pyright: ignore[reportMissingImports]
        SystemModel,  # noqa: F401
    )
except ImportError:  # pragma: no cover - canonical modules absent in this tree.
    pass


__all__ = ["CapabilityId", "RiskLevel", "SystemModel", "build_snapshot"]
