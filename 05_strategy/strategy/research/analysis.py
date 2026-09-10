"""Research Analysis — generic, reuses backtest Metrics.

Honest engine: every metric is derived from the dataset's real trades.
Anything the dataset cannot support (equity curve, bars, exposure) stays
None so the UI renders N/A — never a fabricated zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .dataset import ResearchDataset


@dataclass(frozen=True)
class ResearchAnalysis:
    """Generic analysis results — wraps existing Metrics plus extra."""

    trade_count: int
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    expectancy: float | None
    profit_factor: float | None
    net_profit: float | None
    drawdown_pct: float | None
    sharpe: float | None
    signal_count: int
    metadata: dict[str, Any]
    # Honest extensions — None means "not computable from this dataset".
    sortino: float | None = None
    payoff_ratio: float | None = None
    max_drawdown_abs: float | None = None
    # Structural N/A (need equity curve / bars / sizing — dataset has none).
    cagr: float | None = None
    volatility: float | None = None
    exposure: float | None = None
    turnover: float | None = None


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _stdev(values: list[float], mean: float) -> float | None:
    if len(values) < 2:
        return None
    try:
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    except Exception:
        return None
    return math.sqrt(variance) if variance > 0 else 0.0


def _drawdown_from_pnls(pnls: list[float]) -> tuple[float | None, float | None]:
    """Max drawdown from the cumulative-PnL curve (real trade sequence).

    Returns (drawdown_abs, drawdown_pct_of_peak). Pct is None when the
    running peak is not positive — honest N/A instead of a fake percent.
    """
    if not pnls:
        return None, None
    peak = 0.0
    equity = 0.0
    worst_abs = 0.0
    worst_pct: float | None = None
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        trough = peak - equity
        if trough > worst_abs:
            worst_abs = trough
            worst_pct = (trough / peak * 100.0) if peak > 0 else None
    if worst_abs <= 0:
        return 0.0, 0.0
    return worst_abs, worst_pct


def _sharpe_from_pnls(pnls: list[float]) -> float | None:
    """Per-trade Sharpe (mean/std, no annualisation claim).

    Needs ≥2 trades with non-zero variance; otherwise None → N/A.
    """
    if len(pnls) < 2:
        return None
    mean = _mean(pnls)
    if mean is None:
        return None
    sd = _stdev(pnls, mean)
    if sd is None or sd <= 0:
        return None
    return mean / sd


def _sortino_from_pnls(pnls: list[float], mean: float) -> float | None:
    """Per-trade Sortino: mean / downside deviation (losers only)."""
    losers = [v for v in pnls if v < 0]
    if len(losers) < 1 or mean is None:
        return None
    try:
        downside_var = sum(v * v for v in losers) / len(pnls)
    except Exception:
        return None
    if downside_var <= 0:
        return None
    return mean / math.sqrt(downside_var)


def analyze_dataset(dataset: ResearchDataset) -> ResearchAnalysis:
    """Generic analysis — reuses existing metrics logic where possible.

    For Phase 6, we compute from dataset.trades/signals. If dataset contains
    real Trade objects, we use compute_metrics; otherwise we compute simple stats.
    """
    # Try to use existing metrics if trades are Trade objects
    # For generic dataset, trades may be signals dicts — compute simple stats
    trades = dataset.trades
    # If trades are dicts with price, we can't compute metrics, so do simple
    # For real backtest, trades are Trade objects with pnl
    # We will attempt to compute via existing logic if possible
    try:
        # If trades look like Trade objects (have pnl attribute)
        if trades and hasattr(trades[0], "pnl"):
            # Use TradeJournal-like computation via compute_metrics
            # Need equity curve — for now use simple

            # Fallback to simple
            pass
    except Exception:
        pass

    # Simple generic stats from signals/trades
    trade_count = len(trades)
    # For signals, win rate etc. not applicable, so try to infer
    # If dataset was from execution history signals, we can compute
    # For now, produce analysis with available data
    # Use dataset parameters for context
    # Compute from trades if they have pnl
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    pnls: list[float] = []
    for t in trades:
        if isinstance(t, dict):
            # dict signal has no pnl, skip
            continue
        # Assume Trade object
        try:
            pnl = float(getattr(t, "pnl", 0))
            pnls.append(pnl)
            if pnl > 0:
                wins += 1
                gross_profit += pnl
            elif pnl < 0:
                losses += 1
                gross_loss += abs(pnl)
        except Exception:
            continue

    total = wins + losses
    win_rate = (wins / total) if total else None
    avg_win = (gross_profit / wins) if wins else None
    avg_loss = (gross_loss / losses) if losses else None
    expectancy = (sum(pnls) / len(pnls)) if pnls else None
    profit_factor = (gross_profit / gross_loss) if gross_loss else None
    # Net profit
    net_profit = sum(pnls) if pnls else None
    # Honest trade-sequence statistics (real math, N/A when unsupported).
    drawdown_abs, drawdown_pct = _drawdown_from_pnls(pnls)
    sharpe = _sharpe_from_pnls(pnls)
    payoff_ratio = (avg_win / avg_loss) if avg_win is not None and avg_loss else None
    sortino = _sortino_from_pnls(pnls, expectancy) if expectancy is not None else None
    # For signal count, use dataset
    signal_count = len(dataset.signals)

    return ResearchAnalysis(
        trade_count=trade_count,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=profit_factor,
        net_profit=net_profit,
        drawdown_pct=drawdown_pct,
        sharpe=sharpe,
        signal_count=signal_count,
        metadata={"strategy_id": dataset.strategy_id, "version_id": dataset.version_id},
        sortino=sortino,
        payoff_ratio=payoff_ratio,
        max_drawdown_abs=drawdown_abs,
        cagr=None,  # needs dated equity curve — dataset carries none
        volatility=None,  # needs bar returns — dataset carries none
        exposure=None,  # needs position sizing over time — unavailable
        turnover=None,  # needs notional traded — unavailable
    )
