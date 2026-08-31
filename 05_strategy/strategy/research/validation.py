"""Validation — evidence-driven, separate from discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .analysis import ResearchAnalysis
from .robustness import RobustnessResult


@dataclass(frozen=True)
class ValidationResult:
    """Generic validation result."""

    status: str  # PASS/WARNING/FAIL
    evidence: dict[str, Any]
    summary: str
    details: dict[str, Any]


def validate_experiment(
    analysis: ResearchAnalysis,
    robustness: list[RobustnessResult],
) -> ValidationResult:
    """Generic validation — not a single metric.

    Checks multiple evidence dimensions.
    """
    # Collect evidence
    evidence: dict[str, Any] = {
        "trade_count": analysis.trade_count,
        "win_rate": analysis.win_rate,
        "expectancy": analysis.expectancy,
        "profit_factor": analysis.profit_factor,
        "robustness": [{"test": r.test_type, "stability": r.stability} for r in robustness],
    }
    # Simple rules
    fail_reasons: list[str] = []
    warn_reasons: list[str] = []

    if analysis.trade_count < 10:
        warn_reasons.append("Low trade count (<10) — insufficient evidence")
    if analysis.win_rate is not None and analysis.win_rate < 0.4:
        warn_reasons.append(f"Low win rate {analysis.win_rate:.1%}")
    if analysis.expectancy is not None and analysis.expectancy <= 0:
        fail_reasons.append("Negative expectancy")
    if analysis.profit_factor is not None and analysis.profit_factor < 1.0:
        warn_reasons.append(f"Profit factor {analysis.profit_factor:.2f} < 1")
    # Check robustness
    fails = sum(1 for r in robustness if r.stability == "FAIL")
    warns = sum(1 for r in robustness if r.stability == "WARNING")
    if fails > 0:
        fail_reasons.append(f"{fails} robustness FAIL")
    if warns > 2:
        warn_reasons.append(f"{warns} robustness WARNING")

    if fail_reasons:
        status = "FAIL"
        summary = "Insufficient evidence — " + "; ".join(fail_reasons)
    elif warn_reasons:
        status = "WARNING"
        summary = "Marginal — " + "; ".join(warn_reasons)
    else:
        status = "PASS"
        summary = "Evidence supports edge — trade count, expectancy, and robustness PASS"

    return ValidationResult(
        status=status,
        evidence=evidence,
        summary=summary,
        details={"fails": fail_reasons, "warns": warn_reasons},
    )
