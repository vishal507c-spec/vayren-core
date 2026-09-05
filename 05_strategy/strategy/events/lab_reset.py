"""LabReset — request: clear the lab outputs (results, overlays, state)."""

from dataclasses import dataclass

from core import Event


@dataclass(frozen=True)
class LabReset(Event):
    """Clear lab outputs; engines and panels return to their idle state."""
