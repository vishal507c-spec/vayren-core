"""Out-of-sample splits and walk-forward windows — pure date/trade arithmetic.

Splitting is chronological (execution order for trades, calendar order for
windows). Execution of each window stays with the caller (the app service
re-runs the canonical backtest per window); this module only derives the
splits and aggregates honestly reported per-fold numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class WindowSpec:
    """One walk-forward fold (calendar dates, ISO ``YYYY-MM-DD``)."""

    fold: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class WalkForwardSummary:
    """Aggregation of honestly reported per-fold test economics."""

    folds: int
    test_expectancies: tuple[float | None, ...]
    positive_folds: int
    mean_test_expectancy: float | None
    dispersion: float | None
    status: str
    note: str


def split_trades_chronological(
    trades: list[Any] | tuple[Any, ...], is_fraction: float = 0.7
) -> tuple[list[Any], list[Any]]:
    """Split the executed trade stream into in-sample / out-of-sample.

    Trades are in execution (chronological) order, so a prefix/suffix split
    is a true temporal split. Never shuffles.
    """
    trades = list(trades or [])
    frac = min(0.9, max(0.1, float(is_fraction)))
    cut = int(len(trades) * frac)
    return trades[:cut], trades[cut:]


def walk_forward_windows(
    start_date: str, end_date: str, n_windows: int = 3, train_fraction: float = 0.7
) -> tuple[WindowSpec, ...]:
    """Partition [start, end] into expanding-train / rolling-test folds.

    The test region (last ``1 - train_fraction`` of the span) is divided into
    ``n_windows`` contiguous blocks; fold ``i`` trains on everything before
    its test block. Deterministic date arithmetic, no data access.
    """
    try:
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
    except ValueError:
        return ()
    total_days = (end - start).days + 1
    windows = max(1, min(int(n_windows), 12))
    if total_days < windows + 1:
        return ()
    frac = min(0.9, max(0.5, float(train_fraction)))
    train_days = max(1, int(total_days * frac))
    test_days = total_days - train_days
    base = test_days // windows
    remainder = test_days % windows
    specs: list[WindowSpec] = []
    cursor = train_days
    for fold in range(windows):
        length = base + (1 if fold < remainder else 0)
        if length <= 0:
            continue
        test_start_day = start.toordinal() + cursor
        test_end_day = test_start_day + length - 1
        specs.append(
            WindowSpec(
                fold=fold + 1,
                train_start=start.isoformat(),
                train_end=date.fromordinal(test_start_day - 1).isoformat(),
                test_start=date.fromordinal(test_start_day).isoformat(),
                test_end=date.fromordinal(test_end_day).isoformat(),
            )
        )
        cursor += length
    return tuple(specs)


def summarize_walkforward(fold_test_expectancies: list[float | None]) -> WalkForwardSummary:
    """Aggregate per-fold test expectancies (each from a real execution)."""
    values = [v for v in fold_test_expectancies if v is not None]
    if not values:
        return WalkForwardSummary(
            folds=len(fold_test_expectancies),
            test_expectancies=tuple(fold_test_expectancies),
            positive_folds=0,
            mean_test_expectancy=None,
            dispersion=None,
            status="INSUFFICIENT",
            note="no fold produced a test expectancy",
        )
    mean = sum(values) / len(values)
    if len(values) > 1:
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        import math as _math

        disp = _math.sqrt(var) if var > 0 else 0.0
    else:
        disp = 0.0
    positive = sum(1 for v in values if v > 0)
    if positive == len(values):
        status = "CONSISTENT"
    elif positive == 0:
        status = "FAILING"
    else:
        status = "MIXED"
    return WalkForwardSummary(
        folds=len(fold_test_expectancies),
        test_expectancies=tuple(fold_test_expectancies),
        positive_folds=positive,
        mean_test_expectancy=mean,
        dispersion=disp,
        status=status,
        note=f"{positive}/{len(values)} test folds positive",
    )
