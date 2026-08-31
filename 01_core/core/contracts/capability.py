"""Capability primitives — what a component can do.

A capability is identified by a dot-separated id (``data.query.candles``).
One capability may have many implementations; one component may provide
many capabilities.
"""

import re
from dataclasses import dataclass

from core.contracts.component import ComponentId

_SEGMENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class CapabilityId:
    """Dot-separated capability identifier (``domain.verb[.object]``)."""

    value: str

    def __post_init__(self) -> None:
        parts = self.value.split(".")
        if len(parts) < 2 or not all(_SEGMENT_PATTERN.match(part) for part in parts):
            msg = f"Invalid capability id: {self.value!r}"
            raise ValueError(msg)

    @property
    def parts(self) -> tuple[str, ...]:
        """The dot-separated segments of this capability id."""
        return tuple(self.value.split("."))

    def startswith(self, prefix: str) -> bool:
        """True if this capability id starts with the given prefix (``"data."``)."""
        return self.value.startswith(prefix)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class BehavioralRules:
    """Machine-readable behavioral contract of a capability."""

    can: tuple[str, ...] = ()
    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    guarantees: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityContract:
    """Contract of a single capability: typed surface plus behavioral rules."""

    capability: CapabilityId
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    rules: BehavioralRules = BehavioralRules()
    version: str = "1.0.0"


@dataclass(frozen=True)
class CapabilityDecl:
    """A capability declared by a component manifest."""

    id: CapabilityId
    description: str = ""
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    contract: CapabilityContract | None = None


@dataclass(frozen=True)
class CapabilityProvider:
    """A registered implementation of a capability."""

    component: ComponentId
    implementation: object
    description: str = ""
