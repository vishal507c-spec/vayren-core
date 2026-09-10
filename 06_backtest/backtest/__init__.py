"""Backtest platform — replay, execution, positions, journal and results.

Depends on: core, market, strategy.
"""

from backtest.engine.directional import (
    derive_directional_result,
    derive_symbol_result,
    derive_symbol_results,
    split_by_side,
)
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
from backtest.runner import (
    BacktestRunner,
    BatchSpec,
    SymbolBatchResult,
    default_batch_workers,
    execute_bars,
    run_symbol_batch,
    run_variant_backtest,
)
from backtest.validation import validate_backtest_form
from backtest.worker import BacktestWorker, BatchEnqueued

__all__ = [
    "BacktestRunner",
    "run_variant_backtest",
    "BatchSpec",
    "SymbolBatchResult",
    "default_batch_workers",
    "execute_bars",
    "run_symbol_batch",
    "BacktestWorker",
    "BatchEnqueued",
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
    "derive_directional_result",
    "derive_symbol_result",
    "derive_symbol_results",
    "split_by_side",
]
