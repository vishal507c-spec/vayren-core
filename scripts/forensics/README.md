# VAYREN Coding-Time Forensics (measurement only)

Read-only measurement of where engineering time goes during a development
task. No production code, no architecture, no business logic is touched.
This system only reads the repo (tree scan, `git diff --numstat`) and writes
trace files under `.forensics/` (gitignored).

## Principles

- **Automatic session boundaries** — a task is one coding-agent turn: it
  opens at the turn's first processed message and closes at the turn's exit,
  both read from the agent's own lifecycle log (see `agent.py`). No manual
  `task-start` / `task-end` commands exist.
- **Full-session capture** — `TOTAL_ELAPSED_TIME` is always exactly
  `TASK_END - TASK_START`. The whole window is partitioned into
  non-overlapping intervals; every millisecond belongs to exactly one of
  `MEASURED` or `UNKNOWN` (`MEASURED + UNKNOWN == TOTAL`, verified by the
  PARTITION CHECK line in every report, diff must be 0).
- **No double counting** — intervals never overlap; coverage is asserted in
  `analysis.analyze_task` (a mismatch raises `ValueError`).
- **TASK_START / TASK_END are immutable** — both raw events are stored with
  exact wall-clock timestamps and an `event` field (`"TASK_START"` /
  `"TASK_END"`); they are never edited after being written.
- **No manual timers** — evidence comes from wall-clock timestamps of CLI
  invocations, file mtimes, wrapped command durations, and git numstat.
- **Never invent numbers** — unmeasured time is reported as `UNKNOWN` (not
  attributed to "thinking" or any phase). Every interval is tagged
  `MEASURED` / `INFERRED` / `UNKNOWN`; undeterminable time stays `UNKNOWN`.
- **Real evidence only** — phases with no evidence are not shown; the report
  shows only what actually happened plus the honest `UNKNOWN` remainder.
- **Persistence across restarts** — a task's state lives in
  `.forensics/tasks/<id>/` (events.jsonl + snapshot + numstat baselines), so
  closing a terminal never loses the session; there is no daemon.
- **Raw events required** — reports are derived from `events.jsonl`, never
  stored as aggregates only.

## Quick start

```
# 1. just work — the first invocation auto-opens the session at the turn's
#    REAL start (from the agent lifecycle log); the session name is optional
python scripts/forensics/__main__.py mark --phase CONTEXT_READING --action read_file --file 90_brain --name "My task"

# 2. record phase evidence while working
python scripts/forensics/__main__.py mark --phase CONTEXT_READING   --action read_file    --file 05_strategy/strategy/runtime.py
python scripts/forensics/__main__.py mark --phase ARCHITECTURE_ANALYSIS --action design --note "..."

# 3. wrap commands that have measurable duration (time belongs to that phase)
python scripts/forensics/__main__.py run --phase TESTING -- python -m pytest -q

# 4. at task end, render the report (the report is ALSO auto-appended when
#    the next task's session opens — see "Auto-report")
python scripts/forensics/__main__.py report --name "My task"

# 5. other commands
python scripts/forensics/__main__.py sweep        # scan for file modifications now
python scripts/forensics/__main__.py status       # current turn + session
python scripts/forensics/__main__.py report --full          # + detailed audit sections
python scripts/forensics/__main__.py report --task-id ID    # a specific task
python scripts/forensics/__main__.py aggregate              # all tasks
python scripts/forensics/__main__.py list
```

File edits are auto-detected: every CLI call runs a tree sweep and logs
`watch_mod` events (phase `CODE_GENERATION`) with the file's mtime, clamped
to the session window. Explicit marks are only needed for phase evidence the
system cannot see (reads, searches, design decisions).

## Auto-report

When a task's turn ends (the agent log shows the turn exit, or a new turn
starts), the next forensics invocation automatically closes the session with
the REAL exit timestamp, sweeps the final evidence, and prints the full
report — the completed task's report appears by itself, no `task-end`
command required.

## Session fallback (no agent log)

If `agent_log_path()` finds no log, sessions fall back to evidence-based
boundaries: open at the first invocation, close after `session_idle_ms`
(900 s default) without any event. Override the log path with the
environment variable `VAYREN_FORENSICS_AGENT_LOG`.

## Phases

`REPOSITORY_SEARCH`, `CONTEXT_READING`, `ARCHITECTURE_ANALYSIS`, `PLANNING`,
`CODE_GENERATION`, `COMMAND_EXECUTION`, `BUILD_STARTUP`, `TESTING`,
`DEBUGGING`, `REWORK`, `VALIDATION`, `AI_TOOL_WAIT`, `HUMAN_IDLE`, `UNKNOWN`.

## Interval model

- The session window is `[TASK_START, TASK_END]`; for an open task (no
  `TASK_END`) the window closes at analysis time (`datetime.now()`), resolved
  exactly once per analysis.
- Phase markers (`mark` with `--phase`, `run` wrappers, auto-detected file
  writes) define anchors. Between two anchors the phase of the first anchor
  persists.
- A gap between events larger than `--idle-threshold` (default 120 s, per
  report) is split out as `UNKNOWN` (INFERRED).
- Time before the first marker is `UNKNOWN` (accuracy UNKNOWN).
- A wrapped `run` records its exact start/end/duration; the phase persists
  after the command until the next marker (or until the next UNKNOWN gap).
- Report shows `Measured:` / `Unknown:` totals, a phase table that always
  ends with an `UNKNOWN` row and a `TOTAL` row (all % against total elapsed),
  a `PARTITION CHECK` line proving `measured + unknown == total` with diff 0,
  and a `TOP TIME CONSUMERS` list (top 3 intervals by time, UNKNOWN included
  when it ranks).

## Raw event schema (`events.jsonl`, one JSON per line)

```
task_id, ts (ISO-8601 local, ms), phase, action, file, command, status,
duration_ms, note, source ("marker" | "watch" | "wrap"), accuracy
[event: "TASK_START" | "TASK_END" on the start/end records]
```

Actions: `task_start`, `task_end`, `run_start`, `run_end`, `watch_mod`,
`watch_del`, plus free-form evidence actions (`read_file`, `search_files`,
`edit_file`, ...). Traces recorded before the `event` field was introduced
are still analyzed identically (backward compatible).

## Evidence sources

| Signal | Source | Accuracy |
|---|---|---|
| Session start/end | agent lifecycle log turn start/exit | MEASURED |
| Phase boundary timestamps | CLI invocation clock | MEASURED |
| Wrapped command duration | `run` wall clock | MEASURED |
| File modified (mtime, size) | tree sweep vs snapshot | MEASURED |
| Lines added/removed per file | `git diff --numstat` delta (task start vs end) | MEASURED |
| Rework cycles | heuristic: edit -> failed run/debug -> edit same file | INFERRED |
| Gaps without evidence | idle threshold split | INFERRED |
| Time before first marker | no evidence | UNKNOWN |
| Pre/post gap around a phase without evidence | idle threshold split | UNKNOWN |

## Tests

```
python -m pytest scripts/forensics/tests
```

`scripts/forensics/tests/test_forensics.py` proves with controlled
timestamps (no sleeping) that: TOTAL == MEASURED + UNKNOWN (diff 0), gaps
become UNKNOWN, intervals never overlap, a marker-less task is one UNKNOWN
interval, open tasks close at analysis time, start/end events carry exact
timestamps, sessions open/close at real agent-turn boundaries, a closed
task's report auto-prints when the next turn starts, file writes are
auto-captured as CODE_GENERATION with the file mtime, and old-format traces
still analyze. `conftest.py` adds `scripts/` and `scripts/forensics/` to
`sys.path` (flat sibling imports match `python scripts/forensics/__main__.py`).

## Not measured exactly (by design)

- Time spent thinking/planning inside the agent between two markers — it
  lands in the enclosing phase or an UNKNOWN gap; never fabricated.
- File edits made before the first forensics invocation of a task — they
  predate the session snapshot and land in UNKNOWN (honest).
- Per-file read durations — only access counts and timestamps are recorded.
- Individual test counts inside a `pytest` run — only wrapped run counts,
  pass/fail per run.
- `AI_TOOL_WAIT` / `HUMAN_IDLE` require explicit `mark` events (an agent can
  emit `--phase AI_TOOL_WAIT --action ai_wait` around a model call).

## Layout

```
scripts/forensics/
  __main__.py   CLI (mark, run, sweep, report, status, aggregate, list)
  recorder.py   auto session management + command handlers + wrapped runs
  agent.py      agent lifecycle log parsing (turn start/end)
  watcher.py    sweep-based file modification detection
  intervals.py  non-overlapping interval partition (no double counting)
  analysis.py   metrics, rework cycles, repeated access, aggregate
  report.py     report rendering
  store.py      JSONL store, tree scan, git numstat
  tests/        pytest suite (synthetic traces, partition invariants)
.forensics/     trace data (gitignored)
```
