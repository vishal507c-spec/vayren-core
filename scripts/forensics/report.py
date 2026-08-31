"""Forensics report rendering: task report + multi-task aggregate.

The default report follows the spec: header totals, phase table (evidence
only, in spec order), TOTAL row, partition check, and TOP TIME CONSUMERS.
`--full` appends the detailed audit sections (per-file evidence, task-level
metrics, chronological trace, accuracy legend).
"""

from __future__ import annotations

from analysis import (
    TaskMetrics,
    aggregate,
    analyze_task,
    format_clock,
    format_duration,
    phase_table,
)
from store import ROOT, TASKS_DIR, load_events
from watcher import parse_iso

PHASE_LABELS = {
    "REPOSITORY_SEARCH": "Repository Search",
    "FILE_DISCOVERY": "Repository Search",
    "CONTEXT_READING": "Context Reading",
    "ARCHITECTURE_ANALYSIS": "Architecture Analysis",
    "PLANNING": "Planning",
    "CODE_GENERATION": "Code Generation",
    "COMMAND_EXECUTION": "Command Execution",
    "BUILD_STARTUP": "Command Execution",
    "TESTING": "Testing",
    "DEBUGGING": "Debugging",
    "REWORK": "Rework",
    "VALIDATION": "Validation",
    "AI_TOOL_WAIT": "Tool/AI Waiting",
    "HUMAN_IDLE": "Human Idle",
    "UNKNOWN": "Unknown",
}

PHASE_ORDER = [
    "Repository Search",
    "Context Reading",
    "Architecture Analysis",
    "Planning",
    "Code Generation",
    "Command Execution",
    "Testing",
    "Debugging",
    "Rework",
    "Validation",
    "Tool/AI Waiting",
    "Human Idle",
    "Unknown",
]


def _percent(ms: int, total_ms: int) -> float:
    return 100.0 * ms / total_ms if total_ms else 0.0


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + ".."


def _phase_rows(metrics: TaskMetrics) -> list[tuple[str, int, float]]:
    """(label, ms, pct) per phase, spec order, evidence only; UNKNOWN always."""
    total_ms = metrics.elapsed_ms
    by_label: dict[str, int] = {}
    for phase, ms, _ in phase_table(metrics):
        label = PHASE_LABELS.get(phase, phase)
        by_label[label] = by_label.get(label, 0) + ms
    by_label["Unknown"] = metrics.unknown_ms
    rows = [
        (label, by_label[label], _percent(by_label[label], total_ms))
        for label in PHASE_ORDER
        if label in by_label and by_label[label] > 0
    ]
    if not rows:
        rows.append(("Unknown", metrics.unknown_ms, _percent(metrics.unknown_ms, total_ms)))
    return rows


def render_task_report(metrics: TaskMetrics) -> str:
    lines: list[str] = []
    total_ms = metrics.elapsed_ms
    unknown_ms = metrics.unknown_ms
    measured_ms = total_ms - unknown_ms
    lines.append("CODING TIME FORENSICS")
    lines.append("=====================")
    lines.append("")
    lines.append(f"Task: {metrics.name or metrics.task_id}")
    lines.append("")
    lines.append(f"{'Total elapsed:':<21}{format_duration(total_ms)}")
    lines.append(f"{'Measured:':<21}{format_duration(measured_ms)}")
    lines.append(f"{'Unknown:':<21}{format_duration(unknown_ms)}")
    lines.append("")
    lines.append(f"{'Phase':<30}{'Time':<12}%")
    lines.append("-" * 48)
    rows = _phase_rows(metrics)
    for label, ms, pct in rows:
        lines.append(f"{label:<30}{format_duration(ms):>7}     {pct:.1f}%")
    lines.append("-" * 48)
    lines.append(f"{'TOTAL':<30}{format_duration(total_ms):>7}     100.0%")
    lines.append("")
    lines.append("PARTITION CHECK:")
    lines.append("Measured + Unknown = Total")
    lines.append(f"diff {total_ms - measured_ms - unknown_ms}ms")
    lines.append("")
    ranked = sorted(rows, key=lambda row: row[1], reverse=True)[:3]
    lines.append("TOP TIME CONSUMERS")
    for rank, (label, ms, _) in enumerate(ranked, 1):
        lines.append(f"{rank}. {label:<16} - {format_duration(ms)}")
    return "\n".join(lines)


def render_task_report_details(metrics: TaskMetrics, idle_threshold_ms: int) -> str:
    """The detailed audit sections, appended with --full."""
    del idle_threshold_ms
    lines: list[str] = []
    total_ms = metrics.elapsed_ms

    read_rows = sorted(
        (
            (
                evidence.path,
                evidence.reads,
                evidence.first_read or metrics.start,
                evidence.last_read or metrics.start,
            )
            for evidence in metrics.files.values()
            if evidence.reads
        ),
        key=lambda row: row[1],
        reverse=True,
    )
    if read_rows:
        lines.append("CONTEXT READING (per file - access counts, measured)")
        lines.append("-" * 45)
        for path, count, first, last in read_rows:
            lines.append(
                f"{_truncate(path, 38):<40}{count:>3}x  {format_clock(first)}"
                f" -> {format_clock(last)}"
            )
        lines.append("")

    search_rows = sorted(
        (
            (
                evidence.path,
                evidence.searches,
                evidence.first_search or metrics.start,
                evidence.last_search or metrics.start,
            )
            for evidence in metrics.files.values()
            if evidence.searches
        ),
        key=lambda row: row[1],
        reverse=True,
    )
    lines.append("REPOSITORY SEARCH (per file - access counts, measured)")
    lines.append("-" * 45)
    if search_rows:
        for path, count, first, last in search_rows:
            lines.append(
                f"{_truncate(path, 38):<40}{count:>3}x  {format_clock(first)}"
                f" -> {format_clock(last)}"
            )
    else:
        lines.append("no per-file search marks recorded (mark --action search --file ...)")
    lines.append("")

    repeated = [
        evidence for evidence in metrics.files.values() if evidence.reads + evidence.searches > 1
    ]
    if repeated:
        lines.append("REPEATED CONTEXT ACCESS (>= 2 accesses)")
        lines.append("-" * 45)
        for evidence in sorted(repeated, key=lambda ev: ev.reads + ev.searches, reverse=True):
            lines.append(
                f"{_truncate(evidence.path, 38):<40}reads {evidence.reads}"
                f"  searches {evidence.searches}"
            )
        lines.append("")

    if metrics.rework_cycles:
        lines.append(f"REWORK CYCLES (auto-detected, INFERRED): {len(metrics.rework_cycles)}")
        lines.append("-" * 45)
        for cycle in metrics.rework_cycles:
            lines.append(
                f"{_truncate(cycle.file, 38):<40}{format_clock(cycle.first_edit)}"
                f" -> {format_clock(cycle.last_edit)}  ({cycle.trigger})"
            )
        lines.append("")

    modified = [
        evidence for evidence in metrics.files.values() if evidence.edits or evidence.lines_changed
    ]
    if modified:
        lines.append("FILE-LEVEL EVIDENCE (edits from watcher, lines from git numstat)")
        lines.append("-" * 60)
        lines.append(f"{'File':<38}{'ext':<6}{'edits':>6}{'+':>6}{'-':>6}{'delta':>8}")
        for evidence in sorted(
            modified, key=lambda ev: ev.last_edit or ev.first_edit or metrics.start
        ):
            first = format_clock(evidence.first_edit) if evidence.first_edit else "-"
            last = format_clock(evidence.last_edit) if evidence.last_edit else "-"
            lines.append(
                f"{_truncate(evidence.path, 38):<38}{evidence.extension:<6}"
                f"{evidence.edits:>6}{evidence.lines_added:>6}{evidence.lines_removed:>6}"
                f"{evidence.lines_changed:>8}  {first} -> {last}"
            )
        lines.append("")

    passes = sum(1 for run in metrics.runs if run.get("status") == 0)
    failures = sum(1 for run in metrics.runs if run.get("status") not in (0, None))
    lines.append("TASK-LEVEL METRICS")
    lines.append("-" * 45)
    lines.append(f"TOTAL_ELAPSED_TIME       {format_duration(total_ms)}")
    lines.append(f"ACTIVE_ENGINEERING_TIME  {format_duration(metrics.active_ms)}")
    lines.append(f"AI_WAIT_TIME             {format_duration(metrics.phase_ms('AI_TOOL_WAIT'))}")
    lines.append(f"TEST_TIME                {format_duration(metrics.phase_ms('TESTING'))}")
    lines.append(f"DEBUG_TIME               {format_duration(metrics.phase_ms('DEBUGGING'))}")
    lines.append(f"REWORK_TIME              {format_duration(metrics.phase_ms('REWORK'))}")
    lines.append(f"CONTEXT_TIME             {format_duration(metrics.phase_ms('CONTEXT_READING'))}")
    lines.append(f"CODING_TIME              {format_duration(metrics.phase_ms('CODE_GENERATION'))}")
    lines.append(f"FILES_READ               {sum(1 for ev in metrics.files.values() if ev.reads)}")
    lines.append(f"FILES_MODIFIED           {sum(1 for ev in metrics.files.values() if ev.edits)}")
    lines.append(f"LINES_ADDED              {sum(ev.lines_added for ev in metrics.files.values())}")
    lines.append(
        f"LINES_REMOVED            {sum(ev.lines_removed for ev in metrics.files.values())}"
    )
    lines.append(f"TESTS_RUN (wrapped)      {len(metrics.runs)}  (pass {passes} / fail {failures})")
    lines.append(f"TEST_FAILURES           {failures}")
    lines.append(f"TEST_PASSES             {passes}")
    lines.append(f"NUMBER_OF_REWORK_CYCLES {len(metrics.rework_cycles)}")
    lines.append(f"NUMBER_OF_AI_ITERATIONS {metrics.ai_wait_marks}")
    lines.append("")

    lines.append("CHRONOLOGICAL TRACE")
    lines.append("-" * 45)
    for event in load_events(metrics.task_id):
        phase = event.get("phase") or "-"
        action = event.get("action") or "-"
        file = event.get("file") or ""
        command = event.get("command") or ""
        duration = event.get("duration_ms")
        detail = file or command
        suffix = f"  [{duration}ms]" if duration else ""
        lines.append(
            f"{format_clock(parse_iso(event['ts']))}  {phase:<22}{action:<12} "
            f"{_truncate(detail or '-', 38)}{suffix}"
        )
    lines.append("")

    measured = sum(1 for interval in metrics.intervals if interval.accuracy == "MEASURED")
    inferred = sum(1 for interval in metrics.intervals if interval.accuracy == "INFERRED")
    lines.append("ACCURACY LEGEND")
    lines.append("-" * 45)
    lines.append(f"intervals MEASURED  {measured}")
    lines.append(f"intervals INFERRED  {inferred}")
    lines.append("MEASURED: wall-clock or mtime timestamps")
    lines.append("INFERRED: heuristic classification (e.g. UNKNOWN gaps)")
    lines.append("UNKNOWN:  no timestamp evidence available")
    return "\n".join(lines)


def render_aggregate_report() -> str:
    tasks = _task_ids()
    all_metrics = [analyze_task(task_id) for task_id in tasks]
    summary = aggregate(all_metrics)
    lines: list[str] = []
    lines.append("CODING TIME FORENSICS — AGGREGATE")
    lines.append("=================================")
    if not summary:
        lines.append("no completed tasks recorded")
        return "\n".join(lines)
    lines.append(f"TASK COUNT               {summary['task_count']}")
    lines.append(f"TOTAL ENGINEERING TIME   {format_duration(summary['total_ms'])}")
    lines.append(f"AVERAGE TASK TIME        {format_duration(summary['average_ms'])}")
    lines.append(f"MEDIAN TASK TIME         {format_duration(summary['median_ms'])}")
    lines.append("")
    lines.append(f"{'Phase':<24}{'Avg %':>10}")
    lines.append("-" * 34)
    for phase, pct in sorted(
        summary["average_phase_pct"].items(), key=lambda item: item[1], reverse=True
    ):
        lines.append(f"{phase:<24}{pct:>9.1f}%")
    lines.append("")
    lines.append("TOP 10 BOTTLENECKS (by total minutes across tasks)")
    lines.append("-" * 45)
    for rank, (phase, ms) in enumerate(summary["bottlenecks"][:10], 1):
        lines.append(f"{rank:>2}. {phase}  ({format_duration(ms)})")
    return "\n".join(lines)


def _task_ids() -> list[str]:
    tasks_root = ROOT / TASKS_DIR
    if not tasks_root.exists():
        return []
    return sorted(
        entry.name
        for entry in tasks_root.iterdir()
        if entry.is_dir() and (entry / "events.jsonl").exists()
    )
