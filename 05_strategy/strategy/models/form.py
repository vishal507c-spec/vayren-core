"""BacktestForm + StrategyDraft — plain values exchanged between lab UI and wiring."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestForm:
    """The strategy-lab control form, exactly as configured by the user.

    The panel emits this; the composition root validates it, attaches the
    current chart symbol and turns it into a :class:`RunBacktest` request.

    Attributes:
        strategy_id: Active strategy definition id.
        timeframe: Requested bar timeframe label (e.g. ``"15m"``).
        start_date: Range start (ISO date string).
        end_date: Range end (ISO date string).
        initial_capital: Starting capital in currency units.
        slippage_pct: Slippage applied per fill, in percent.
        commission_pct: Commission applied per fill, in percent.
    """

    strategy_id: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    slippage_pct: float
    commission_pct: float


@dataclass(frozen=True)
class StrategyDraft:
    """Raw strategy-dialog input before registry validation.

    Attributes:
        name: Display name chosen by the user.
        version: Version string.
        kind: Registered logic kind.
        params: Raw parameter values keyed by parameter key.
        allocation_pct: Allocation weight in percent (0-100).
    """

    name: str
    version: str
    kind: str
    params: dict[str, float]
    allocation_pct: float
