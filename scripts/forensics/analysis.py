"""Forensics analysis: task metrics, rework cycles, repeated access, aggregate."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from intervals import Interval, build_intervals, task_window
from store import load_events, load_json
from watcher import parse_iso

MISSING = "UNKNOWN"

ACTION_READ = {"read_file", "read_dir", "read_doc", "inspect_file", "inspect_dir"}
ACTION_SEARCH = {"search", "search_files", "search_symbol", "search_refs", "grep"}
ACTION_WRITE = {"edit_file", "write_file", "create_file", "delete_file"}


@dataclass
class FileEvidence:
    path: str
    edits: int = 0
    first_edit: datetime | None = None
    last_edit: datetime | None = None
    reads: int = 0
    searches: int = 0
    first_read: datetime | None = None
    last_read: datetime | None = None
    first_search: datetime | None = None
    last_search: datetime | None = None
    lines_added: int = 0
    lines_removed: int = 0

    @property
    def extension(self) -> str:
        return self.path.rsplit(".", 1)[-1] if "." in self.path else MISSING

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_removed


@dataclass
class ReworkCycle:
    file: str
    first_edit: datetime
    last_edit: datetime
    trigger: str
    accuracy: str = "INFERRED"


@dataclass
class TaskMetrics:
    task_id: str
    name: str
    start: datetime
    end: datetime | None
    intervals: list[Interval] = field(default_factory=list)
    files: dict[str, FileEvidence] = field(default_factory=dict)
    rework_cycles: list[ReworkCycle] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    ai_wait_marks: int = 0

    @property
    def elapsed_ms(self) -> int:
        end = self.end or datetime.now().astimezone()
        return max(0, int((end - self.start).total_seconds() * 1000))

    def phase_ms(self, phase: str) -> int:
        return sum(interval.duration_ms for interval in self.intervals if interval.phase == phase)

    @property
    def unknown_ms(self) -> int:
        return self.phase_ms("UNKNOWN")

    @property
    def active_ms(self) -> int:
        excluded = {"UNKNOWN", "AI_TOOL_WAIT", "HUMAN_IDLE"}
        return sum(
            interval.duration_ms for interval in self.intervals if interval.phase not in excluded
        )


def analyze_task(task_id: str, idle_threshold_ms: int = 120_000) -> TaskMetrics:
    events = load_events(task_id)
    start, task_end = task_window(events)
    if task_end is not None:
        window_end = task_end
    elif events:
        window_end = datetime.now().astimezone()
    else:
        window_end = start
    name = ""
    for event in events:
        if event.get("action") == "task_start" and event.get("note"):
            name = event["note"]
            break
    metrics = TaskMetrics(task_id=task_id, name=name, start=start, end=window_end)
    metrics.intervals, _ = build_intervals(events, idle_threshold_ms, window_end)
    covered_ms = sum(interval.duration_ms for interval in metrics.intervals)
    if covered_ms != metrics.elapsed_ms:
        raise ValueError(
            "partition mismatch: "
            f"covered {covered_ms}ms != elapsed {metrics.elapsed_ms}ms "
            f"for task {task_id}"
        )

    for event in events:
        action = event.get("action")
        file = event.get("file")
        ts = parse_iso(event["ts"])
        if action == "watch_mod" and file:
            evidence = metrics.files.setdefault(file, FileEvidence(path=file))
            evidence.edits += 1
            if evidence.first_edit is None or ts < evidence.first_edit:
                evidence.first_edit = ts
            if evidence.last_edit is None or ts > evidence.last_edit:
                evidence.last_edit = ts
        elif action == "watch_del" and file:
            metrics.files.setdefault(file, FileEvidence(path=file))
        elif action in ACTION_READ and file:
            evidence = metrics.files.setdefault(file, FileEvidence(path=file))
            evidence.reads += 1
            if evidence.first_read is None or ts < evidence.first_read:
                evidence.first_read = ts
            if evidence.last_read is None or ts > evidence.last_read:
                evidence.last_read = ts
        elif action in ACTION_SEARCH and file:
            evidence = metrics.files.setdefault(file, FileEvidence(path=file))
            evidence.searches += 1
            if evidence.first_search is None or ts < evidence.first_search:
                evidence.first_search = ts
            if evidence.last_search is None or ts > evidence.last_search:
                evidence.last_search = ts
        elif action == "run_end":
            metrics.runs.append(event)
        elif action in ("mark", "run_start") and event.get("phase") == "AI_TOOL_WAIT":
            metrics.ai_wait_marks += 1

    _apply_numstat_delta(metrics, task_id)
    metrics.rework_cycles = _detect_rework(metrics, events)
    return metrics


def _apply_numstat_delta(metrics: TaskMetrics, task_id: str) -> None:
    baseline = load_json(task_id, "numstat_baseline.json", {})
    end_state = load_json(task_id, "numstat_end.json", {})
    all_paths = set(baseline) | set(end_state)
    for path in all_paths:
        base = baseline.get(path, (0, 0))
        final = end_state.get(path, (0, 0))
        added = max(0, final[0] - base[0])
        removed = max(0, final[1] - base[1])
        if added or removed:
            evidence = metrics.files.setdefault(path, FileEvidence(path=path))
            evidence.lines_added = added
            evidence.lines_removed = removed


def _detect_rework(metrics: TaskMetrics, events: list[dict]) -> list[ReworkCycle]:
    """A rework cycle = file edited, then a failed test/debug event, then edited again."""
    cycles: list[ReworkCycle] = []
    runs_by_index = {
        str(event["ts"]): event
        for event in events
        if event.get("action") == "run_end" and event.get("ts")
    }
    for evidence in metrics.files.values():
        first_edit = evidence.first_edit
        last_edit = evidence.last_edit
        if evidence.edits < 2 or first_edit is None or last_edit is None:
            continue
        failed_after_first = [
            run
            for ts, run in runs_by_index.items()
            if run.get("status") not in (0, None)
            and parse_iso(ts) > first_edit
            and parse_iso(ts) < last_edit
        ]
        trigger = (
            "failed test/command between edits"
            if failed_after_first
            else "two edit bursts without failing command"
        )
        cycles.append(
            ReworkCycle(
                file=evidence.path,
                first_edit=first_edit,
                last_edit=last_edit,
                trigger=trigger,
            )
        )
    return cycles


def format_duration(ms: int) -> str:
    """Wall-clock style: `Xm Ys` (and `Xh Ym Zs` for long tasks)."""
    total = max(0, ms)
    seconds = total // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes}m {seconds:02d}s"


def format_clock(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def phase_table(metrics: TaskMetrics) -> list[tuple[str, int, float]]:
    total = metrics.elapsed_ms
    rows = [
        (phase, metrics.phase_ms(phase), 100.0 * metrics.phase_ms(phase) / total)
        for phase in sorted(
            {interval.phase for interval in metrics.intervals if interval.phase not in ("UNKNOWN",)}
        )
    ]
    return sorted(rows, key=lambda row: row[1], reverse=True)


def aggregate(all_metrics: list[TaskMetrics]) -> dict:
    if not all_metrics:
        return {}
    durations = [metrics.elapsed_ms for metrics in all_metrics]
    phase_names = sorted(
        {interval.phase for metrics in all_metrics for interval in metrics.intervals}
    )
    phase_totals: dict[str, int] = {}
    for phase in phase_names:
        phase_totals[phase] = sum(metrics.phase_ms(phase) for metrics in all_metrics)
    total_ms = sum(durations)
    total_known = sum(ms for phase, ms in phase_totals.items() if phase != "UNKNOWN")
    average_phase: dict[str, float] = {}
    for phase, ms in phase_totals.items():
        if phase == "UNKNOWN":
            continue
        denominator = total_known or 1
        average_phase[phase] = 100.0 * ms / denominator
    bottlenecks = sorted((phase, ms) for phase, ms in phase_totals.items() if phase != "UNKNOWN")
    return {
        "task_count": len(all_metrics),
        "total_ms": total_ms,
        "average_ms": int(statistics.mean(durations)),
        "median_ms": int(statistics.median(durations)),
        "average_phase_pct": average_phase,
        "bottlenecks": [phase for phase, _ in bottlenecks[:10]],
    }
