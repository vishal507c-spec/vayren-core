"""Directional result derivation — filter trades by side and recompute metrics.

Pure post-processing over a :class:`StrategyResult`. No trading logic,
no persistence, no new calculation formulas — reuses :func:`compute_metrics`
and :func:`compute_equity_curve` so numbers stay identical to the engine.
"""

from __future__ import annotations

from typing import Any

from backtest.engine.metrics import compute_equity_curve, compute_metrics
from backtest.models.result import StrategyResult


def _filter_trades(trades: tuple, side: str):  # type: ignore[no-untyped-def]
    wanted = side.upper()
    return tuple(t for t in trades if getattr(t, "side", "") == wanted)


def derive_directional_result(base: StrategyResult | None, side: str) -> StrategyResult | None:
    """Return a view of *base* containing only *side* trades.

    When *base* is None (no backtest yet) the result is None.
    When the side has zero trades the result still exists — metrics will
    report ``total_trades == 0`` and callers render ``--``. This matches
    the engine's own zero-trade metrics and avoids inventing numbers.
    """
    if base is None:
        return None
    side_u = side.upper()
    if side_u not in ("LONG", "SHORT"):
        raise ValueError(f"side must be LONG or SHORT, got {side!r}")
    filtered = _filter_trades(base.trades, side_u)
    # Recompute equity curve from filtered trades using same helpers
    start = base.period_start or (base.equity_curve[0].timestamp if base.equity_curve else None)
    curve = compute_equity_curve(filtered, base.config.initial_capital, start)
    metrics = compute_metrics(filtered, curve, base.config.initial_capital)
    # Preserve period / bars_used / chart_series / chart_plots (plotting is direction-agnostic)
    return StrategyResult(
        strategy_id=base.strategy_id,
        name=base.name,
        config=base.config,
        trades=filtered,
        equity_curve=curve,
        metrics=metrics,
        bars_used=base.bars_used,
        period_start=base.period_start,
        period_end=base.period_end,
        chart_series=base.chart_series,
        chart_plots=base.chart_plots,
        muted_bars=base.muted_bars,
    )


def split_by_side(
    base: StrategyResult | None,
) -> tuple[StrategyResult | None, StrategyResult | None]:
    """Return ``(buy_result, sell_result)`` for *base*.

    BUY == LONG, SELL == SHORT.
    """
    if base is None:
        return None, None
    return derive_directional_result(base, "LONG"), derive_directional_result(base, "SHORT")


def derive_symbol_result(base: StrategyResult | None, symbol: str) -> StrategyResult | None:
    """Return a view of *base* containing only *symbol* trades.

    Same recompute helpers as the directional split so numbers stay identical
    to the engine. Used for per-symbol research filtering of multi-symbol
    backtests; ``TradeRecord.symbol`` is the single source of truth.
    """
    if base is None:
        return None
    filtered = tuple(t for t in base.trades if getattr(t, "symbol", "") == symbol)
    return _symbol_view(base, filtered)


def derive_symbol_results(
    base: StrategyResult | None, symbols: tuple[str, ...] | list[str]
) -> dict[str, StrategyResult]:
    """Return per-symbol views for *symbols* in a single pass over trades.

    Identical math to calling :func:`derive_symbol_result` per symbol (same
    helpers, same start timestamp, same field assembly) but groups the
    merged trades once instead of rescanning them per symbol — ranking
    527 symbols drops from tens of seconds to well under one.
    """
    if base is None:
        return {}
    wanted = set(symbols)
    grouped: dict[str, list] = {}
    for trade in base.trades:
        symbol = getattr(trade, "symbol", "")
        if symbol in wanted:
            grouped.setdefault(symbol, []).append(trade)
    return {symbol: _symbol_view(base, tuple(grouped.get(symbol, ()))) for symbol in symbols}


def _symbol_view(base: StrategyResult, filtered: tuple[Any, ...]) -> StrategyResult:
    """Assemble a per-symbol view from already-filtered trades (shared core)."""
    start = base.period_start or (base.equity_curve[0].timestamp if base.equity_curve else None)
    curve = compute_equity_curve(filtered, base.config.initial_capital, start)
    metrics = compute_metrics(filtered, curve, base.config.initial_capital)
    return StrategyResult(
        strategy_id=base.strategy_id,
        name=base.name,
        config=base.config,
        trades=filtered,
        equity_curve=curve,
        metrics=metrics,
        bars_used=base.bars_used,
        period_start=base.period_start,
        period_end=base.period_end,
        chart_series=base.chart_series,
        chart_plots=base.chart_plots,
        muted_bars=base.muted_bars,
    )
