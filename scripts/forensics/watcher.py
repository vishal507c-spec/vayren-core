"""Sweep-based file watcher: records modification evidence without a daemon."""

from __future__ import annotations

from datetime import datetime

import store
from store import append_event, load_events, load_json, now_iso, save_json, tree_scan


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _task_start_ts(task_id: str) -> datetime:
    for event in load_events(task_id):
        if event.get("action") == "task_start":
            return parse_iso(event["ts"])
    return datetime.now().astimezone()


def _task_end_ts(task_id: str) -> datetime | None:
    for event in load_events(task_id):
        if event.get("action") == "task_end":
            return parse_iso(event["ts"])
    return None


def sweep(task_id: str) -> int:
    """Compare the current tree against the stored snapshot; log watch events.

    Event timestamps use each file's mtime (measured), clamped to the task
    window. Returns the number of events logged.
    """
    snapshot = load_json(task_id, "snapshot.json", {})
    current = tree_scan(store.ROOT)
    start_dt = _task_start_ts(task_id)
    end_dt = _task_end_ts(task_id)
    logged = 0
    for rel, (mtime, size) in current.items():
        old = snapshot.get(rel)
        if old is not None and old[0] == mtime and old[1] == size:
            continue
        ts_dt = datetime.fromtimestamp(mtime).astimezone()
        if ts_dt < start_dt:
            ts_dt = start_dt
        if end_dt is not None and ts_dt > end_dt:
            ts_dt = end_dt
        append_event(
            task_id,
            {
                "task_id": task_id,
                "ts": ts_dt.isoformat(timespec="milliseconds"),
                "phase": "CODE_GENERATION",
                "action": "watch_mod",
                "file": rel,
                "command": None,
                "status": None,
                "duration_ms": None,
                "note": None,
                "source": "watch",
                "accuracy": "MEASURED",
            },
        )
        logged += 1
    for rel in snapshot:
        if rel not in current:
            append_event(
                task_id,
                {
                    "task_id": task_id,
                    "ts": now_iso(),
                    "phase": None,
                    "action": "watch_del",
                    "file": rel,
                    "command": None,
                    "status": None,
                    "duration_ms": None,
                    "note": None,
                    "source": "watch",
                    "accuracy": "MEASURED",
                },
            )
            logged += 1
    save_json(task_id, "snapshot.json", current)
    return logged
