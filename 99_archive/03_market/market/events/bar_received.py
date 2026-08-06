from dataclasses import dataclass
from market.models.bar import Bar


@dataclass(frozen=True)
class BarReceived:
    """Emitted when a new bar is ingested."""

    bar: Bar
    source: str = ""
