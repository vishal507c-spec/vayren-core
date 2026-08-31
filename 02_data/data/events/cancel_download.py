"""CancelDownload — request to stop the running download."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class CancelDownload(Event):
    """Cancel the running download; the engine stops between chunks."""
