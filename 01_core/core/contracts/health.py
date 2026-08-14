"""Component health primitives."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class HealthStatus(Enum):
    """Health status reported by a component."""

    UNKNOWN = "unknown"
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class Health:
    """Self-reported health of a component."""

    status: HealthStatus = HealthStatus.UNKNOWN
    message: str = ""


HealthCheck = Callable[[], Health]
