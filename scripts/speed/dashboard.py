"""Speed dashboard rendering (Phase 18, §22).

Pure rendering over loaded journal records: BEFORE → CURRENT → DELTA pairs
for equivalent tasks (shared fingerprint), separate speed metrics, ranked
bottlenecks, evidence-gated recommendations. Anything without a comparable
measurement renders as NOT MEASURED.
"""

from __future__ import annotations

import statistics

from bottleneck import costs_from_record, rank_costs, recommend
from edits import first_pass
from journal import aggregate, find_pairs


def cell(value: object, suffix: str = "") -> str:
    """Render one value; None becomes NOT MEASURED (never blank, never 0)."""
    if value is None:
        return "NOT MEASURED"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def _floats(values: list[object]) -> list[float]:
    """Keep measured numbers only; unmeasured entries are dropped."""
    return [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]


def snapshot(journal: list[dict]) -> dict:
    """Aggregate snapshot feeding the recommendation engine."""
    agg = aggregate(journal)
    totals = _floats([r.get("total_s") for r in journal])
    validations = _floats([r.get("validation_s") for r in journal])
    impls = _floats([(r.get("segments") or {}).get("implementation_s") for r in journal])
    result = dict(agg)
    if totals and validations:
        result["validation_share"] = round(
            statistics.median(validations) / statistics.median(totals), 3
        )
    if totals and impls:
        result["implementation_share"] = round(
            statistics.median(impls) / statistics.median(totals), 3
        )
    unknowns = _floats([(r.get("phases") or {}).get("UNKNOWN") for r in journal])
    if totals and unknowns and statistics.median(totals) > 0:
        result["unknown_share"] = round(statistics.median(unknowns) / statistics.median(totals), 3)
    duplicates = _floats([(r.get("context") or {}).get("duplicate_read_rate") for r in journal])
    if duplicates:
        result["duplicate_read_rate"] = round(statistics.median(duplicates), 3)
    return result


def validation_speedup(journal: list[dict], before_task: str, current_task: str) -> float | None:
    """Validation-time ratio for a pair; None when either side is unclocked."""
    before = next((r for r in journal if r.get("task") == before_task), None)
    current = next((r for r in journal if r.get("task") == current_task), None)
    if before is None or current is None:
        return None
    before_v = before.get("validation_s")
    current_v = current.get("validation_s")
    if not isinstance(before_v, (int, float)) or not isinstance(current_v, (int, float)):
        return None
    if isinstance(before_v, bool) or isinstance(current_v, bool) or current_v <= 0:
        return None
    return round(float(before_v) / float(current_v), 3)


def confidence(pair: dict, validation_ratio: float | None) -> str:
    """Pair confidence, downgraded on window-hygiene divergence.

    When the validation-time ratio and the total-time speedup point in
    opposite directions (beyond a ±5% noise band), the wall-clock window of
    at least one side contains non-workload time: the pair is flagged
    DIVERGENT instead of trusted.
    """
    speedup = pair.get("speedup")
    if (
        validation_ratio is not None
        and isinstance(speedup, (int, float))
        and (
            (validation_ratio > 1.05 and speedup < 0.95)
            or (validation_ratio < 0.95 and speedup > 1.05)
        )
    ):
        return (
            f"LOW (same fingerprint BUT DIVERGENT: validation {validation_ratio:.2f}x "
            f"vs total {speedup:.2f}x — window hygiene suspect, do not claim)"
        )
    return "HIGH (same fingerprint)"


def median_costs(journal: list[dict]) -> dict[str, float | None]:
    """Median per cost name across records; None when never measured."""
    names: set[str] = set()
    for record in journal:
        names.update(costs_from_record(record))
    medians: dict[str, float | None] = {}
    for name in sorted(names):
        values = [
            costs_from_record(record).get(name)
            for record in journal
            if isinstance(costs_from_record(record).get(name), (int, float))
        ]
        typed = [float(v) for v in values if isinstance(v, (int, float))]
        medians[name] = round(statistics.median(typed), 3) if typed else None
    return medians


def dashboard_lines(journal: list[dict]) -> list[str]:
    """Markdown lines for the speed dashboard (§22)."""
    out = [
        "## Speed dashboard (BEFORE -> CURRENT -> DELTA, equivalent tasks only)",
        "",
        "Pairs share a fingerprint (same class + request + files). Unpaired",
        "tasks are listed without speedup: SPEEDUP = NOT MEASURED.",
        "",
        "| task | class | baseline_s | current_s | speedup | validation_speedup | "
        "first_pass | repairs | validation_s | total_s | confidence |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    pairs = find_pairs(journal)
    paired_tasks = {str(p["before_task"]) for p in pairs} | {str(p["current_task"]) for p in pairs}
    for pair in pairs:
        current = next(r for r in journal if r.get("task") == pair["current_task"])
        validation_ratio = validation_speedup(
            journal, str(pair["before_task"]), str(pair["current_task"])
        )
        ratio_cell = f"{validation_ratio:.2f}x" if validation_ratio is not None else "NOT MEASURED"
        out.append(
            f"| {pair['current_task']} | {pair['task_class']} | {pair['before_s']:.1f} | "
            f"{pair['current_s']:.1f} | {pair['speedup']:.2f}x | {ratio_cell} | "
            f"{cell(first_pass(current))} | "
            f"{cell(current.get('repairs'))} | {cell(current.get('validation_s'), 's')} | "
            f"{cell(current.get('total_s'), 's')} | {confidence(pair, validation_ratio)} |"
        )
    for record in journal:
        if str(record.get("task")) in paired_tasks:
            continue
        out.append(
            f"| {record.get('task')} | {record.get('task_class')} | NOT MEASURED | "
            f"{cell(record.get('total_s'), 's')} | NOT MEASURED | NOT MEASURED | "
            f"{cell(first_pass(record))} | "
            f"{cell(record.get('repairs'))} | {cell(record.get('validation_s'), 's')} | "
            f"{cell(record.get('total_s'), 's')} | — (no comparable pair) |"
        )
    if not journal:
        out.append(
            "| (none) | — | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |"
            " — | — | — | — | — |"
        )
    agg = aggregate(journal)
    out += [
        "",
        "## Speed metrics (kept separate from AI utilization, never mixed)",
        "",
        "- AI_UTILIZATION_SCORE: NOT MEASURED here (see development log; "
        "utilization is not a speed multiplier).",
        f"- TASK_WALL_TIME median: {cell(agg['task_wall_s_median'], 's')} "
        f"over {agg['n']} speed-task(s).",
        f"- VALIDATION_TIME median: {cell(agg['validation_s_median'], 's')}.",
        f"- FIRST_PASS_RATE: {agg['first_pass_rate']} ({agg['first_pass_known']} known).",
        f"- REPAIR_TAX median: {agg['repair_tax_median']}.",
        f"- REUSE_SPEEDUP: {agg['reuse_speedup']} ({agg['reuse_pairs']} cold/warm pair(s)).",
        f"- HUMAN_INTERVENTIONS: {agg['human_interventions']}.",
        "",
        "## Bottlenecks (measured phases ranked, rest UNMEASURED)",
        "",
    ]
    medians = median_costs(journal)
    if medians:
        for row in rank_costs(medians):
            out.append(f"- {row['phase']}: {cell(row['seconds'], 's')} [{row['basis']}]")
    else:
        out.append("- (no measured costs yet)")
    out += ["", "## Next-optimization recommendations (evidence-gated)", ""]
    for item in recommend(snapshot(journal)):
        out.append(f"- [{item['confidence']}] {item['rule']}: {item['recommendation']}")
        out.append(f"  evidence: {item['evidence']}")
    return out
