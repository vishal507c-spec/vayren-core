"""SymbolsListed — the data directory scan result."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class SymbolsListed(Event):
    """The symbols discovered in the data directory."""

    symbols: tuple[str, ...]
