"""StrategyState — per-strategy runtime state snapshot."""

import math
from dataclasses import dataclass

_VALID_SIDES = ("LONG", "SHORT")


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

    def __post_init__(self) -> None:
        """Normalize ``side`` to upper case and reject inconsistent snapshots."""
        if self.side is not None:
            if not isinstance(self.side, str):
                raise ValueError(f"side must be 'LONG'/'SHORT' or None, got {self.side!r}")
            normalized = self.side.strip().upper()
            if normalized not in _VALID_SIDES:
                raise ValueError(f"side must be one of {_VALID_SIDES}, got {self.side!r}")
            object.__setattr__(self, "side", normalized)
        if self.flat:
            if (
                self.side is not None
                or self.entry_price is not None
                or self.entry_index is not None
            ):
                raise ValueError("flat state must not carry side/entry_price/entry_index")
        elif self.side is None:
            raise ValueError("open state requires side 'LONG' or 'SHORT'")
        if self.entry_price is not None and (
            not isinstance(self.entry_price, (int, float))
            or isinstance(self.entry_price, bool)
            or not math.isfinite(float(self.entry_price))
        ):
            raise ValueError(f"entry_price must be finite or None, got {self.entry_price!r}")
        if self.entry_index is not None:
            if isinstance(self.entry_index, bool) or not isinstance(self.entry_index, int):
                raise ValueError(f"entry_index must be an int or None, got {self.entry_index!r}")
            if self.entry_index < 0:
                raise ValueError(f"entry_index must be >= 0, got {self.entry_index!r}")

    @staticmethod
    def open(side: str, entry_price: float, entry_index: int) -> "StrategyState":
        """State for an open position."""
        return StrategyState(
            flat=False, side=side, entry_price=entry_price, entry_index=entry_index
        )
