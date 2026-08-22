"""Backtest engine subpackage."""

from backtest.engine.journal import TradeJournal
from backtest.engine.metrics import compute_equity_curve, compute_metrics
from backtest.engine.positions import PositionManager
from backtest.engine.replay import slice_bars
from backtest.engine.simulator import ExecutionSimulator

__all__ = [
    "TradeJournal",
    "PositionManager",
    "ExecutionSimulator",
    "slice_bars",
    "compute_equity_curve",
    "compute_metrics",
]
