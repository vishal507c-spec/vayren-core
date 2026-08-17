"""WindowRendered — the chart window has been displayed."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class WindowRendered(Event):
    """The chart window has been shown to the user."""
