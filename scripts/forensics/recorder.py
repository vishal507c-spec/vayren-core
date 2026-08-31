"""Forensics recorder: automatic session boundaries, markers, wrapped commands.

Sessions follow the REAL coding-task lifecycle from the agent's own log
(see agent.py): a task starts at the turn's first processed message and ends
at the turn's exit. No manual task-start/task-end commands are needed.

Raw evidence captured automatically at every invocation:
- wrapped command start/end (run_start/run_end),
- file writes detected by tree scan (watch_mod, phase CODE_GENERATION),
- marks are optional refinements (context reading, planning, ...).

Without an agent log, sessions fall back to evidence-based boundaries:
open at first invocation, close after session_idle without activity.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from datetime import datetime
from pathlib import Path

import agent
import store
from analysis import analyze_task
from intervals import PHASES
from report import render_task_report
from store import (
    append_event,
    events_path,
    git_numstat,
    load_events,
    now_iso,
    save_json,
    tree_scan,
)
from watcher import parse_iso, sweep

PHASE_CHOICES = sorted(PHASES)


def _base_event(task_id: str, phase: str | None, action: str) -> dict:
    return {
        "task_id": task_id,
        "ts": now_iso(),
        "phase": phase,
        "action": action,
        "file": None,
        "command": None,
        "status": None,
        "duration_ms": None,
        "note": None,
        "source": "marker",
        "accuracy": "MEASURED",
    }


def _task_id_for_turn(turn: agent.Turn) -> str:
    return f"turn-{turn.msg_id}"


def _open_session(turn: agent.Turn | None, name: str | None) -> dict:
    """Open a session: TASK_START with the REAL start timestamp."""
    start_dt = turn.start if turn is not None else datetime.now().astimezone()
    task_id = _task_id_for_turn(turn) if turn is not None else f"session-{start_dt:%Y%m%d-%H%M%S}"
    if events_path(task_id).exists():
        task_id = f"{task_id}-{start_dt:%H%M%S}"
    append_event(
        task_id,
        {
            **_base_event(task_id, None, "task_start"),
            "event": "TASK_START",
            "note": name,
            "source": "agent" if turn is not None else "auto",
            "ts": start_dt.isoformat(timespec="milliseconds"),
        },
    )
    save_json(task_id, "snapshot.json", tree_scan(store.ROOT))
    save_json(task_id, "numstat_baseline.json", git_numstat())
    state = {
        "task_id": task_id,
        "task_name": name,
        "session_id": turn.session_id if turn is not None else None,
        "msg_id": turn.msg_id if turn is not None else None,
        "start_iso": start_dt.isoformat(timespec="milliseconds"),
        "end_iso": None,
        "source": "agent" if turn is not None else "auto",
    }
    store.save_state(state)
    return state


def _close_session(state: dict, end_dt: datetime, note: str | None = None) -> str:
    """Close a session: TASK_END with the REAL end timestamp, freeze evidence."""
    task_id = state["task_id"]
    append_event(
        task_id,
        {
            **_base_event(task_id, None, "task_end"),
            "event": "TASK_END",
            "note": note,
            "source": "agent" if state.get("source") == "agent" else "auto",
            "ts": end_dt.isoformat(timespec="milliseconds"),
        },
    )
    sweep(task_id)
    save_json(task_id, "numstat_end.json", git_numstat())
    state["end_iso"] = end_dt.isoformat(timespec="milliseconds")
    store.save_state(state)
    return task_id


def _end_of_turn(path: Path | None, msg_id: str) -> datetime | None:
    """The real exit timestamp of the turn with the given msg id."""
    if path is None:
        return None
    for turn in agent.parse_turns(path):
        if turn.msg_id == msg_id:
            return turn.end
    return None


def auto_manage(
    name: str | None = None, session_idle_ms: int = 900_000
) -> tuple[str | None, str | None]:
    """Open/close sessions from real agent turn boundaries.

    Returns (current_task_id, closed_task_id). Closing a previous task
    prints its final report automatically (auto-report at task completion).
    """
    path = agent.agent_log_path()
    turn = agent.current_turn(path)
    state = store.load_state()
    closed_id: str | None = None
    if state:
        if turn is not None and state.get("msg_id") != turn.msg_id:
            end_dt = _end_of_turn(path, state.get("msg_id", "")) or turn.start
            closed_id = _close_session(state, end_dt)
            print()
            print(render_task_report(analyze_task(closed_id)))
            state = {}
        elif turn is not None and turn.end is not None and not state.get("end_iso"):
            closed_id = _close_session(state, turn.end)
            print()
            print(render_task_report(analyze_task(closed_id)))
            state = {}
        elif turn is None and not state.get("end_iso"):
            last_ts = max(
                (parse_iso(event["ts"]) for event in load_events(state["task_id"])),
                default=datetime.now().astimezone(),
            )
            idle_ms = int((datetime.now().astimezone() - last_ts).total_seconds() * 1000)
            if idle_ms >= session_idle_ms:
                closed_id = _close_session(state, last_ts)
                print()
                print(render_task_report(analyze_task(closed_id)))
                state = {}
    if not state:
        if turn is not None and turn.end is None:
            return _open_session(turn, name)["task_id"], closed_id
        if turn is None:
            return _open_session(None, name)["task_id"], closed_id
    return state.get("task_id"), closed_id


def session_task_id() -> str | None:
    state = store.load_state()
    return state.get("task_id") if state and not state.get("end_iso") else None


def cmd_mark(args: argparse.Namespace) -> int:
    phase = args.phase.upper() if args.phase else None
    if phase and phase not in PHASES:
        print(f"warning: unknown phase {phase!r} (expected one of {PHASE_CHOICES})")
    task_id, _ = auto_manage(args.name)
    if task_id is None:
        return 0
    sweep(task_id)
    event = _base_event(task_id, phase, args.action)
    event.update(
        {
            "file": args.file,
            "command": args.command,
            "status": args.status,
            "note": args.note,
            "duration_ms": args.duration_ms,
        }
    )
    append_event(task_id, event)
    print(f"marked {args.action}" + (f" [{phase}]" if phase else ""))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    phase = args.phase.upper()
    if phase not in PHASES:
        print(f"error: unknown phase {phase!r}")
        return 2
    task_id, _ = auto_manage(args.name)
    if task_id is None:
        return 2
    sweep(task_id)
    command_args = list(args.command)
    if command_args and command_args[0] == "--":
        command_args = command_args[1:]
    command = " ".join(command_args)
    append_event(
        task_id,
        {
            **_base_event(task_id, phase, "run_start"),
            "command": command,
            "source": "wrap",
        },
    )
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command_args,
            cwd=store.ROOT,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        status = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        status = -1
        output = str(exc)
    duration_ms = int((time.perf_counter() - started) * 1000)
    append_event(
        task_id,
        {
            **_base_event(task_id, phase, "run_end"),
            "command": command,
            "status": status,
            "duration_ms": duration_ms,
            "note": output[-2000:] if args.capture else None,
            "source": "wrap",
        },
    )
    print(f"exit={status} duration={duration_ms}ms phase={phase}")
    return status if status >= 0 else 1


def cmd_sweep(args: argparse.Namespace) -> int:
    del args
    task_id, _ = auto_manage()
    if task_id is None:
        print("no active session - nothing to sweep")
        return 0
    count = sweep(task_id)
    print(f"swept {count} new modification event(s)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    del args
    turn = agent.current_turn(agent.agent_log_path())
    state = store.load_state()
    if turn is not None:
        end_text = turn.end.strftime("%H:%M:%S") if turn.end else "open"
        print(f"agent turn: {turn.msg_id}  start {turn.start:%H:%M:%S}  end {end_text}")
    else:
        print("agent turn: none (no agent log)")
    if state and not state.get("end_iso"):
        print(f"session:    {state['task_id']}  start {state.get('start_iso')}  open")
    elif state:
        print(f"session:    {state['task_id']}  closed {state.get('end_iso')}")
    else:
        print("session:    none")
    return 0
