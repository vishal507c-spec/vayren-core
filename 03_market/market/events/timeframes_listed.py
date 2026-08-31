"""TimeframesListed — timeframes detected in the symbol's database."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class TimeframesListed(Event):
    """Timeframes detected from the symbol's database, ascending by size."""

    symbol: str
    timeframes: tuple[str, ...]
