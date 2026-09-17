"""Experiment comparison — real configuration and outcome deltas.

Compares persisted experiment summaries only. Compatibility warnings fire
whenever the two experiments differ in a dimension that makes naive
metric comparison misleading (strategy, timeframe, period, costs, universe).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_COMPARE_METRICS = (
    "trade_count",
    "net_pnl",
    "return_pct",
    "win_rate",
    "profit_factor",
    "expectancy",
    "max_drawdown_pct",
    "sharpe",
    "sortino",
)

_CONFIG_DIMS = (
    "strategy_id",
    "strategy_version",
    "universe",
    "symbols",
    "timeframe",
    "start_date",
    "end_date",
    "side",
    "parameters",
    "initial_capital",
    "slippage_pct",
    "commission_pct",
)


@dataclass(frozen=True)
class MetricDelta:
    """One metric across two experiments (None where either side lacks it)."""

    metric: str
    left: float | None
    right: float | None
    delta: float | None


@dataclass(frozen=True)
class ComparisonResult:
    """Full comparison of two persisted experiments."""

    left_id: str
    right_id: str
    config_differences: tuple[str, ...]
    metric_deltas: tuple[MetricDelta, ...]
    warnings: tuple[str, ...]
    verdict: str
    details: dict[str, Any] = field(default_factory=dict)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def compare_experiments(left: dict[str, Any], right: dict[str, Any]) -> ComparisonResult:
    """Compare two experiment dicts (as returned by storage ``to_dict``)."""
    left_id = str(left.get("experiment_id", "?"))
    right_id = str(right.get("experiment_id", "?"))
    left_cfg = left.get("configuration", {}) if isinstance(left.get("configuration"), dict) else {}
    right_cfg = (
        right.get("configuration", {}) if isinstance(right.get("configuration"), dict) else {}
    )
    differences: list[str] = []
    for dim in _CONFIG_DIMS:
        if left_cfg.get(dim) != right_cfg.get(dim):
            differences.append(dim)
    # Legacy records carry strategy identity top-level; treat mismatch as difference.
    for dim in ("strategy_id", "version_id"):
        if left.get(dim) != right.get(dim):
            name = "strategy_id" if dim == "strategy_id" else "strategy_version"
            if name not in differences:
                differences.append(name)

    left_res = (
        left.get("result_summary", {}) if isinstance(left.get("result_summary"), dict) else {}
    )
    right_res = (
        right.get("result_summary", {}) if isinstance(right.get("result_summary"), dict) else {}
    )
    deltas: list[MetricDelta] = []
    for metric in _COMPARE_METRICS:
        lval = _number(left_res.get(metric))
        rval = _number(right_res.get(metric))
        delta = (rval - lval) if (lval is not None and rval is not None) else None
        deltas.append(MetricDelta(metric=metric, left=lval, right=rval, delta=delta))

    warnings: list[str] = []
    for dim in ("strategy_id", "timeframe", "symbols", "universe", "start_date", "end_date"):
        if dim in differences:
            warnings.append(f"different {dim} — compare with caution")
    for dim in ("slippage_pct", "commission_pct", "initial_capital"):
        if dim in differences:
            warnings.append(
                f"different cost/capital assumption ({dim}) — P&L not directly comparable"
            )
    if not left_res or not right_res:
        warnings.append("one experiment has no executed result — comparison is structural only")

    if not left_res or not right_res:
        verdict = "INCOMPARABLE — missing executed result"
    elif warnings and any("strategy_id" in w or "timeframe" in w for w in warnings):
        verdict = "DIVERGENT SETUP — deltas reflect configuration, not edge"
    else:
        pnl = next((d for d in deltas if d.metric == "net_pnl"), None)
        if pnl is not None and pnl.delta is not None:
            verdict = (
                f"{right_id} ahead by {pnl.delta:,.2f}"
                if pnl.delta > 0
                else (
                    f"{left_id} ahead by {abs(pnl.delta):,.2f}"
                    if pnl.delta < 0
                    else "equal net P&L"
                )
            )
        else:
            verdict = "compared structurally — no common P&L basis"
    return ComparisonResult(
        left_id=left_id,
        right_id=right_id,
        config_differences=tuple(differences),
        metric_deltas=tuple(deltas),
        warnings=tuple(warnings),
        verdict=verdict,
        details={"left_status": left.get("status"), "right_status": right.get("status")},
    )
