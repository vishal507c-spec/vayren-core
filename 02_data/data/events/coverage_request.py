"""CoverageRequest — request to scan a symbol's historical coverage."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class CoverageRequest(Event):
    """Request a coverage scan for one symbol (no network involved)."""

    symbol: str
    interval: str
