"""Machine-readable task journal (Phase 18, §4).

Two append-only logs live next to the benchmark runs log; history is never
rewritten (§6 baseline preservation):

- ``scripts/speed_marks.jsonl`` — marker events (see markers.py).
- ``scripts/speed_journal.jsonl`` — one record per benchmarked task with
  derived loop segments, phase totals, counts and complexity signals.

Unmeasurable fields are stored as null and rendered as UNMEASURED — never
invented (§23).
"""

from __future__ import annotations

import json
import statistics
import subprocess
from pathlib import Path

from edits import repair_tax
from markers import MILESTONES, first_milestones, loop_segments, phase_totals

TASK_CLASSES: tuple[str, ...] = (
    "MICRO",
    "SMALL",
    "UI",
    "BUGFIX",
    "STRATEGY",
    "BACKTEST",
    "REFACTOR",
    "ARCHITECTURE",
    "MIGRATION",
)

REUSE_MODES: tuple[str, ...] = ("cold", "warm")


def repo_root() -> Path:
    """Repository root: nearest ancestor directory containing .git."""
    path = Path.cwd().resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return path


def marks_path(root: Path | None = None) -> Path:
    """Path of the marker stream (append-only)."""
    return (root or repo_root()) / "scripts" / "speed_marks.jsonl"


def journal_path(root: Path | None = None) -> Path:
    """Path of the task journal (append-only)."""
    return (root or repo_root()) / "scripts" / "speed_journal.jsonl"


def git_revision(root: Path | None = None) -> str | None:
    """Short HEAD revision, or None when git is unavailable."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root or repo_root(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.stdout.strip() or None
    except Exception:
        return None


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file; missing file means no records yet (not an error)."""
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_jsonl(path: Path, record: dict) -> None:
    """Append one record; the log is never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def append_mark(
    task: str,
    kind: str,
    name: str,
    *,
    file: str | None = None,
    note: str | None = None,
    duration_ms: int | None = None,
    ts: str | None = None,
    path: Path | None = None,
) -> dict:
    """Append one marker event (negligible overhead: a single JSONL line)."""
    from datetime import datetime

    if kind not in ("milestone", "phase"):
        raise ValueError(f"kind must be milestone|phase, got {kind!r}")
    if kind == "milestone" and name not in MILESTONES:
        raise ValueError(f"unknown milestone {name!r}")
    record = {
        "task": task,
        "ts": ts or datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "kind": kind,
        "name": name,
        "file": file,
        "note": note,
        "duration_ms": duration_ms,
    }
    append_jsonl(path or marks_path(), record)
    return record


def build_record(
    task: str,
    task_class: str,
    marks: list[dict],
    *,
    reuse: str | None = None,
    fingerprint: str | None = None,
    validation_s: float | None = None,
    tests: int | None = None,
    failures: int | None = None,
    repairs: int | None = None,
    files_changed: int | None = None,
    lines_added: int | None = None,
    lines_removed: int | None = None,
    human_interventions: int = 0,
    validation_ref: str | None = None,
    revision: str | None = None,
    notes: str | None = None,
) -> dict:
    """Build a journal record, deriving segments/phases from marker evidence.

    ``validation_s`` is clocked validation time only (wrapped runs); when it
    was not clocked it stays None even if ``validation_window_s`` exists —
    the window includes repair loops and must not pose as validation cost.
    """
    if task_class not in TASK_CLASSES:
        raise ValueError(f"unknown task_class {task_class!r}")
    if reuse is not None and reuse not in REUSE_MODES:
        raise ValueError(f"reuse must be cold|warm, got {reuse!r}")
    segments = loop_segments(marks, task)
    phases = phase_totals(marks, task)
    milestones = first_milestones(marks, task)
    return {
        "kind": "speed-task",
        "task": task,
        "task_class": task_class,
        "fingerprint": fingerprint,
        "reuse": reuse,
        "revision": revision,
        "start": milestones.get("TASK_START"),
        "end": milestones.get("TASK_END"),
        "first_edit": milestones.get("FIRST_EDIT"),
        "implementation_complete": milestones.get("IMPLEMENTATION_COMPLETE"),
        "first_validation": milestones.get("FIRST_VALIDATION"),
        "final_green": milestones.get("FINAL_GREEN"),
        "segments": segments,
        "total_s": segments.get("total_s"),
        "validation_s": validation_s,
        "phases": phases,
        "planning_s": phases.get("PLANNING"),
        "repair_s": phases.get("REPAIR"),
        "tests": tests,
        "failures": failures,
        "repairs": repairs,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "human_interventions": human_interventions,
        "validation_ref": validation_ref,
        "notes": notes,
    }


def speed_tasks(journal: list[dict]) -> list[dict]:
    """Filter a loaded journal to task records."""
    return [r for r in journal if r.get("kind") == "speed-task"]


def _measured(values: list[float | None]) -> list[float]:
    return [v for v in values if isinstance(v, (int, float))]


def find_pairs(records: list[dict]) -> list[dict]:
    """BEFORE → CURRENT → DELTA pairs for equivalent tasks (§6).

    Only records sharing a fingerprint are comparable (§5: never compare
    unrelated tasks and call it speedup). BEFORE is the earliest measured
    total, CURRENT the latest; the log itself is never modified.
    """
    by_fingerprint: dict[str, list[dict]] = {}
    for record in records:
        fingerprint = record.get("fingerprint")
        if not fingerprint:
            continue
        by_fingerprint.setdefault(str(fingerprint), []).append(record)
    pairs: list[dict] = []
    for fingerprint, group in sorted(by_fingerprint.items()):
        measured = [r for r in group if isinstance(r.get("total_s"), (int, float))]
        if len(measured) < 2:
            continue
        before = min(measured, key=lambda r: str(r.get("start") or r.get("task")))
        current = max(measured, key=lambda r: str(r.get("start") or r.get("task")))
        if before is current:
            continue
        before_s = float(before["total_s"])
        current_s = float(current["total_s"])
        pairs.append(
            {
                "fingerprint": fingerprint,
                "task_class": current.get("task_class"),
                "before_task": before.get("task"),
                "current_task": current.get("task"),
                "before_s": before_s,
                "current_s": current_s,
                "delta_s": round(before_s - current_s, 3),
                "speedup": round(before_s / current_s, 3) if current_s > 0 else None,
            }
        )
    return pairs


def _median(values: list[float | None]) -> float | None:
    measured = _measured(values)
    return round(statistics.median(measured), 3) if measured else None


def _first_pass(record: dict) -> bool | None:
    if record.get("repairs") is None or record.get("failures") is None:
        return None
    return bool(record.get("repairs") == 0 and record.get("failures") == 0)


def aggregate(records: list[dict]) -> dict:
    """Separate speed metrics (§21) — never mixed with AI utilization.

    FIRST_PASS_RATE: share of tasks green without agent-error retry.
    REUSE_SPEEDUP: median cold/warm total ratio per fingerprint (§18).
    """
    tasks = speed_tasks(records)
    first_passes = [_first_pass(r) for r in tasks]
    known = [v for v in first_passes if v is not None]
    ratios: list[float] = []
    by_fingerprint: dict[str, list[dict]] = {}
    for record in tasks:
        fingerprint = record.get("fingerprint")
        if fingerprint:
            by_fingerprint.setdefault(str(fingerprint), []).append(record)
    for group in by_fingerprint.values():
        cold = _measured([r.get("total_s") for r in group if r.get("reuse") == "cold"])
        warm = _measured([r.get("total_s") for r in group if r.get("reuse") == "warm"])
        if cold and warm and statistics.median(warm) > 0:
            ratios.append(statistics.median(cold) / statistics.median(warm))
    repair_ratios = _measured(
        [
            repair_tax(r).get("repair_tax_ratio")
            for r in tasks
            if repair_tax(r).get("repair_tax_ratio") is not None
        ]
    )
    humans = [r.get("human_interventions") for r in tasks]
    return {
        "n": len(tasks),
        "task_wall_s_median": _median([r.get("total_s") for r in tasks]),
        "validation_s_median": _median([r.get("validation_s") for r in tasks]),
        "first_pass_rate": round(sum(known) / len(known), 3) if known else None,
        "first_pass_known": len(known),
        "repair_tax_median": round(statistics.median(repair_ratios), 3) if repair_ratios else None,
        "reuse_speedup": round(statistics.median(ratios), 3) if ratios else None,
        "reuse_pairs": len(ratios),
        "human_interventions": sum(h for h in humans if isinstance(h, int)),
    }
