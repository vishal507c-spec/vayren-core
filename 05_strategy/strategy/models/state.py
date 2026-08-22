"""StrategyState — per-strategy runtime state snapshot."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyState:
    """Immutable snapshot of one strategy's position state during a run.

    The runner feeds this back into the logic each bar so strategies can
    condition decisions on their own open position without sharing state.

    Attributes:
        flat: True when no position is open.
        side: ``"LONG"`` or ``"SHORT"`` when a position is open, else None.
        entry_price: Fill price of the open position, else None.
        entry_index: Bar index of the entry fill, else None.
    """

    flat: bool = True
    side: str | None = None
    entry_price: float | None = None
    entry_index: int | None = None

    @staticmethod
    def open(side: str, entry_price: float, entry_index: int) -> "StrategyState":
        """State for an open position."""
        return StrategyState(
            flat=False, side=side, entry_price=entry_price, entry_index=entry_index
        )
