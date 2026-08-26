"""BacktestResult / StrategyResult — one backtest execution's outputs."""

from dataclasses import dataclass

from backtest.models.config import BacktestConfig
from backtest.models.equity import EquityPoint
from backtest.models.metrics import PerformanceMetrics
from backtest.models.trade import TradeRecord


@dataclass(frozen=True)
class ChartSeries:
    """One plot series for chart rendering — stable identity per title.

    Attributes:
        title: Series name (e.g., "REF HIGH").
        values: Mapping bar_index -> value (only bars where plot was called).
        style: Optional style hint ("line", "dots", etc.).
        extend: Optional extend hint ("session", "none", etc.).
        strategy: Strategy name/id that produced this series (for visibility).
    """

    title: str
    values: tuple[tuple[int, float], ...]
    style: str = "line"
    extend: str = "session"
    strategy: str = ""


@dataclass(frozen=True)
class StrategyResult:
    """Outputs of one strategy's isolated run.

    Attributes:
        strategy_id: Definition id.
        name: Display name including version (``"SMA Crossover v1.0"``).
        config: The real inputs that produced this result.
        trades: Closed positions in entry order.
        equity_curve: Curve including the starting point as index 0.
        metrics: The 8 displayed metrics plus supporting aggregates.
        bars_used: Number of bars that survived the date-range slice.
        period_start: First bar timestamp in the slice.
        period_end: Last bar timestamp in the slice.
        chart_series: Plot series for chart rendering (stable identity per title).
    """

    strategy_id: str
    name: str
    config: BacktestConfig
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: PerformanceMetrics
    bars_used: int
    period_start: str | None
    period_end: str | None
    chart_series: tuple[ChartSeries, ...] = ()


@dataclass(frozen=True)
class BacktestResult:
    """One lab run: per-strategy results together with common metadata.

    Attributes:
        results: Per-strategy results (order of the request's ids).
        has_error: True when at least one requested strategy produced no
            bars or failed validation — callers surface a user-visible
            reason rather than fabricating empty metrics.
        error: Machine-readable error code when ``has_error`` (else None).
        error_detail: User-facing detail attached to the error.
    """

    results: tuple[StrategyResult, ...]
    has_error: bool = False
    error: str | None = None
    error_detail: str | None = None
