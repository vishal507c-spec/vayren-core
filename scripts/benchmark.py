"""AEOS benchmark harness — stdlib only.

Subcommands:
  gate        time each validation-gate step N times (sample 1 = cold)
  begin       mark task start (wall-clock); `record` computes wall_s from it
  record      append one task-execution sample to the runs log
  record-action  append one bracket-timed action to the runs log
  impact      print the impact-first validation plan for changed files
  replay      re-run a recorded task's validation commands (no reset, no edits)
  phases      backfill agent-work breakdown for an already-recorded task
  scoreboard  regenerate scripts/benchmark_scoreboard.md from the runs log

Runs log: scripts/benchmark_runs.jsonl (append-only evidence, never rewrite).
Unmeasurable metrics are stored as null and rendered as NOT MEASURED.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_LOG = ROOT / "scripts" / "benchmark_runs.jsonl"
SCOREBOARD = ROOT / "scripts" / "benchmark_scoreboard.md"

GATE_STEPS: tuple[tuple[str, list[str]], ...] = (
    ("ruff-check", ["ruff", "check", "."]),
    ("ruff-format-check", ["ruff", "format", "--check", "."]),
    ("pyright", ["pyright"]),
    ("pytest", [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]),
    ("validate-structure", [sys.executable, "scripts/validate_structure.py"]),
    ("validate-imports", [sys.executable, "scripts/validate_imports.py"]),
)


def _env_info() -> dict[str, str]:
    return {"os": platform.system(), "python": platform.python_version()}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    rank = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[rank]


def _stats(samples: list[float]) -> dict[str, float]:
    return {
        "n": len(samples),
        "median_s": statistics.median(samples),
        "p95_s": _p95(samples),
        "min_s": min(samples),
        "max_s": max(samples),
        "cold_s": samples[0],
    }


def _run_step(cmd: list[str]) -> tuple[float, int, str]:
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    return elapsed, proc.returncode, proc.stdout + proc.stderr


def _pytest_summary(output: str) -> tuple[int | None, int | None]:
    """Extract (passed, failed+errors) from a pytest summary line, if present."""
    passed = failed = None
    for line in output.splitlines():
        if "passed" in line and ("==" in line or " passed" in line):
            m = re.search(r"(\d+) passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+) failed", line)
            failed = int(m.group(1)) if m else 0
            m = re.search(r"(\d+) error", line)
            if m:
                failed = (failed or 0) + int(m.group(1))
    return passed, failed


def cmd_gate(samples: int) -> int:
    print(f"# gate benchmark — {samples} sample(s), sample 1 = cold")
    records: list[dict] = []
    for name, cmd in GATE_STEPS:
        timings: list[float] = []
        ok = True
        passed_total: int | None = None
        failed_total: int | None = None
        for _ in range(samples):
            elapsed, rc, output = _run_step(cmd)
            timings.append(elapsed)
            if name == "pytest":
                # Known pre-existing flake: the Qt suite can abort at teardown
                # (Windows abort, e.g. rc=-1073740791) AFTER all tests passed.
                # Report that distinctly — never as a pass, never as a test
                # failure. See development log for the clean-HEAD proof.
                passed, failed = _pytest_summary(output)
                if passed is not None:
                    passed_total = passed
                if failed:
                    failed_total = (failed_total or 0) + failed
                    ok = False
                elif rc != 0:
                    ok = False  # all green per summary, process aborted after
            else:
                ok = ok and rc == 0
        status = "pass" if ok else "fail"
        if name == "pytest" and not ok and (failed_total or 0) == 0 and passed_total:
            status = f"flake-teardown ({passed_total} passed, rc!=0)"
        rec = {
            "kind": "gate",
            "ts": _now_iso(),
            "env": _env_info(),
            "step": name,
            "samples_s": [round(t, 3) for t in timings],
            "status": status,
            "tests_passed": passed_total,
            "tests_failed": failed_total,
            **{k: round(v, 3) if isinstance(v, float) else v for k, v in _stats(timings).items()},
        }
        records.append(rec)
        s = rec
        print(
            f"{name:22s} n={s['n']} median={s['median_s']:.2f}s "
            f"p95={s['p95_s']:.2f}s min={s['min_s']:.2f}s max={s['max_s']:.2f}s "
            f"cold={s['cold_s']:.2f}s [{s['status']}]"
        )
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print(f"appended {len(records)} records to {RUNS_LOG.name}")
    return 0


def _git_revision() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.stdout.strip() or None
    except Exception:
        return None


def _git_numstat() -> tuple[int | None, int | None, int | None]:
    try:
        proc = subprocess.run(
            ["git", "diff", "HEAD", "--numstat"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        files = added = removed = 0
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            files += 1
            try:
                added += int(parts[0])
                removed += int(parts[1])
            except ValueError:
                continue
        return files, added, removed
    except Exception:
        return None, None, None


# ── impact-first validation plans ─────────────────────────────
# Domain reverse-dependency map: a change in key requires re-running the
# tests of every listed domain (own + consumers), cheapest safe superset.
REVERSE_DEPS: dict[str, tuple[str, ...]] = {
    "core": ("core", "data", "market", "chart", "strategy", "backtest", "risk", "execution", "app"),
    "data": ("data", "app"),
    "market": ("market", "chart", "strategy", "backtest", "execution", "app"),
    "chart": ("chart", "app"),
    "strategy": ("strategy", "backtest", "execution", "app"),
    "backtest": ("backtest", "app"),
    "risk": ("risk", "execution", "app"),
    "execution": ("execution", "app"),
    "app": ("app",),
}

DOMAIN_TESTS: dict[str, str] = {
    "core": "01_core/core/tests",
    "data": "02_data/data/tests",
    "market": "03_market/market/tests",
    "chart": "04_chart/chart/tests",
    "strategy": "05_strategy/strategy/tests 05_strategy/strategy/research/tests",
    "backtest": "06_backtest/backtest/tests",
    "risk": "07_risk/risk/tests",
    "execution": "08_execution/execution/tests",
    "app": "00_app/app/tests",
}

# Path fragments whose change may affect consumers beyond the own domain.
PUBLIC_FRAGMENTS = ("__init__.py", "/events/", "/manifest.py", "/models/")

# Files whose change always escalates to the full gate.
FULL_GATE_FILES = ("pyproject.toml", "conftest.py", "Makefile")


def _domain_of(path: str) -> str | None:
    part = path.replace("\\", "/").split("/")
    if len(part) >= 2 and part[1] in REVERSE_DEPS:
        return part[1]
    return None


def _changed_files(explicit: list[str] | None) -> list[str]:
    if explicit:
        return explicit
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    files: list[str] = []
    for line in proc.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:  # renamed: take the new path
            path = path.split(" -> ")[-1].strip()
        if path:
            files.append(path)
    return sorted(files)


def impact_plan(files: list[str]) -> dict:
    """Compute the cheapest safe validation plan for changed files.

    Levels: 0 docs-only · 1 test-only · 2 single-domain src ·
    3 public-surface/multi-domain · 4 full gate required.
    """
    if not files:
        return {
            "level": 0,
            "reason": "no changes",
            "pytest": [],
            "pyright": "none",
            "full_gate": False,
        }
    flat = [f.replace("\\", "/") for f in files]
    if any(f.endswith(FULL_GATE_FILES) for f in flat):
        return {
            "level": 4,
            "reason": "config/build/test-runner change affects everything",
            "pytest": ["full suite"],
            "pyright": "full",
            "full_gate": True,
        }
    if all(f.endswith(".md") for f in flat):
        return {
            "level": 0,
            "reason": "docs only",
            "pytest": [],
            "pyright": "none",
            "full_gate": False,
        }
    if all(f.startswith("scripts/") for f in flat):
        return {
            "level": 3,
            "reason": "tooling change (validators/harness/runners)",
            "pytest": ["scripts/forensics/tests", "scripts/tests"],
            "pyright": "full",
            "full_gate": False,
            "extra_cmds": [
                "python scripts/validate_structure.py",
                "python scripts/validate_imports.py",
            ],
        }
    domains = {d for f in flat if (d := _domain_of(f)) is not None}
    if not domains:
        return {
            "level": 4,
            "reason": "unrecognized paths, conservatively full gate",
            "pytest": ["full suite"],
            "pyright": "full",
            "full_gate": True,
        }
    if all("/tests/" in f for f in flat):
        return {
            "level": 1,
            "reason": "test files only",
            "pytest": sorted(set(flat)),
            "pyright": "files",
            "full_gate": False,
        }
    public = any(frag in f for f in flat for frag in PUBLIC_FRAGMENTS if "/tests/" not in f)
    needed: set[str] = set()
    for d in domains:
        needed.update(REVERSE_DEPS[d])
    pytest_paths: list[str] = []
    for d in (
        "core",
        "data",
        "market",
        "chart",
        "strategy",
        "backtest",
        "risk",
        "execution",
        "app",
    ):
        if d in needed:
            pytest_paths.extend(DOMAIN_TESTS[d].split())
    if any(f.startswith("scripts/") for f in flat):
        pytest_paths.append("scripts/forensics/tests scripts/tests")
    if public or len(domains) > 1:
        return {
            "level": 3,
            "reason": "public-surface or multi-domain change",
            "pytest": pytest_paths,
            "pyright": "full",
            "full_gate": False,
        }
    return {
        "level": 2,
        "reason": f"single-domain src change ({sorted(domains)[0]})",
        "pytest": pytest_paths,
        "pyright": "files",
        "full_gate": False,
    }


def cmd_impact(args: argparse.Namespace) -> int:
    files = _changed_files(args.files)
    plan = impact_plan(files)
    print(
        f"# impact plan for {len(files)} changed file(s) — level {plan['level']}: {plan['reason']}"
    )
    for f in files:
        print(f"  changed: {f}")
    if plan["pytest"]:
        print(f"pytest {' '.join(plan['pytest'])}")
    print(f"pyright: {plan['pyright']}")
    for cmd in plan.get("extra_cmds", []):
        print(f"also: {cmd}")
    print(f"full gate required: {plan['full_gate']}")
    return 0


def cmd_begin(args: argparse.Namespace) -> int:
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "task-start", "ts": _now_iso(), "task": args.task}) + "\n")
    print(f"task {args.task} started (wall-clock runs until record)")
    return 0


def _last_task_start(task: str) -> str | None:
    if not RUNS_LOG.is_file():
        return None
    last: str | None = None
    for line in RUNS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "task-start" and rec.get("task") == task:
            last = rec["ts"]
    return last


def _wall_since(ts_iso: str) -> float | None:
    try:
        start = datetime.datetime.fromisoformat(ts_iso)
        now = datetime.datetime.now(datetime.UTC)
        return round((now - start).total_seconds(), 1)
    except ValueError:
        return None


def cmd_record(args: argparse.Namespace) -> int:
    auto_files, auto_added, auto_removed = _git_numstat()
    wall = args.wall_s
    if wall is None:
        started = _last_task_start(args.task)
        wall = _wall_since(started) if started else None
    rec = {
        "kind": "task",
        "ts": _now_iso(),
        "env": _env_info(),
        "task": args.task,
        "task_class": args.task_class,
        "status": args.status,
        "revision": args.revision or _git_revision(),
        "wall_s": wall,
        "agent_active_s": args.agent_active_s,
        "validation_s": args.validation_s,
        "validation_mode": args.validation_mode,
        "validation_cmds": args.validation_cmd,
        "tool_calls": args.tool_calls,
        "human_interventions": args.human,
        "first_pass": args.first_pass,
        "files_changed": args.files if args.files is not None else auto_files,
        "lines_added": args.lines_added if args.lines_added is not None else auto_added,
        "lines_removed": args.lines_removed if args.lines_removed is not None else auto_removed,
        "tests_run": args.tests_run,
        "failures": args.failures,
        "repairs": args.repairs,
        "notes": args.notes,
        **_phase_ops_dict(args),
    }
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"recorded {args.task} -> {RUNS_LOG.name} (wall={wall})")
    return 0


def _phase_ops_dict(args: argparse.Namespace) -> dict:
    return {
        "phases": {
            "understand_s": args.understand_s,
            "search_s": args.search_s,
            "plan_s": args.plan_s,
            "implement_s": args.implement_s,
            "debug_s": args.debug_s,
            "verify_s": args.verify_s,
            "unknown_s": args.unknown_s,
        },
        "ops": {
            "reads": args.reads,
            "searches": args.searches,
            "edits": args.edits,
            "validation_runs": args.validation_runs,
            "debug_runs": args.debug_runs,
            "plan_ops": args.plan_ops,
        },
        "method": args.method,
    }


def cmd_phases(args: argparse.Namespace) -> int:
    """Append agent-work breakdown for an already-recorded task (backfill).

    The runs log is append-only: task records are never rewritten. A phases
    record links by task id; the scoreboard merges it into the task row.
    """
    rec = {"kind": "phases", "ts": _now_iso(), "task": args.task, **_phase_ops_dict(args)}
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"recorded phases for {args.task} -> {RUNS_LOG.name}")
    return 0


def cmd_record_action(args: argparse.Namespace) -> int:
    """Append one bracket-timed action (kind:action) to the runs log."""
    rec = {
        "kind": "action",
        "ts": _now_iso(),
        "env": _env_info(),
        "task": args.task,
        "phase": args.phase,
        "tool": args.tool,
        "duration_ms": args.duration_ms,
        "success": args.success == "true",
        "note": args.note,
    }
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"action {args.task}/{args.phase}/{args.tool}: {args.duration_ms}ms ok={args.success}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-run a recorded task's validation commands (no reset, no edits).

    Proves the VALIDATION side is reproducible. Implementation replay is the
    agent re-executing the corpus spec — deliberately not scripted, because
    RESET (discarding worktree state) is destructive.
    """
    if not RUNS_LOG.is_file():
        print("no runs log")
        return 1
    target: dict | None = None
    for line in RUNS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "task" and rec.get("task") == args.task:
            target = rec
    if target is None or not target.get("validation_cmds"):
        print(f"no recorded validation commands for {args.task}")
        return 1
    timings: list[float] = []
    ok = True
    for cmd in target["validation_cmds"]:
        elapsed, rc, _ = _run_step(cmd.split())
        timings.append(round(elapsed, 2))
        ok = ok and rc == 0
        print(f"  [{('pass' if rc == 0 else 'FAIL')}] {cmd} ({elapsed:.1f}s)")
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "kind": "replay",
                    "ts": _now_iso(),
                    "env": _env_info(),
                    "task": args.task,
                    "revision": _git_revision(),
                    "validation_cmds": target["validation_cmds"],
                    "validation_s": round(sum(timings), 1),
                    "status": "pass" if ok else "fail",
                }
            )
            + "\n"
        )
    print(f"replay {args.task}: {'pass' if ok else 'FAIL'} in {sum(timings):.1f}s")
    return 0 if ok else 1


def _cell(value: object, suffix: str = "") -> str:
    if value is None:
        return "NOT MEASURED"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def _speed_section() -> list[str]:
    """Phase-18 speed dashboard, rendered by the speed package in isolation.

    Subprocess isolation keeps this harness decoupled from the speed
    modules (no shared imports, no behavior change to existing sections).
    """
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "speed" / "__main__.py"),
                "dashboard",
                "--format",
                "md",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        return ["", "## Speed dashboard", "", f"NOT MEASURED (dashboard error: {exc}).", ""]
    if proc.returncode != 0 or not proc.stdout.strip():
        return ["", "## Speed dashboard", "", "NOT MEASURED (no speed records yet).", ""]
    return [""] + proc.stdout.strip().splitlines() + [""]


def cmd_scoreboard() -> int:
    gates: dict[str, dict] = {}
    tasks: list[dict] = []
    replays: dict[str, list[dict]] = {}
    phase_backfill: dict[str, dict] = {}
    actions: list[dict] = []
    if RUNS_LOG.is_file():
        for line in RUNS_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("kind") == "gate":
                gates[rec["step"]] = rec  # latest wins
            elif rec.get("kind") == "task":
                tasks.append(rec)
            elif rec.get("kind") == "replay":
                replays.setdefault(rec.get("task", "?"), []).append(rec)
            elif rec.get("kind") == "phases":
                phase_backfill[rec.get("task", "?")] = rec  # latest wins
            elif rec.get("kind") == "action":
                actions.append(rec)
    out = [
        "# AEOS Velocity Scoreboard — measured evidence only",
        "",
        "> Generated by `python scripts/benchmark.py scoreboard` from",
        "> `scripts/benchmark_runs.jsonl`. Never hand-edit numbers here —",
        "> re-run the harness instead. Unmeasurable = NOT MEASURED.",
        "",
        "## Validation-gate cost (latest run per step)",
        "",
        "| step | n | median | p95 | min | max | cold | status | tests | measured_at |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, _ in GATE_STEPS:
        rec = gates.get(name)
        if rec is None:
            out.append(f"| {name} | NOT MEASURED | — | — | — | — | — | — | — | — |")
            continue
        passed = rec.get("tests_passed")
        failed = rec.get("tests_failed")
        tests = (
            "—" if passed is None else f"{passed} passed" + (f", {failed} failed" if failed else "")
        )
        out.append(
            f"| {name} | {rec['n']} | {_cell(rec.get('median_s'), 's')} | "
            f"{_cell(rec.get('p95_s'), 's')} | {_cell(rec.get('min_s'), 's')} | "
            f"{_cell(rec.get('max_s'), 's')} | {_cell(rec.get('cold_s'), 's')} | "
            f"{rec.get('status')} | {tests} | {rec.get('ts')} |"
        )
    out += [
        "",
        "## Task samples",
        "",
        "| task | class | wall | validation | files/lines | tests | failures | "
        "repairs | success | notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rec in tasks:
        files = rec.get("files_changed")
        lines = rec.get("lines_added")
        lines_r = rec.get("lines_removed")
        change = "NOT MEASURED" if files is None else f"{files}f +{lines}/-{lines_r}"
        out.append(
            f"| {rec.get('task')} | {rec.get('task_class')} | {_cell(rec.get('wall_s'), 's')} | "
            f"{_cell(rec.get('validation_s'), 's')} | {change} | {_cell(rec.get('tests_run'))} | "
            f"{_cell(rec.get('failures'))} | {_cell(rec.get('repairs'))} | {rec.get('status')} | "
            f"{rec.get('notes') or ''} |"
        )
    out += [
        "",
        "## Agent-work breakdown (phase durations + operation counts)",
        "",
        "Durations measured only where clocked (validation runs); all other",
        "phases are NOT MEASURED unless a sample records them. Operation",
        "counts are transcribed from the execution record (see method).",
        "UNKNOWN = wall − measured parts (thinking, tool roundtrips, reads).",
        "",
        "| task | total | understand | search | plan | implement | debug | "
        "validate | verify | unknown | ops R/S/E/V/D/P |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def _op(ops: dict, *keys: str) -> str:
        vals = [ops.get(k) for k in keys]
        if all(v is None for v in vals):
            return "?"
        return "/".join(str(v) if v is not None else "?" for v in vals)

    for rec in tasks:
        backfill = phase_backfill.get(rec.get("task", ""))
        if backfill is not None:
            ph = backfill.get("phases") or {}
            ops = backfill.get("ops") or {}
        else:
            ph = rec.get("phases") or {}
            ops = rec.get("ops") or {}
        total = rec.get("wall_s")
        unknown = ph.get("unknown_s")
        if unknown is None and total is not None and rec.get("validation_s") is not None:
            unknown = round(total - rec["validation_s"], 1)
        op_counts = _op(
            ops, "reads", "searches", "edits", "validation_runs", "debug_runs", "plan_ops"
        )
        out.append(
            f"| {rec.get('task')} | {_cell(total, 's')} | {_cell(ph.get('understand_s'), 's')} | "
            f"{_cell(ph.get('search_s'), 's')} | {_cell(ph.get('plan_s'), 's')} | "
            f"{_cell(ph.get('implement_s'), 's')} | {_cell(ph.get('debug_s'), 's')} | "
            f"{_cell(rec.get('validation_s'), 's')} | {_cell(ph.get('verify_s'), 's')} | "
            f"{_cell(unknown, 's')} | {op_counts} |"
        )
    out += [
        "",
        "## Optimizations (BASELINE vs CURRENT)",
        "",
        "Baseline = full-gate pytest median. Current = median validation_s of",
        "recorded task samples that used impact-first validation. Operational",
        "speedup (validation time eliminated per cycle) is separate from",
        "end-to-end task speedup (needs wall-clock BEFORE/AFTER per task).",
        "",
    ]
    baseline_pytest = gates.get("pytest", {}).get("median_s")
    impact_times = [
        r["validation_s"]
        for r in tasks
        if r.get("validation_mode") == "impact" and r.get("validation_s") is not None
    ]
    full_times = [
        r["validation_s"]
        for r in tasks
        if r.get("validation_mode") == "full" and r.get("validation_s") is not None
    ]
    if baseline_pytest is not None and impact_times:
        med = statistics.median(impact_times)
        out.append(
            f"- Impact-first validation: baseline full-suite {baseline_pytest:.1f}s vs "
            f"impact median {med:.1f}s over {len(impact_times)} samples = "
            f"{baseline_pytest / med:.1f}x operational speedup per validation cycle."
        )
    else:
        out.append("- Impact-first validation: NOT MEASURED (no impact-mode task samples yet).")
    if full_times:
        med_full = statistics.median(full_times)
        out.append(f"- Full-gate task validations: {len(full_times)} samples,")
        out.append(f"  median {med_full:.1f}s.")
    successes = [r for r in tasks if r.get("status") == "pass"]
    out.append(
        f"- Correctness: {len(successes)}/{len(tasks)} task samples pass; "
        f"total failures={sum(r.get('failures') or 0 for r in tasks)}, "
        f"repairs={sum(r.get('repairs') or 0 for r in tasks)}."
    )
    out += [
        "",
        "## Validation replays (BEFORE → AFTER, same commands)",
        "",
        "BEFORE = task record validation_s (flake era: includes abort-retry cost).",
        "AFTER = median of replay validation_s (current workflow, Qt fix live).",
        "End-to-end task speedup is NOT MEASURED: re-executing completed",
        "implementation work would be contaminated theater (solution known),",
        "so wall-clock BEFORE/AFTER pairs do not exist. Validation speedup —",
        "the realizable, identical-workload comparison — is measured below.",
        "",
        "| task | before_s | after_s | speedup | saved_s | samples | replay_status |",
        "|---|---|---|---|---|---|---|",
    ]
    speedups: list[float] = []
    saved_total = 0.0
    for rec in tasks:
        task_runs = [
            r for r in replays.get(rec.get("task", ""), []) if r.get("validation_s") is not None
        ]
        base = rec.get("validation_s")
        if base is None or not task_runs:
            out.append(f"| {rec.get('task')} | {_cell(base, 's')} | NOT MEASURED | — | — | 0 | — |")
            continue
        cur = statistics.median([r["validation_s"] for r in task_runs])
        speedup = base / cur if cur else 0.0
        speedups.append(speedup)
        saved_total += base - cur
        statuses = sorted({r.get("status", "?") for r in task_runs})
        out.append(
            f"| {rec.get('task')} | {base:.1f}s | {cur:.1f}s | {speedup:.2f}x | "
            f"{base - cur:.1f}s | {len(task_runs)} | {','.join(statuses)} |"
        )
    if speedups:
        med_speedup = statistics.median(speedups)
        mean_speedup = statistics.mean(speedups)
        out += [
            "",
            "## Aggregate (validation speedup only)",
            "",
            f"- Median task speedup: {med_speedup:.2f}x over {len(speedups)} tasks.",
            f"- Mean task speedup: {mean_speedup:.2f}x.",
            f"- Overall validation time saved: {saved_total:.1f}s across replayed tasks.",
            "- End-to-end task speedup: NOT MEASURED (no uncontaminated repeats exist).",
            "- Human interventions: "
            f"{sum(r.get('human_interventions') or 0 for r in tasks)} total across "
            f"{len(tasks)} task samples (BEFORE and CURRENT both 0).",
            "",
        ]
    else:
        out += ["", "- No replay pairs yet — run `benchmark.py replay --task ID`.", ""]
    out += [
        "",
        "## Action timing (bracket-measured upper bounds)",
        "",
        "Each action was bracketed by wall-clock timestamps immediately around",
        "a single tool call. Duration = action roundtrip + adjacent agent",
        "dispatch latency: an UPPER BOUND on the true action cost, never an",
        "estimate of thinking time. Conclusions hold only where robust to that",
        "bias (upper bounds still rule out tiny phases as dominant).",
        "",
        "| phase | n | total | avg | median | p95 | max |",
        "|---|---|---|---|---|---|---|",
    ]
    by_phase: dict[str, list[float]] = {}
    for rec in actions:
        ms = rec.get("duration_ms")
        if isinstance(ms, (int, float)):
            by_phase.setdefault(str(rec.get("phase", "?")), []).append(float(ms) / 1000.0)
    all_actions: list[float] = [s for samples in by_phase.values() for s in samples]
    for phase in sorted(by_phase):
        samples = sorted(by_phase[phase])
        out.append(
            f"| {phase} | {len(samples)} | {sum(samples):.1f}s | "
            f"{statistics.mean(samples):.2f}s | {statistics.median(samples):.2f}s | "
            f"{_p95(samples):.2f}s | {max(samples):.2f}s |"
        )
    if all_actions:
        out.append(
            f"| ALL | {len(all_actions)} | {sum(all_actions):.1f}s | "
            f"{statistics.mean(all_actions):.2f}s | {statistics.median(all_actions):.2f}s | "
            f"{_p95(all_actions):.2f}s | {max(all_actions):.2f}s |"
        )
    else:
        out.append("| (none) | 0 | — | — | — | — | — |")
    out += _speed_section()
    out += [
        "",
        "## Rules",
        "",
        "- BEFORE vs AFTER requires same task, same revision, same validation.",
        "- Operational speedup (operations eliminated) is reported separately",
        "  from end-to-end wall-clock improvement.",
        "- No 10x/50x/100x/1000x claim without measured BEFORE/AFTER samples.",
        "",
    ]
    SCOREBOARD.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {SCOREBOARD.name} ({len(tasks)} task samples, {len(gates)} gate steps)")
    return 0


def _add_phase_args(p: argparse.ArgumentParser) -> None:
    """Phase-4 agent-work breakdown fields (durations null = NOT MEASURED)."""
    p.add_argument("--understand-s", type=float, default=None)
    p.add_argument("--search-s", type=float, default=None)
    p.add_argument("--plan-s", type=float, default=None)
    p.add_argument("--implement-s", type=float, default=None)
    p.add_argument("--debug-s", type=float, default=None)
    p.add_argument("--verify-s", type=float, default=None)
    p.add_argument("--unknown-s", type=float, default=None)
    p.add_argument("--reads", type=int, default=None)
    p.add_argument("--searches", type=int, default=None)
    p.add_argument("--edits", type=int, default=None)
    p.add_argument("--validation-runs", type=int, default=None)
    p.add_argument("--debug-runs", type=int, default=None)
    p.add_argument("--plan-ops", type=int, default=None)
    p.add_argument("--method", default=None)


def main() -> int:
    parser = argparse.ArgumentParser(description="AEOS benchmark harness (stdlib only)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_gate = sub.add_parser("gate", help="time each validation-gate step")
    p_gate.add_argument("--samples", type=int, default=3)
    p_begin = sub.add_parser("begin", help="mark task start for wall-clock timing")
    p_begin.add_argument("--task", required=True)
    p_rec = sub.add_parser("record", help="append one task-execution sample")
    p_rec.add_argument("--task", required=True)
    p_rec.add_argument("--class", dest="task_class", required=True)
    p_rec.add_argument("--status", choices=("pass", "fail"), required=True)
    p_rec.add_argument("--revision", default=None)
    p_rec.add_argument("--wall-s", type=float, default=None)
    p_rec.add_argument("--agent-active-s", type=float, default=None)
    p_rec.add_argument("--validation-s", type=float, default=None)
    p_rec.add_argument("--validation-mode", choices=("impact", "full"), default=None)
    p_rec.add_argument("--validation-cmd", action="append", default=None)
    p_rec.add_argument("--tool-calls", type=int, default=None)
    p_rec.add_argument("--human", type=int, default=None)
    p_rec.add_argument("--first-pass", choices=("true", "false"), default=None)
    p_rec.add_argument("--files", type=int, default=None)
    p_rec.add_argument("--lines-added", type=int, default=None)
    p_rec.add_argument("--lines-removed", type=int, default=None)
    p_rec.add_argument("--tests-run", type=int, default=None)
    p_rec.add_argument("--failures", type=int, default=None)
    p_rec.add_argument("--repairs", type=int, default=None)
    p_rec.add_argument("--notes", default=None)
    _add_phase_args(p_rec)
    p_impact = sub.add_parser("impact", help="print impact-first validation plan")
    p_impact.add_argument("--files", nargs="*", default=None)
    p_replay = sub.add_parser("replay", help="re-run a recorded task's validation")
    p_replay.add_argument("--task", required=True)
    p_act = sub.add_parser("record-action", help="append one bracket-timed action")
    p_act.add_argument("--task", required=True)
    p_act.add_argument("--phase", required=True)
    p_act.add_argument("--tool", required=True)
    p_act.add_argument("--duration-ms", type=int, required=True)
    p_act.add_argument("--success", choices=("true", "false"), default="true")
    p_act.add_argument("--note", default="")
    p_phases = sub.add_parser("phases", help="backfill agent-work breakdown for a task")
    p_phases.add_argument("--task", required=True)
    _add_phase_args(p_phases)
    sub.add_parser("scoreboard", help="regenerate the scoreboard md from the log")
    args = parser.parse_args()
    if args.cmd == "gate":
        return cmd_gate(max(1, args.samples))
    if args.cmd == "begin":
        return cmd_begin(args)
    if args.cmd == "record":
        return cmd_record(args)
    if args.cmd == "impact":
        return cmd_impact(args)
    if args.cmd == "replay":
        return cmd_replay(args)
    if args.cmd == "record-action":
        return cmd_record_action(args)
    if args.cmd == "phases":
        return cmd_phases(args)
    return cmd_scoreboard()


if __name__ == "__main__":
    raise SystemExit(main())
