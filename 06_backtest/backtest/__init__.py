"""Backtest platform — replay, execution, positions, journal and results.

Depends on: core, market, strategy.
"""

from backtest.events import (
    BacktestCompleted,
    BacktestFailed,
    BacktestProgress,
    BacktestStarted,
    RunBacktest,
)
from backtest.manifest import backtest_manifest
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PerformanceMetrics,
    StrategyResult,
    TradeRecord,
)
from backtest.runner import BacktestRunner
from backtest.validation import validate_backtest_form
from backtest.worker import BacktestWorker

__all__ = [
    "BacktestRunner",
    "BacktestWorker",
    "BacktestConfig",
    "BacktestResult",
    "StrategyResult",
    "TradeRecord",
    "EquityPoint",
    "PerformanceMetrics",
    "RunBacktest",
    "BacktestStarted",
    "BacktestProgress",
    "BacktestCompleted",
    "BacktestFailed",
    "backtest_manifest",
    "validate_backtest_form",
]
