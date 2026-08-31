"""Interval building from the raw event stream.

Every millisecond of the task window belongs to exactly one interval.
Phase boundaries come from explicit markers and wrapped commands
(MEASURED). Event-free gaps beyond a threshold become UNKNOWN intervals
(INFERRED phase, MEASURED boundaries) unless the agent explicitly marked
them as AI_TOOL_WAIT or HUMAN_IDLE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from watcher import parse_iso

PHASES = frozenset(
    {
        "REPOSITORY_SEARCH",
        "CONTEXT_READING",
        "ARCHITECTURE_ANALYSIS",
        "PLANNING",
        "CODE_GENERATION",
        "COMMAND_EXECUTION",
        "BUILD_STARTUP",
        "TESTING",
        "DEBUGGING",
        "REWORK",
        "VALIDATION",
        "AI_TOOL_WAIT",
        "HUMAN_IDLE",
        "UNKNOWN",
    }
)

_ANCHOR_ACTIONS = {"run_start", "run_end"}


@dataclass
class Interval:
    start: datetime
    end: datetime
    phase: str
    accuracy: str
    evidence: list[dict] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() * 1000))


def _sort(events: list[dict]) -> list[dict]:
    return sorted(events, key=lambda event: parse_iso(event["ts"]))


def task_window(events: list[dict]) -> tuple[datetime, datetime | None]:
    start: datetime | None = None
    end: datetime | None = None
    for event in events:
        ts = parse_iso(event["ts"])
        if event.get("action") == "task_start":
            start = ts
        elif event.get("action") == "task_end":
            end = ts
    if start is None:
        start = parse_iso(events[0]["ts"]) if events else datetime.now().astimezone()
    return start, end


def build_intervals(
    events: list[dict],
    idle_threshold_ms: int = 120_000,
    window_end: datetime | None = None,
) -> tuple[list[Interval], list[dict]]:
    """Non-overlapping intervals covering the whole task window.

    The window is [TASK_START, TASK_END]; window_end (the TASK_END
    timestamp, or the analysis moment for open tasks) is resolved exactly
    once so the partition always sums to the elapsed duration.

    Returns (intervals, unattributed_events) — unattributed_events is
    always empty in the current model; kept for auditability.
    """
    ordered = _sort(events)
    start, task_end = task_window(ordered)
    if window_end is not None:
        end = window_end
    elif task_end is not None:
        end = task_end
    elif ordered:
        end = parse_iso(ordered[-1]["ts"])
    else:
        end = datetime.now().astimezone()
    anchors = [
        event for event in ordered if event.get("phase") and event.get("action") != "run_end"
    ]
    if not anchors:
        return [Interval(start, end, "UNKNOWN", "UNKNOWN", ordered)], []
    intervals: list[Interval] = []
    first_ts = parse_iso(anchors[0]["ts"])
    if first_ts > start:
        intervals.append(Interval(start, first_ts, "UNKNOWN", "UNKNOWN", []))
    for index, anchor in enumerate(anchors):
        nxt = anchors[index + 1] if index + 1 < len(anchors) else None
        anchor_ts = parse_iso(anchor["ts"])
        span_end = parse_iso(nxt["ts"]) if nxt else end
        if anchor.get("action") == "run_start":
            pair_end = _matching_run_end(anchor, ordered)
            if pair_end is not None:
                pair_ts = parse_iso(pair_end["ts"])
                intervals.append(
                    Interval(
                        anchor_ts,
                        pair_ts,
                        anchor["phase"],
                        "MEASURED",
                        [anchor, pair_end],
                    )
                )
                anchor_ts = pair_ts
        between = [event for event in ordered if anchor_ts < parse_iso(event["ts"]) < span_end]
        points = [anchor_ts, *[parse_iso(event["ts"]) for event in between], span_end]
        for a, b in zip(points, points[1:], strict=False):
            gap_ms = int((b - a).total_seconds() * 1000)
            if gap_ms > idle_threshold_ms:
                intervals.append(Interval(a, b, "UNKNOWN", "INFERRED", []))
            else:
                intervals.append(Interval(a, b, anchor["phase"], "MEASURED", between))
    return intervals, []


def _matching_run_end(run_start: dict, ordered: list[dict]) -> dict | None:
    """The run_end belonging to run_start (consecutive pair by command)."""
    found = False
    for event in ordered:
        if event is run_start:
            found = True
            continue
        if not found:
            continue
        if event.get("action") == "run_end":
            return event
        if event.get("action") == "run_start":
            return None
    return None
