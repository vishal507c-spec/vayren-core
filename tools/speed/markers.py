"""Phase markers and engineering-loop milestone segments (Phase 18).

Measurement only: marker events are timestamped facts appended to
``scripts/speed_marks.jsonl``. Anything that cannot be derived from actually
recorded markers is ``None`` (rendered as UNMEASURED) — never estimated.

Engineering phases (coarse, §3 — marked repeatedly during a task)::

    DISCOVERY, PLANNING, IMPLEMENTATION, VALIDATION, REPAIR, DONE

Loop milestones (one-time timestamps per task, §2, canonical order)::

    TASK_START -> REPOSITORY_UNDERSTANDING_START -> PLAN_READY ->
    FIRST_EDIT -> IMPLEMENTATION_COMPLETE -> FIRST_VALIDATION ->
    FINAL_GREEN -> TASK_END

A marker event is a dict with keys: ``task``, ``ts`` (ISO-8601), ``kind``
(``"milestone"`` | ``"phase"``), ``name``, ``file`` | None, ``note`` | None,
``duration_ms`` | None.
"""

from __future__ import annotations

from datetime import datetime

PHASES: tuple[str, ...] = (
    "DISCOVERY",
    "PLANNING",
    "IMPLEMENTATION",
    "VALIDATION",
    "REPAIR",
    "DONE",
)

MILESTONES: tuple[str, ...] = (
    "TASK_START",
    "REPOSITORY_UNDERSTANDING_START",
    "PLAN_READY",
    "FIRST_EDIT",
    "IMPLEMENTATION_COMPLETE",
    "FIRST_VALIDATION",
    "FINAL_GREEN",
    "TASK_END",
)

# Segment key -> (start milestone, end milestone). §2 required metrics map:
# repo_understanding_s, first_edit_latency_s, implementation_s,
# validation_window_s, total_s. startup_s / pre_validation_gap_s /
# closeout_s are the honest remainder segments (§19 "other measurable
# phases") — they are reported, never silently folded elsewhere.
SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("startup_s", "TASK_START", "REPOSITORY_UNDERSTANDING_START"),
    ("repo_understanding_s", "REPOSITORY_UNDERSTANDING_START", "PLAN_READY"),
    ("first_edit_latency_s", "PLAN_READY", "FIRST_EDIT"),
    ("implementation_s", "FIRST_EDIT", "IMPLEMENTATION_COMPLETE"),
    ("pre_validation_gap_s", "IMPLEMENTATION_COMPLETE", "FIRST_VALIDATION"),
    ("validation_window_s", "FIRST_VALIDATION", "FINAL_GREEN"),
    ("closeout_s", "FINAL_GREEN", "TASK_END"),
)

TOTAL_KEY = "total_s"


def parse_ts(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None when unparseable."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def first_milestones(marks: list[dict], task: str) -> dict[str, str]:
    """First recorded timestamp per milestone for a task (first wins).

    Later duplicates are ignored: re-marking a milestone must not rewrite
    history (baseline preservation, §6).
    """
    found: dict[str, str] = {}
    ordered = sorted(
        (m for m in marks if m.get("task") == task and m.get("kind") == "milestone"),
        key=lambda m: str(m.get("ts") or ""),
    )
    for mark in ordered:
        name = mark.get("name")
        if name in MILESTONES and name not in found and parse_ts(mark.get("ts")) is not None:
            found[str(name)] = str(mark.get("ts"))
    return found


def loop_segments(marks: list[dict], task: str) -> dict[str, float | None]:
    """Derive §2 loop segments from milestone markers.

    A segment is measured only when both endpoints exist and are ordered;
    otherwise it is None (UNMEASURED). Segments are independent — a missing
    endpoint never borrows time from a neighbouring segment.
    """
    milestones = first_milestones(marks, task)
    parsed = {name: parse_ts(ts) for name, ts in milestones.items()}
    segments: dict[str, float | None] = {}
    for key, start, end in SEGMENTS:
        start_dt = parsed.get(start)
        end_dt = parsed.get(end)
        if start_dt is None or end_dt is None:
            segments[key] = None
        elif end_dt < start_dt:
            segments[key] = None  # out-of-order evidence: do not invent
        else:
            segments[key] = round((end_dt - start_dt).total_seconds(), 3)
    start_dt = parsed.get("TASK_START")
    end_dt = parsed.get("TASK_END")
    if start_dt is None or end_dt is None or end_dt < start_dt:
        segments[TOTAL_KEY] = None
    else:
        segments[TOTAL_KEY] = round((end_dt - start_dt).total_seconds(), 3)
    return segments


def phase_totals(marks: list[dict], task: str, idle_s: float = 120.0) -> dict[str, float]:
    """Attribute measured time to §3 phases from phase markers.

    A phase persists until the next marker (same rule as forensics); a gap
    larger than ``idle_s`` is split out as ``UNKNOWN`` instead of being
    attributed. Explicit ``duration_ms`` on a mark adds measured time to
    that phase. The terminal ``DONE`` marker carries no duration.
    """
    totals: dict[str, float] = {}
    unknown = 0.0
    ordered = sorted(
        (m for m in marks if m.get("task") == task and m.get("kind") == "phase"),
        key=lambda m: str(m.get("ts") or ""),
    )
    stamps: list[tuple[datetime, dict]] = []
    for mark in ordered:
        stamp = parse_ts(mark.get("ts"))
        if stamp is not None:
            stamps.append((stamp, mark))
    for index, (stamp, mark) in enumerate(stamps):
        name = str(mark.get("name") or "")
        if name and name != "DONE":
            totals.setdefault(name, 0.0)
        duration_ms = mark.get("duration_ms")
        if name and name != "DONE" and isinstance(duration_ms, (int, float)):
            totals[name] = round(totals[name] + float(duration_ms) / 1000.0, 3)
        if index + 1 < len(stamps) and name and name != "DONE":
            gap = (stamps[index + 1][0] - stamp).total_seconds()
            if gap < 0:
                continue  # out-of-order evidence: attribute nothing
            if gap <= idle_s:
                totals[name] = round(totals[name] + gap, 3)
            else:
                unknown = round(unknown + gap, 3)
    totals["UNKNOWN"] = round(unknown, 3)
    return totals
