"""Structured research report — evidence-based, reproducible.

Assembles the 18-section report from already-computed real inputs. Writes no
conclusion stronger than the evidence: language is graded
(supports / inconclusive / unstable / insufficient) from sample size,
significance, robustness and out-of-sample state.
"""

from __future__ import annotations

from typing import Any


def conclude(
    trade_count: int,
    p_value: float | None,
    robustness_fail: bool,
    has_oos: bool,
    oos_positive: bool | None,
) -> str:
    """One evidence-graded conclusion sentence (never stronger than inputs)."""
    if trade_count == 0:
        return "Insufficient sample size — no trades were executed, so no conclusion is possible."
    if trade_count < 30:
        return (
            f"Insufficient sample size (n={trade_count}) — observations are exploratory; "
            "evidence is inconclusive."
        )
    if robustness_fail:
        return (
            "Performance is unstable across tested dimensions — "
            "evidence does not support a robust edge."
        )
    if p_value is not None and p_value >= 0.05:
        return "Evidence is inconclusive — the expectancy interval includes zero."
    if has_oos and oos_positive is False:
        return "In-sample edge did not confirm out-of-sample — evidence is inconclusive."
    if has_oos and oos_positive:
        return (
            "Evidence supports a persistent edge — "
            "significant in-sample with out-of-sample confirmation."
        )
    return "Evidence supports an in-sample edge — out-of-sample confirmation still required."


def build_report(
    experiment: dict[str, Any],
    result_summary: dict[str, Any] | None = None,
    condition_summary: dict[str, Any] | None = None,
    robustness_summary: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
    benchmark: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the structured report (all sections data-driven)."""
    result_summary = dict(result_summary or {})
    stats = dict(stats or {})
    trade_count = int(result_summary.get("trade_count", 0) or 0)
    try:
        p_value = stats.get("p_value")
        p_value = float(p_value) if p_value is not None else None
    except (TypeError, ValueError):
        p_value = None
    robustness_fail = any(
        str(item.get("stability", "")).upper() == "FAIL" for item in (robustness_summary or [])
    )
    has_oos = bool((validation or {}).get("has_oos"))
    oos_positive = (validation or {}).get("oos_positive")
    conclusion = conclude(trade_count, p_value, robustness_fail, has_oos, oos_positive)
    hypothesis = experiment.get("hypothesis", {})
    hypothesis_text = (
        hypothesis.get("text", "") if isinstance(hypothesis, dict) else str(hypothesis or "")
    )
    return {
        "research_question": experiment.get("research_question", ""),
        "hypothesis": hypothesis_text,
        "strategy": {
            "strategy_id": experiment.get("strategy_id"),
            "strategy_version": experiment.get("strategy_version"),
        },
        "dataset": experiment.get("dataset", {}),
        "universe": experiment.get("configuration", {}).get("symbols", ()),
        "configuration": experiment.get("configuration", {}),
        "methodology": (
            "Canonical strategy logic executed over canonical market data via the shared "
            "backtest core; analysis, robustness and validation computed from the executed "
            "trade stream only."
        ),
        "signals": {"count": result_summary.get("signal_count", 0)},
        "trades": {"count": trade_count, "summary": result_summary},
        "performance": result_summary,
        "risk": {
            "max_drawdown_pct": result_summary.get("max_drawdown_pct"),
            "max_drawdown_abs": result_summary.get("max_drawdown_abs"),
            "sharpe": result_summary.get("sharpe"),
            "sortino": result_summary.get("sortino"),
        },
        "regime_analysis": (condition_summary or {}),
        "robustness": list(robustness_summary or []),
        "out_of_sample": (validation or {}).get("oos", {}),
        "statistical_evidence": stats,
        "benchmark": (benchmark or {}),
        "comparison": (comparison or {}),
        "limitations": list(limitations or []),
        "conclusion": conclusion,
        "reproducibility": experiment.get("reproducibility", {}),
    }
