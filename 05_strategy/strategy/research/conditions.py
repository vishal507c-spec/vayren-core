"""Condition / regime / symbol analysis — real breakdowns of executed trades.

Every bucket is derived from the experiment's actual ``TradeRecord`` stream
(plus the executed bar windows when regime context is requested). Dimensions
that need data the experiment does not carry are reported as unsupported —
never invented.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ConditionBucket:
    """One bucket of trades within a dimension (real observed economics)."""

    dimension: str
    key: str
    n: int
    total_pnl: float
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    avg_win: float | None = None
    avg_loss: float | None = None


@dataclass(frozen=True)
class SymbolRow:
    """Per-symbol performance row (real experiment data)."""

    symbol: str
    trades: int
    net_pnl: float
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    avg_trade: float | None
    max_drawdown_abs: float | None
    sharpe: float | None


@dataclass(frozen=True)
class ConditionAnalysis:
    """Full condition breakdown for one executed experiment."""

    buckets: dict[str, tuple[ConditionBucket, ...]] = field(default_factory=dict)
    symbols: tuple[SymbolRow, ...] = ()
    unsupported: tuple[str, ...] = ()
    trade_count: int = 0


def _pnl(trade: Any) -> float:
    if isinstance(trade, dict):
        try:
            return float(trade.get("pnl", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(getattr(trade, "pnl", 0))
    except (TypeError, ValueError):
        return 0.0


def _get(trade: Any, key: str, default: Any = None) -> Any:
    if isinstance(trade, dict):
        return trade.get(key, default)
    return getattr(trade, key, default)


def _parse_day(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp))
    except ValueError:
        try:
            return datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _summarize(dimension: str, key: str, pnls: list[float]) -> ConditionBucket:
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = sum(abs(p) for p in losses)
    return ConditionBucket(
        dimension=dimension,
        key=key,
        n=n,
        total_pnl=sum(pnls),
        win_rate=(len(wins) / n) if n else None,
        profit_factor=(gross_profit / gross_loss) if gross_loss else None,
        expectancy=(sum(pnls) / n) if n else None,
        avg_win=(gross_profit / len(wins)) if wins else None,
        avg_loss=(gross_loss / len(losses)) if losses else None,
    )


def _bucketize(trades: list[Any], dimension: str, key_of: Any) -> tuple[ConditionBucket, ...]:
    groups: dict[str, list[float]] = {}
    for trade in trades:
        key = key_of(trade)
        groups.setdefault(str(key), []).append(_pnl(trade))
    return tuple(_summarize(dimension, key, pnls) for key, pnls in sorted(groups.items()))


_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _duration_bucket(bars_held: Any) -> str:
    try:
        held = int(bars_held)
    except (TypeError, ValueError):
        return "unknown"
    if held <= 5:
        return "0-5 bars"
    if held <= 20:
        return "6-20 bars"
    if held <= 60:
        return "21-60 bars"
    return "61+ bars"


def analyze_conditions(
    trades: list[Any] | tuple[Any, ...],
    windows: dict[str, tuple[Any, ...]] | None = None,
) -> ConditionAnalysis:
    """Break executed trades down by every honestly supported dimension.

    Always supported (from ``TradeRecord`` fields): SYMBOL, SIDE,
    EXIT_REASON, YEAR, MONTH, DAY_OF_WEEK, ENTRY_HOUR, DURATION.
    With ``windows`` (symbol -> executed bar window in execution order):
    VOLATILITY_REGIME (trailing-20 stdev tercile at entry) and TREND_REGIME
    (entry close vs trailing-20 mean). Without windows both are listed as
    unsupported. GAP / OPENING_RANGE / VOLUME / SECTOR regimes need data the
    trade stream does not carry and are always reported unsupported.
    """
    trades = list(trades or [])
    buckets: dict[str, tuple[ConditionBucket, ...]] = {}
    if trades:
        buckets["SYMBOL"] = _bucketize(trades, "SYMBOL", lambda t: _get(t, "symbol", "?"))
        buckets["SIDE"] = _bucketize(trades, "SIDE", lambda t: _get(t, "side", "?"))
        buckets["EXIT_REASON"] = _bucketize(
            trades, "EXIT_REASON", lambda t: _get(t, "exit_reason", "?")
        )

        def _year(trade: Any) -> str:
            day = _parse_day(str(_get(trade, "entry_time", "") or ""))
            return str(day.year) if day else "unknown"

        def _month(trade: Any) -> str:
            day = _parse_day(str(_get(trade, "entry_time", "") or ""))
            return day.strftime("%Y-%m") if day else "unknown"

        def _dow(trade: Any) -> str:
            day = _parse_day(str(_get(trade, "entry_time", "") or ""))
            return _DOW[day.weekday()] if day else "unknown"

        def _hour(trade: Any) -> str:
            day = _parse_day(str(_get(trade, "entry_time", "") or ""))
            return f"{day.hour:02d}:00" if day else "unknown"

        buckets["YEAR"] = _bucketize(trades, "YEAR", _year)
        buckets["MONTH"] = _bucketize(trades, "MONTH", _month)
        buckets["DAY_OF_WEEK"] = _bucketize(trades, "DAY_OF_WEEK", _dow)
        buckets["ENTRY_HOUR"] = _bucketize(trades, "ENTRY_HOUR", _hour)
        buckets["DURATION"] = _bucketize(
            trades, "DURATION", lambda t: _duration_bucket(_get(t, "bars_held", None))
        )

    unsupported = [
        "GAP_SIZE (requires session open/gap series)",
        "OPENING_RANGE_SIZE (requires intraday opening-range series)",
        "VOLUME_REGIME (requires per-bar volume context)",
        "SECTOR (requires symbol sector mapping)",
        "BULL_BEAR (requires benchmark series)",
    ]
    if windows:
        vol = _volatility_regimes(trades, windows)
        trend = _trend_regimes(trades, windows)
        if vol:
            buckets["VOLATILITY_REGIME"] = vol
        else:
            unsupported.insert(0, "VOLATILITY_REGIME (entry bar outside supplied windows)")
        if trend:
            buckets["TREND_REGIME"] = trend
        else:
            unsupported.insert(0, "TREND_REGIME (entry bar outside supplied windows)")
    else:
        unsupported.insert(0, "VOLATILITY_REGIME (requires executed bar windows)")
        unsupported.insert(0, "TREND_REGIME (requires executed bar windows)")

    symbols = _symbol_rows(trades)
    return ConditionAnalysis(
        buckets=buckets, symbols=symbols, unsupported=tuple(unsupported), trade_count=len(trades)
    )


def _closes(window: tuple[Any, ...]) -> list[float]:
    out: list[float] = []
    for bar in window:
        try:
            out.append(float(bar.close if not isinstance(bar, dict) else bar.get("close", 0)))
        except (TypeError, ValueError, AttributeError):
            out.append(0.0)
    return out


def _trailing_stats(
    trades: list[Any], windows: dict[str, tuple[Any, ...]], lookback: int = 20
) -> list[tuple[float, float] | None]:
    """Per-trade (volatility, trend_gap) at entry from real bar windows."""
    cache: dict[str, list[float]] = {}
    out: list[tuple[float, float] | None] = []
    for trade in trades:
        symbol = str(_get(trade, "symbol", "") or "")
        window = windows.get(symbol)
        if not window:
            out.append(None)
            continue
        if symbol not in cache:
            cache[symbol] = _closes(tuple(window))
        closes = cache[symbol]
        try:
            idx = int(_get(trade, "entry_index", -1))
        except (TypeError, ValueError):
            out.append(None)
            continue
        start = idx - lookback
        if start < 0 or idx > len(closes) or idx <= 0:
            out.append(None)
            continue
        trail = closes[start:idx]
        if len(trail) < 5:
            out.append(None)
            continue
        mean = sum(trail) / len(trail)
        var = sum((c - mean) ** 2 for c in trail) / len(trail)
        vol = math.sqrt(var) / mean if mean else 0.0
        gap = (closes[idx] - mean) / mean if mean else 0.0
        out.append((vol, gap))
    return out


def _volatility_regimes(
    trades: list[Any], windows: dict[str, tuple[Any, ...]]
) -> tuple[ConditionBucket, ...]:
    stats = _trailing_stats(trades, windows)
    vols = sorted(s for s in stats if s is not None for s in [s[0]])
    if len(vols) < 6:
        return ()
    lo, hi = vols[len(vols) // 3], vols[2 * len(vols) // 3]
    groups: dict[str, list[float]] = {"LOW": [], "MED": [], "HIGH": []}
    for trade, stat in zip(trades, stats, strict=False):
        if stat is None:
            continue
        vol = stat[0]
        label = "LOW" if vol <= lo else ("HIGH" if vol >= hi else "MED")
        groups[label].append(_pnl(trade))
    return tuple(_summarize("VOLATILITY_REGIME", k, groups[k]) for k in ("LOW", "MED", "HIGH"))


def _trend_regimes(
    trades: list[Any], windows: dict[str, tuple[Any, ...]]
) -> tuple[ConditionBucket, ...]:
    stats = _trailing_stats(trades, windows)
    groups: dict[str, list[float]] = {"UP": [], "DOWN": []}
    seen = False
    for trade, stat in zip(trades, stats, strict=False):
        if stat is None:
            continue
        seen = True
        groups["UP" if stat[1] >= 0 else "DOWN"].append(_pnl(trade))
    if not seen:
        return ()
    return tuple(_summarize("TREND_REGIME", k, groups[k]) for k in ("UP", "DOWN"))


def _drawdown_abs(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _sharpe(pnls: list[float]) -> float | None:
    if len(pnls) < 2:
        return None
    mean = sum(pnls) / len(pnls)
    var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var)


def _symbol_rows(trades: list[Any]) -> tuple[SymbolRow, ...]:
    by_symbol: dict[str, list[Any]] = {}
    for trade in trades:
        by_symbol.setdefault(str(_get(trade, "symbol", "?")), []).append(trade)
    rows: list[SymbolRow] = []
    for symbol in sorted(by_symbol):
        group = by_symbol[symbol]
        pnls = [_pnl(t) for t in group]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = sum(abs(p) for p in losses)
        rows.append(
            SymbolRow(
                symbol=symbol,
                trades=len(group),
                net_pnl=sum(pnls),
                win_rate=(len(wins) / len(group)) if group else None,
                profit_factor=(gross_profit / gross_loss) if gross_loss else None,
                expectancy=(sum(pnls) / len(pnls)) if pnls else None,
                avg_trade=(sum(pnls) / len(pnls)) if pnls else None,
                max_drawdown_abs=_drawdown_abs(pnls),
                sharpe=_sharpe(pnls),
            )
        )
    return tuple(rows)
