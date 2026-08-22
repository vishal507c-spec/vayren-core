"""EquityPoint — one point on the equity curve."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityPoint:
    """One equity observation: at one exit (or the starting point).

    Attributes:
        timestamp: Bar timestamp of this point (start uses first bar's time).
        equity: Account equity after this point.
        drawdown_pct: Drawdown from the running equity peak at this point,
            in percent (0.0 when at a peak).
    """

    timestamp: str
    equity: float
    drawdown_pct: float = 0.0
