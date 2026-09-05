"""BacktestConfig — immutable inputs to one backtest run."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestConfig:
    """User-validated inputs to one isolated backtest execution.

    Attributes:
        symbol: Traded symbol (e.g. ``"TCS"``).
        timeframe: Bar timeframe label (e.g. ``"15m"``).
        start_date: Date-range start (ISO ``YYYY-MM-DD``).
        end_date: Date-range end (ISO ``YYYY-MM-DD``).
        initial_capital: Starting account equity.
        slippage_pct: Slippage per fill, in percent (0-5).
        commission_pct: Commission per fill, in percent (0-5).
        max_position_size: Optional cap per position in currency units
            (None = uncapped, preserves previous behavior).
    """

    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float = 1_000_000.0
    slippage_pct: float = 0.02
    commission_pct: float = 0.03
    max_position_size: float | None = None
