"""Component primitives — stable identity, version, status, and metadata."""

import re
from dataclasses import dataclass
from enum import Enum

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ComponentStatus(Enum):
    """Lifecycle status of a registered component."""

    UNKNOWN = "unknown"
    REGISTERED = "registered"
    STARTED = "started"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class ComponentId:
    """Stable identity of a component (e.g. ``market``, ``chart``)."""

    name: str

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            msg = f"Invalid component name: {self.name!r}"
            raise ValueError(msg)

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class ComponentVersion:
    """Semantic version of a component (``major.minor.patch``)."""

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        for part, label in ((self.major, "major"), (self.minor, "minor"), (self.patch, "patch")):
            if part < 0:
                msg = f"Invalid {label} version: {part!r}"
                raise ValueError(msg)

    @classmethod
    def parse(cls, text: str) -> "ComponentVersion":
        """Parse a ``"1.2.3"`` version string."""
        parts = text.split(".")
        if len(parts) != 3:
            msg = f"Invalid version string: {text!r}"
            raise ValueError(msg)
        try:
            major, minor, patch = (int(part) for part in parts)
        except ValueError:
            msg = f"Invalid version string: {text!r}"
            raise ValueError(msg) from None
        return cls(major=major, minor=minor, patch=patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class ComponentMetadata:
    """Human and AI readable description of a component."""

    description: str = ""
    tags: tuple[str, ...] = ()
