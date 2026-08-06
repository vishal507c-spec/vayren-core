from dataclasses import dataclass


@dataclass(frozen=True)
class PositionTargeted:
    """Emitted when a strategy sets a position target."""

    symbol: str
    target_size: int
    strategy: str = ""
    reason: str = ""
