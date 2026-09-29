"""BacktestForm + StrategyDraft — plain values exchanged between lab UI and wiring."""

import math
from dataclasses import dataclass
from datetime import date


def _require_non_empty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_non_negative_number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got {value!r}")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and >= 0, got {value!r}")
    return number


def _parse_iso_day(value: object, name: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


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
        max_position_size: Optional cap per position (None = uncapped).
    """

    strategy_id: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    slippage_pct: float
    commission_pct: float
    max_position_size: float | None = None

    def __post_init__(self) -> None:
        """Reject empty identity, negative costs and inverted ranges."""
        _require_non_empty(self.strategy_id, "strategy_id")
        _require_non_empty(self.timeframe, "timeframe")
        start = _parse_iso_day(self.start_date, "start_date")
        end = _parse_iso_day(self.end_date, "end_date")
        if end < start:
            raise ValueError(
                f"end_date ({self.end_date}) must not precede start_date ({self.start_date})"
            )
        _require_non_negative_number(self.initial_capital, "initial_capital")
        _require_non_negative_number(self.slippage_pct, "slippage_pct")
        _require_non_negative_number(self.commission_pct, "commission_pct")
        if self.max_position_size is not None:
            size = _require_non_negative_number(self.max_position_size, "max_position_size")
            if size <= 0:
                raise ValueError("max_position_size must be positive when set")


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

    def __post_init__(self) -> None:
        """Reject empty identity fields and out-of-range allocations."""
        _require_non_empty(self.name, "name")
        _require_non_empty(self.kind, "kind")
        allocation = _require_non_negative_number(self.allocation_pct, "allocation_pct")
        if allocation > 100.0:
            raise ValueError(f"allocation_pct must be within [0, 100], got {allocation!r}")
