"""Backtest events."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class RunBacktest(Event):
    """Request: run a backtest for one or more strategies."""

    request_id: str
    strategy_ids: tuple[str, ...]
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float = 1_000_000.0
    slippage_pct: float = 0.02
    commission_pct: float = 0.03


@dataclass(frozen=True)
class BacktestStarted(Event):
    """A backtest run started."""

    request_id: str
    strategy_ids: tuple[str, ...]
    symbol: str
    timeframe: str


@dataclass(frozen=True)
class BacktestProgress(Event):
    """Periodic progress during a backtest run."""

    request_id: str
    processed: int
    total: int


@dataclass(frozen=True)
class BacktestCompleted(Event):
    """A backtest finished successfully with per-strategy results."""

    request_id: str
    result: object  # BacktestResult — object to avoid import cycle for pyright


@dataclass(frozen=True)
class BacktestFailed(Event):
    """A backtest could not be produced."""

    request_id: str
    reason: str
