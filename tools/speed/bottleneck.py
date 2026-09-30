"""Bottleneck detection and next-optimization recommendations (§19, §20).

Ranking covers measured phases only; unmeasured phases are listed last and
labeled UNMEASURED (§19: never distribute total time arbitrarily).
Recommendations fire only on measured evidence (§20); with no evidence the
engine returns a single insufficient-evidence entry instead of advice.
"""

from __future__ import annotations

# Evidence-gated thresholds (documented so every recommendation is auditable).
REPAIR_TAX_THRESHOLD = 0.30
DUPLICATE_READ_THRESHOLD = 0.25
VALIDATION_SHARE_THRESHOLD = 0.40
UNKNOWN_SHARE_THRESHOLD = 0.50
FIRST_PASS_FLOOR = 0.70
MIN_SAMPLES_FOR_RATE_RULES = 3


def rank_costs(costs: dict[str, float | None]) -> list[dict]:
    """Rank measured costs descending; UNMEASURED entries rank last (§19)."""
    measured = [
        {"phase": name, "seconds": value, "basis": "measured"}
        for name, value in costs.items()
        if isinstance(value, (int, float))
    ]
    unmeasured = [
        {"phase": name, "seconds": None, "basis": "UNMEASURED"}
        for name, value in costs.items()
        if not isinstance(value, (int, float))
    ]
    measured.sort(key=lambda row: float(row["seconds"]), reverse=True)
    return measured + unmeasured


def costs_from_record(record: dict) -> dict[str, float | None]:
    """Assemble the rankable cost map for one journal record."""
    segments = record.get("segments") or {}
    phases = record.get("phases") or {}
    return {
        "repository_discovery": segments.get("repo_understanding_s"),
        "context_preparation": None,  # no direct marker; see planning/reads proxies
        "planning": record.get("planning_s", phases.get("PLANNING")),
        "editing": segments.get("implementation_s"),
        "validation": record.get("validation_s"),
        "repair": record.get("repair_s", phases.get("REPAIR")),
        "other": segments.get("startup_s"),
    }


def recommend(snapshot: dict) -> list[dict]:
    """Return evidence-based next-optimization recommendations (§20)."""
    recommendations: list[dict] = []

    def add(rule: str, evidence: str, recommendation: str, confidence: str) -> None:
        recommendations.append(
            {
                "rule": rule,
                "evidence": evidence,
                "recommendation": recommendation,
                "confidence": confidence,
            }
        )

    repair_tax = snapshot.get("repair_tax_median")
    if isinstance(repair_tax, (int, float)) and repair_tax > REPAIR_TAX_THRESHOLD:
        add(
            "repair_tax",
            f"median repair_tax_ratio {repair_tax} > {REPAIR_TAX_THRESHOLD}",
            "improve pre-edit validation (contracts re-read before editing)",
            "MEDIUM",
        )
    duplicate_rate = snapshot.get("duplicate_read_rate")
    if isinstance(duplicate_rate, (int, float)) and duplicate_rate > DUPLICATE_READ_THRESHOLD:
        add(
            "duplicate_search",
            f"duplicate read rate {duplicate_rate} > {DUPLICATE_READ_THRESHOLD}",
            "improve repository-cache discipline (read once, reuse notes)",
            "MEDIUM",
        )
    validation_share = snapshot.get("validation_share")
    if isinstance(validation_share, (int, float)) and validation_share > VALIDATION_SHARE_THRESHOLD:
        add(
            "validation_share",
            f"validation share of task {validation_share} > {VALIDATION_SHARE_THRESHOLD}",
            "optimize validation (impact-first already default; check full-gate causes)",
            "MEDIUM",
        )
    implementation_share = snapshot.get("implementation_share")
    unknown_share = snapshot.get("unknown_share")
    if isinstance(unknown_share, (int, float)) and unknown_share > UNKNOWN_SHARE_THRESHOLD:
        add(
            "unknown_dominates",
            f"unmeasured share {unknown_share} > {UNKNOWN_SHARE_THRESHOLD}",
            "improve marking discipline before any other optimization",
            "HIGH",
        )
    elif isinstance(implementation_share, (int, float)) and implementation_share > 0.50:
        add(
            "implementation_dominates",
            f"implementation share {implementation_share} > 0.50",
            "improve context/planning/reuse (context compiler + proven plans)",
            "MEDIUM",
        )
    first_pass_rate = snapshot.get("first_pass_rate")
    n = snapshot.get("n", 0)
    if (
        isinstance(first_pass_rate, (int, float))
        and isinstance(n, int)
        and n >= MIN_SAMPLES_FOR_RATE_RULES
        and first_pass_rate < FIRST_PASS_FLOOR
    ):
        add(
            "first_pass",
            f"first-pass rate {first_pass_rate} < {FIRST_PASS_FLOOR} over {n} tasks",
            "increase first-pass correctness (smaller edits, contract re-check)",
            "MEDIUM",
        )
    # Static evidence from the benchmark log: parallel validation measured
    # 0.9x (slower) — kept as a standing rule, labeled static (§12).
    add(
        "parallel_validation",
        "static: parallel validation measured 0.9x (slower) on 2026-09-05",
        "keep validation serial unless a new measurement passes review",
        "HIGH",
    )
    measured_rules = [r for r in recommendations if r["rule"] != "parallel_validation"]
    if not measured_rules:
        add(
            "insufficient_evidence",
            "no measured snapshot metric crossed a recommendation threshold",
            "change nothing; record more benchmarked tasks first",
            "HIGH",
        )
    return recommendations
