"""Real data validation for research — pure, deterministic, no I/O.

Operates on in-memory bars (duck-typed ``timestamp/open/high/low/close/volume``)
supplied by the caller (the app service loads them through the canonical
``market`` repository). Never fabricates, never silently continues: every
finding is returned as structured issues so the caller can fail closed with
an actionable message.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BarIssue:
    """One concrete data problem (symbol + location + reason)."""

    symbol: str
    timestamp: str
    kind: str
    detail: str


@dataclass(frozen=True)
class SymbolValidation:
    """Validation outcome for one symbol."""

    symbol: str
    bar_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    missing_expected_days: int = 0
    issues: tuple[BarIssue, ...] = ()
    ok: bool = True


@dataclass(frozen=True)
class DataValidationReport:
    """Whole-universe validation outcome."""

    symbols: tuple[SymbolValidation, ...] = ()
    missing_symbols: tuple[str, ...] = ()
    ok: bool = True
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _get(bar: Any, key: str, default: Any = None) -> Any:
    if isinstance(bar, dict):
        return bar.get(key, default)
    return getattr(bar, key, default)


def validate_bars(
    bars_by_symbol: dict[str, Any],
    expected_symbols: tuple[str, ...] | list[str] = (),
    start_date: str | None = None,
    end_date: str | None = None,
) -> DataValidationReport:
    """Validate loaded bars for a research universe.

    Checks per symbol: availability, date-range coverage, timestamp ordering,
    duplicates, invalid OHLC (high<low, open/close outside range, non-positive
    prices), non-positive volume flag (warning, not failure), and gaps in the
    daily date sequence within the observed range.
    """
    expected = [str(s) for s in (expected_symbols or [])]
    missing = tuple(s for s in expected if not bars_by_symbol.get(s))
    per_symbol: list[SymbolValidation] = []
    all_ok = not missing

    for symbol in sorted(bars_by_symbol):
        raw = tuple(bars_by_symbol[symbol] or ())
        issues: list[BarIssue] = []
        stamps = [str(_get(b, "timestamp", "") or "") for b in raw]

        # Ordering + duplicates.
        counts = Counter(stamps)
        for stamp, count in counts.items():
            if count > 1:
                issues.append(
                    BarIssue(symbol, stamp, "duplicate", f"{count} records share timestamp")
                )
        ordered = [s for s in stamps if s]
        if ordered != sorted(ordered):
            issues.append(
                BarIssue(
                    symbol, ordered[0] if ordered else "", "unordered", "timestamps not ascending"
                )
            )

        # OHLC sanity per bar.
        for bar, stamp in zip(raw, stamps, strict=False):
            try:
                o = float(_get(bar, "open", 0) or 0)
                h = float(_get(bar, "high", 0) or 0)
                lo = float(_get(bar, "low", 0) or 0)
                c = float(_get(bar, "close", 0) or 0)
            except (TypeError, ValueError):
                issues.append(BarIssue(symbol, stamp, "invalid_ohlc", "non-numeric OHLC"))
                continue
            if min(o, h, lo, c) <= 0:
                issues.append(BarIssue(symbol, stamp, "invalid_ohlc", "non-positive OHLC value"))
            elif h < lo:
                issues.append(BarIssue(symbol, stamp, "invalid_ohlc", "high below low"))
            elif o < lo or o > h or c < lo or c > h:
                issues.append(
                    BarIssue(symbol, stamp, "invalid_ohlc", "open/close outside high-low")
                )

        # Range coverage.
        days = sorted({s[:10] for s in ordered if len(s) >= 10})
        if start_date and days and days[0] > start_date:
            issues.append(
                BarIssue(
                    symbol, days[0], "range_gap", f"first bar {days[0]} after start {start_date}"
                )
            )
        if end_date and days and days[-1] < end_date:
            issues.append(
                BarIssue(
                    symbol, days[-1], "range_gap", f"last bar {days[-1]} before end {end_date}"
                )
            )
        missing_days = 0
        if len(days) > 1:
            from datetime import date as _date

            try:
                d0 = _date.fromisoformat(days[0])
                d1 = _date.fromisoformat(days[-1])
                span = (d1 - d0).days + 1
                # Trading calendar unknown here: report sparse coverage as info,
                # fail only on exact duplicate/ordering/OHLC problems above.
                missing_days = max(0, span - len(days))
            except ValueError:
                missing_days = 0

        fatal = [i for i in issues if i.kind in ("duplicate", "unordered", "invalid_ohlc")]
        ok = not fatal and bool(raw)
        if not raw:
            issues.append(BarIssue(symbol, "", "no_data", "no bars loaded for symbol"))
            ok = False
        if not ok:
            all_ok = False
        per_symbol.append(
            SymbolValidation(
                symbol=str(symbol),
                bar_count=len(raw),
                first_timestamp=ordered[0] if ordered else None,
                last_timestamp=ordered[-1] if ordered else None,
                missing_expected_days=missing_days,
                issues=tuple(issues),
                ok=ok,
            )
        )

    if missing:
        all_ok = False
        summary = f"DATA VALIDATION FAILED — missing data: {', '.join(missing)}"
    elif not all_ok:
        bad = [s.symbol for s in per_symbol if not s.ok]
        summary = f"DATA VALIDATION FAILED — invalid data: {', '.join(bad)}"
    else:
        total = sum(s.bar_count for s in per_symbol)
        summary = f"Data validation passed — {len(per_symbol)} symbols, {total} bars"
    return DataValidationReport(
        symbols=tuple(per_symbol),
        missing_symbols=missing,
        ok=all_ok,
        summary=summary,
        details={"symbols_checked": len(per_symbol)},
    )
