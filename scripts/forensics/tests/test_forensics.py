"""Automatic full-task measurement tests.

The system follows the REAL agent lifecycle (turn start/end from the agent
log) - no manual task-start/task-end. Every trace uses controlled
timestamps; the partition invariant (MEASURED + UNKNOWN == TOTAL, diff 0ms)
is asserted throughout.
"""

from __future__ import annotations

import json
import os
import re
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import store as flat_store
from forensics import agent, analysis, recorder
from report import render_task_report

T0 = datetime(2026, 8, 15, 9, 0, 0).astimezone()


def ts(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def write_task(tmp_path: Path, task_id: str, events: list[dict]) -> Path:
    task_dir = tmp_path / ".forensics" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    with (task_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return task_dir


def base(task_id: str, dt: datetime, action: str, phase: str | None = None) -> dict:
    return {
        "task_id": task_id,
        "ts": ts(dt),
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


def utc_to_local(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def check_partition(metrics: analysis.TaskMetrics) -> None:
    covered = sum(i.duration_ms for i in metrics.intervals)
    assert covered == metrics.elapsed_ms, f"covered {covered}ms != elapsed {metrics.elapsed_ms}ms"
    assert (
        metrics.unknown_ms + sum(i.duration_ms for i in metrics.intervals if i.phase != "UNKNOWN")
        == metrics.elapsed_ms
    )


def check_no_overlap(metrics: analysis.TaskMetrics) -> None:
    ordered = sorted(metrics.intervals, key=lambda i: i.start)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.end <= nxt.start, f"overlap: {prev} vs {nxt}"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Isolated forensics environment: tmp repo root + agent log file."""
    monkeypatch.setattr(flat_store, "ROOT", tmp_path)
    log = tmp_path / "agent.log"
    import agent as flat_agent

    monkeypatch.setattr(flat_agent, "agent_log_path", lambda: log)
    return tmp_path, log


def write_agent_log(log: Path, entries: list[tuple[str, str, str]]) -> None:
    """entries = [(utc_ts, kind, detail)]; kind in {process, exit, error}."""
    lines = []
    for utc, kind, detail in entries:
        if kind == "process":
            lines.append(
                f"timestamp={utc} level=INFO run=x message=process "
                f"session.id=ses_a messageID={detail}"
            )
        elif kind == "exit":
            lines.append(
                f'timestamp={utc} level=INFO run=x message="exiting loop" session.id={detail}'
            )
        else:
            lines.append(
                f'timestamp={utc} level=INFO run=x message="stream error" session.id={detail}'
            )
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mark_args(**overrides) -> Namespace:
    values = {
        "name": None,
        "phase": "CONTEXT_READING",
        "action": "read_file",
        "file": "x.py",
        "command": None,
        "status": None,
        "note": None,
        "duration_ms": None,
    }
    values.update(overrides)
    return Namespace(**values)


# ---------------------------------------------------------------- agent log


def test_parse_turns_from_agent_log(env: tuple[Path, Path]) -> None:
    _, log = env
    write_agent_log(
        log,
        [
            ("2026-08-15T03:50:33.111Z", "process", "msg_aaa"),
            ("2026-08-15T03:52:00.000Z", "process", "msg_bbb"),
            ("2026-08-15T04:00:12.485Z", "exit", "ses_a"),
            ("2026-08-15T04:01:00.000Z", "process", "msg_ccc"),
        ],
    )
    turns = agent.parse_turns(log)
    assert [t.msg_id for t in turns] == ["msg_aaa", "msg_ccc"]
    assert turns[0].start == utc_to_local("2026-08-15T03:50:33.111Z")
    assert turns[0].end == utc_to_local("2026-08-15T04:00:12.485Z")
    assert turns[1].end is None
    turn = agent.current_turn(log)
    assert turn is not None
    assert turn.msg_id == "msg_ccc"


def test_stream_error_closes_turn(env: tuple[Path, Path]) -> None:
    _, log = env
    write_agent_log(
        log,
        [
            ("2026-08-15T03:50:33.111Z", "process", "msg_aaa"),
            ("2026-08-15T03:51:00.000Z", "error", "ses_a"),
        ],
    )
    turns = agent.parse_turns(log)
    assert turns[0].end == utc_to_local("2026-08-15T03:51:00.000Z")


# ------------------------------------------------------------ auto session


def test_first_invocation_opens_session_at_turn_start(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp, log = env
    del tmp
    turn_start = T0
    clock = {"t": T0 + timedelta(minutes=2)}
    monkeypatch.setattr(recorder, "now_iso", lambda: ts(clock["t"]))
    write_agent_log(log, [(utc_of(turn_start), "process", "msg_open")])

    assert recorder.cmd_mark(mark_args()) == 0

    state = flat_store.load_state()
    assert state["msg_id"] == "msg_open"
    assert state["end_iso"] is None
    raw = flat_store.load_events(state["task_id"])
    start = [e for e in raw if e.get("action") == "task_start"]
    assert len(start) == 1
    assert start[0]["event"] == "TASK_START"
    assert start[0]["ts"] == ts(turn_start)
    assert start[0]["source"] == "agent"
    assert raw[-1]["action"] == "read_file"
    assert raw[-1]["phase"] == "CONTEXT_READING"
    assert raw[-1]["ts"] == ts(clock["t"])
    assert state["task_id"].startswith("turn-msg_open")


def utc_of(dt: datetime) -> str:
    return dt.astimezone(datetime.now().astimezone().tzinfo).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def test_new_turn_auto_closes_previous_and_reports(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    tmp, log = env
    del tmp
    turn_start = T0
    first_mark = T0 + timedelta(minutes=2)
    prev_exit = T0 + timedelta(minutes=12)
    next_start = T0 + timedelta(minutes=14)
    clock = {"t": first_mark}
    monkeypatch.setattr(recorder, "now_iso", lambda: ts(clock["t"]))

    write_agent_log(
        log,
        [
            (utc_of(turn_start), "process", "msg_prev"),
            (utc_of(T0 + timedelta(minutes=5)), "process", "msg_prev2"),
        ],
    )
    assert recorder.cmd_mark(mark_args()) == 0
    prev_task = flat_store.load_state()["task_id"]

    write_agent_log(
        log,
        [
            (utc_of(turn_start), "process", "msg_prev"),
            (utc_of(T0 + timedelta(minutes=5)), "process", "msg_prev2"),
            (utc_of(prev_exit), "exit", "ses_a"),
            (utc_of(next_start), "process", "msg_next"),
        ],
    )
    clock["t"] = T0 + timedelta(minutes=15)
    assert recorder.cmd_mark(mark_args()) == 0
    out = capsys.readouterr().out

    state = flat_store.load_state()
    assert state["msg_id"] == "msg_next"
    raw_prev = flat_store.load_events(prev_task)
    ends = [e for e in raw_prev if e.get("action") == "task_end"]
    assert len(ends) == 1
    assert ends[0]["event"] == "TASK_END"
    assert ends[0]["ts"] == ts(prev_exit)
    assert "CODING TIME FORENSICS" in out
    assert "PARTITION CHECK:" in out
    metrics = analysis.analyze_task(prev_task)
    check_partition(metrics)


def test_file_write_is_auto_captured_as_code_generation(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp, log = env
    now = datetime.now().astimezone()
    turn_start = now - timedelta(minutes=10)
    mark_t = now - timedelta(minutes=2)
    write_ts = now - timedelta(seconds=30)
    clock = {"t": mark_t}
    monkeypatch.setattr(recorder, "now_iso", lambda: ts(clock["t"]))
    write_agent_log(log, [(utc_of(turn_start), "process", "msg_w")])
    assert recorder.cmd_mark(mark_args()) == 0
    task_id = flat_store.load_state()["task_id"]

    target = tmp / "scripts" / "new_file.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")
    os.utime(target, (write_ts.timestamp(), write_ts.timestamp()))

    from argparse import Namespace

    assert recorder.cmd_sweep(Namespace()) == 0

    raw = flat_store.load_events(task_id)
    mods = [e for e in raw if e.get("action") == "watch_mod"]
    assert len(mods) == 1
    assert mods[0]["phase"] == "CODE_GENERATION"
    assert mods[0]["source"] == "watch"
    assert mods[0]["ts"] == ts(write_ts)

    metrics = analysis.analyze_task(task_id)
    assert metrics.phase_ms("CODE_GENERATION") >= 20_000
    check_partition(metrics)
    check_no_overlap(metrics)


def test_fallback_without_agent_log(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp, log = env
    del log
    import agent as flat_agent

    monkeypatch.setattr(flat_agent, "agent_log_path", lambda: None)

    task_id = "old-session"
    old_start = T0
    old_last = T0 + timedelta(minutes=5)
    events = [
        base(task_id, old_start, "task_start"),
        base(task_id, old_last, "read_file", "CONTEXT_READING"),
    ]
    write_task(tmp, task_id, events)
    flat_store.save_state(
        {
            "task_id": task_id,
            "task_name": None,
            "session_id": None,
            "msg_id": None,
            "start_iso": ts(old_start),
            "end_iso": None,
            "source": "auto",
        }
    )

    current_id, closed_id = recorder.auto_manage(None, session_idle_ms=1)

    assert closed_id == task_id
    raw = flat_store.load_events(task_id)
    ends = [e for e in raw if e.get("action") == "task_end"]
    assert len(ends) == 1
    assert ends[0]["event"] == "TASK_END"
    assert ends[0]["ts"] == ts(old_last)
    metrics = analysis.analyze_task(task_id)
    check_partition(metrics)
    # a fresh fallback session opens for the next task
    assert current_id is not None
    assert current_id != task_id
    assert flat_store.load_state()["end_iso"] is None


# ----------------------------------------------------------- report format


def _metrics_for(events: list[dict]) -> analysis.TaskMetrics:

    task_id = "fmt"
    write_task(flat_store.ROOT, task_id, events)
    return analysis.analyze_task(task_id)


def test_report_format_matches_spec(env: tuple[Path, Path]) -> None:
    del env
    task_id = "fmt"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=5), "read_file", "CONTEXT_READING"),
        base(task_id, T0 + timedelta(minutes=6), "read_file", "CONTEXT_READING"),
        {
            **base(task_id, T0 + timedelta(minutes=10), "run_start", "TESTING"),
            "command": "pytest",
            "source": "wrap",
        },
        {
            **base(task_id, T0 + timedelta(minutes=12, seconds=30), "run_end", "TESTING"),
            "command": "pytest",
            "status": 0,
            "duration_ms": 150_000,
            "source": "wrap",
        },
        base(task_id, T0 + timedelta(minutes=25), "task_end"),
    ]
    out = render_task_report(_metrics_for(events))

    assert "CODING TIME FORENSICS" in out
    assert "Task: fmt" in out
    assert "Total elapsed:       25m 00s" in out
    assert "Measured:            3m 30s" in out
    assert "Unknown:             21m 30s" in out
    assert re.search(r"Context Reading\s+1m 00s\s+4\.0%", out)
    assert re.search(r"Testing\s+2m 30s\s+10\.0%", out)
    assert re.search(r"Unknown\s+21m 30s\s+86\.0%", out)
    assert re.search(r"TOTAL\s+25m 00s\s+100\.0%", out)
    assert "PARTITION CHECK:" in out
    assert "Measured + Unknown = Total" in out
    assert "diff 0ms" in out
    assert "TOP TIME CONSUMERS" in out
    assert "1. Unknown          - 21m 30s" in out
    assert "2. Testing          - 2m 30s" in out
    assert "3. Context Reading  - 1m 00s" in out


def test_report_unknown_only_when_no_evidence(env: tuple[Path, Path]) -> None:
    del env
    task_id = "bare"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=30), "task_end"),
    ]
    out = render_task_report(_metrics_for(events))

    assert re.search(r"Unknown\s+30m 00s\s+100\.0%", out)
    assert re.search(r"TOTAL\s+30m 00s\s+100\.0%", out)
    assert "diff 0ms" in out
    assert "1. Unknown          - 30m 00s" in out
    for label in ("Context Reading", "Code Generation", "Testing"):
        assert label not in out, f"phase {label} must not appear without evidence"


def test_report_folds_related_phases(env: tuple[Path, Path]) -> None:
    del env
    task_id = "fold"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=1), "search", "REPOSITORY_SEARCH"),
        base(task_id, T0 + timedelta(minutes=2), "search", "FILE_DISCOVERY"),
        base(task_id, T0 + timedelta(minutes=3), "task_end"),
    ]
    out = render_task_report(_metrics_for(events))

    assert re.search(r"Repository Search\s+2m 00s", out)


# ------------------------------------------------------- analysis invariants


def test_gaps_become_unknown_and_total_is_complete(env: tuple[Path, Path]) -> None:
    del env
    task_id = "gaps"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=5), "read_file", "CONTEXT_READING"),
        base(task_id, T0 + timedelta(minutes=6), "read_file", "CONTEXT_READING"),
        base(task_id, T0 + timedelta(minutes=25), "task_end"),
    ]
    write_task(flat_store.ROOT, task_id, events)
    metrics = analysis.analyze_task(task_id)

    assert metrics.elapsed_ms == 25 * 60_000
    assert metrics.phase_ms("CONTEXT_READING") == 60_000
    assert metrics.unknown_ms == 24 * 60_000
    check_partition(metrics)
    check_no_overlap(metrics)


def test_markers_written_after_the_window_closed_do_not_inflate_partition(
    env: tuple[Path, Path],
) -> None:
    del env
    task_id = "late_markers"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=1), "validate", "TESTING"),
        base(task_id, T0 + timedelta(minutes=2), "task_end"),
        base(task_id, T0 + timedelta(minutes=3), "validate", "VALIDATION"),
        base(task_id, T0 + timedelta(minutes=4), "cleanup", "VALIDATION"),
    ]
    write_task(flat_store.ROOT, task_id, events)
    metrics = analysis.analyze_task(task_id)

    assert metrics.elapsed_ms == 2 * 60_000
    check_partition(metrics)
    check_no_overlap(metrics)


def test_no_markers_at_all_single_unknown_interval(env: tuple[Path, Path]) -> None:
    del env
    task_id = "bare"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=30), "task_end"),
    ]
    write_task(flat_store.ROOT, task_id, events)
    metrics = analysis.analyze_task(task_id)

    assert metrics.elapsed_ms == 30 * 60_000
    assert len(metrics.intervals) == 1
    assert metrics.intervals[0].phase == "UNKNOWN"
    assert metrics.intervals[0].duration_ms == 30 * 60_000
    check_partition(metrics)


def test_open_task_uses_analysis_moment_once(env: tuple[Path, Path]) -> None:
    del env
    task_id = "open"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=2), "read_file", "CONTEXT_READING"),
    ]
    write_task(flat_store.ROOT, task_id, events)
    metrics = analysis.analyze_task(task_id)

    assert metrics.end is not None
    assert metrics.elapsed_ms > 0
    check_partition(metrics)
    check_no_overlap(metrics)


def test_measured_plus_unknown_equals_total_zero_diff(env: tuple[Path, Path]) -> None:
    del env
    task_id = "identity"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=1), "edit_file", "CODE_GENERATION"),
        base(task_id, T0 + timedelta(minutes=1, seconds=40), "edit_file", "CODE_GENERATION"),
        base(task_id, T0 + timedelta(minutes=4), "run_start", "TESTING"),
        {
            **base(task_id, T0 + timedelta(minutes=4, seconds=30), "run_end", "TESTING"),
            "command": "pytest",
            "status": 0,
            "duration_ms": 30_000,
            "source": "wrap",
        },
        base(task_id, T0 + timedelta(minutes=9), "task_end"),
    ]
    write_task(flat_store.ROOT, task_id, events)
    metrics = analysis.analyze_task(task_id)

    measured = sum(i.duration_ms for i in metrics.intervals if i.phase != "UNKNOWN")
    assert metrics.unknown_ms + measured == metrics.elapsed_ms
    assert metrics.elapsed_ms == 9 * 60_000
    check_no_overlap(metrics)


def test_backward_compat_old_event_format(env: tuple[Path, Path]) -> None:
    del env
    task_id = "legacy"
    events = [
        base(task_id, T0, "task_start"),
        base(task_id, T0 + timedelta(minutes=3), "edit_file", "CODE_GENERATION"),
        base(task_id, T0 + timedelta(minutes=3, seconds=30), "edit_file", "CODE_GENERATION"),
        base(task_id, T0 + timedelta(minutes=10), "task_end"),
    ]
    write_task(flat_store.ROOT, task_id, events)
    metrics = analysis.analyze_task(task_id)

    assert metrics.elapsed_ms == 10 * 60_000
    assert metrics.phase_ms("CODE_GENERATION") == 30_000
    assert metrics.unknown_ms == 9.5 * 60_000
    check_partition(metrics)
