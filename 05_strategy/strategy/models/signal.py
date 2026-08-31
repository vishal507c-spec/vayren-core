"""Signal — a single strategy decision emitted at one bar."""

from dataclasses import dataclass
from enum import Enum


class SignalKind(Enum):
    """The action a strategy requests for one bar."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Signal:
    """One strategy decision: enter (or reverse) at the current bar.

    Attributes:
        index: Bar index inside the replayed window.
        timestamp: Bar timestamp (ISO-like string from the Bar model).
        kind: BUY (open long) or SELL (close/exit or open short).
        price: Reference price (the bar close that produced the signal).
        stop_loss: Optional stop-loss level attached to the entry.
        take_profit: Optional take-profit level attached to the entry.
    """

    index: int
    timestamp: str
    kind: SignalKind
    price: float
    stop_loss: float | None = None
    take_profit: float | None = None
