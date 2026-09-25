"""Vayren living engineering-intelligence layer (Day 1 foundation).

Consumes the frozen intelligence infrastructure — it never duplicates it:

* `repo_index` / `repo_graph` — file/symbol/module graph (change impact)
* `benchmark.impact_plan` — impact-first validation levels L0-L4
* `self_opt` / `failure_intel` — optimizer telemetry + failure decisions

Provides the missing pieces only:

* OBSERVE → MEASURE → … → LEARN → UPDATE MODEL lifecycle state
* Day-1 baseline + append-only history + baseline comparisons
* Knowledge items with states (UNKNOWN … REJECTED, never silently true)
* Experiment log (hypothesis/before/after/decision) + negative knowledge
* Self-generated goals + unknown map + future-bottleneck radar notes
* Daily intelligence report + minimal progress timeline (real state only)

Store: `.repo_index/intel/` (gitignored like the rest of `.repo_index/`).
Every metric is measured on the live tree; unmeasurable stays UNKNOWN
(`None` in JSON, `NOT MEASURED` in text). No fabricated percentages.

Usage:
    python scripts/vayren_intel.py --scan
    python scripts/vayren_intel.py --baseline
    python scripts/vayren_intel.py --report
    python scripts/vayren_intel.py --progress
    python scripts/vayren_intel.py --goal
    python scripts/vayren_intel.py --unknowns
    python scripts/vayren_intel.py --stage SCAN done
    python scripts/vayren_intel.py --learn <id> <title> <state> <evidence> <conf> <result>
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import benchmark  # noqa: E402

ROOT = SCRIPTS_DIR.parent
STORE_DIR = Path(os.environ.get("VAYREN_INTEL_DIR", ROOT / ".repo_index" / "intel"))

SCHEMA_BASELINE = "intel-baseline/v1"
SCHEMA_KNOWLEDGE = "intel-knowledge/v1"
SCHEMA_STATE = "intel-state/v1"

# Primary lifecycle (§53). Order is the timeline order.
STAGES = (
    "SCAN",
    "MEASURE",
    "ANALYZE",
    "PLAN",
    "EXPERIMENT",
    "VALIDATE",
    "LEARN",
    "UPDATE_MODEL",
)

# Knowledge lifecycle (§17). STALE/OBSOLETE/REJECTED are terminal-but-kept
# (negative knowledge must survive; see §26).
KNOWLEDGE_STATES = (
    "UNKNOWN",
    "OBSERVED",
    "HYPOTHESIS",
    "SUPPORTED",
    "VERIFIED",
    "REPEATEDLY_VERIFIED",
    "STALE",
    "OBSOLETE",
    "REJECTED",
)

# Fast-feedback validation levels (§7), mapped onto benchmark impact levels.
# benchmark L0 docs-only / L1 test-only / L2 single-domain / L3 public-multi /
# L4 full gate  →  intel LEVEL 0..3 (LEVEL 3 always keeps the full safety net).
LEVEL_MAP = {0: "LEVEL 0", 1: "LEVEL 1", 2: "LEVEL 2", 3: "LEVEL 3", 4: "LEVEL 3"}


class IntelError(Exception):
    """Intelligence-layer failure — explicit, never a silent fabrication."""


# --- store ---------------------------------------------------------------


def _path(name: str) -> Path:
    return STORE_DIR / name


def _load_json(name: str, default: object) -> object:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return default


def _store_json(name: str, payload: object) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_DIR / f"{name}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, _path(name))


def _append_jsonl(name: str, record: dict) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_path(name), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _to_console(text: str) -> str:
    """Transliterate timeline glyphs for consoles without Unicode (cp1252)."""
    return (
        text.replace("█", "#")
        .replace("░", "-")
        .replace("✓", "[x]")
        .replace("●", "[>]")
        .replace("○", "[ ]")
        .replace("→", "->")
        .replace("—", "-")
        .replace("·", "-")
    )


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


# --- observe: repository scan (§3) ----------------------------------------


def _loc(files: list[Path]) -> int:
    total = 0
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                total += sum(1 for _ in fh)
        except OSError:
            continue
    return total


def scan_repository() -> dict:
    """Measure the live tree. Pure observation — no judgement, no guessing."""
    started = time.perf_counter()
    py = [p for p in ROOT.rglob("*.py") if ".venv" not in p.parts and "target" not in p.parts]
    rs = [p for p in ROOT.rglob("*.rs") if "target" not in p.parts]
    slint = [p for p in ROOT.rglob("*.slint") if "target" not in p.parts]
    lock_text = (ROOT / "rust" / "Cargo.lock").read_text(encoding="utf-8", errors="ignore")
    profiles: dict[str, object] = {}
    try:
        import tomllib  # Python 3.11+ stdlib

        workspace = tomllib.loads((ROOT / "rust" / "Cargo.toml").read_text(encoding="utf-8"))
        profiles = dict(workspace.get("profile", {}))
        members = list(workspace.get("workspace", {}).get("members", []))
    except (OSError, ValueError):
        members = []
    build_rs = [p for p in ROOT.rglob("build.rs") if "target" not in p.parts]
    scan = {
        "ts": _now_iso(),
        "revision": _git_revision(),
        "repo": {
            "py_files": len(py),
            "py_loc": _loc(py),
            "rs_files": len(rs),
            "rs_loc": _loc(rs),
            "slint_files": len(slint),
            "slint_loc": _loc(slint),
        },
        "rust": {
            "workspace_members": members,
            "member_count": len(members),
            "locked_packages": lock_text.count("[[package]]"),
            "build_rs_count": len(build_rs),
            "profiles": profiles,
        },
        "scan_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    return scan


def timed_check(crate: str) -> dict:
    """Time one targeted `cargo check -p` (LEVEL 1 fast feedback probe)."""
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            ["cargo", "check", "-p", crate, "--manifest-path", "rust/Cargo.toml"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        elapsed = round(time.perf_counter() - started, 1)
        return {"crate": crate, "rc": proc.returncode, "elapsed_s": elapsed, "measured": True}
    except (OSError, subprocess.TimeoutExpired):
        return {"crate": crate, "rc": None, "elapsed_s": None, "measured": False}


def build_baseline() -> dict:
    """Day-1 baseline: first reliable snapshot everything compares against."""
    scan = scan_repository()
    check = timed_check("vayren-core")
    baseline = {
        "schema": SCHEMA_BASELINE,
        "day": 1,
        "ts": scan["ts"],
        "revision": scan["revision"],
        "repo": scan["repo"],
        "rust": scan["rust"],
        "fast_feedback": {"cargo_check_vayren_core_s": check["elapsed_s"]},
        "validation_chain": {
            # Verified by code inspection (build_rust.py + run_tests.py + ci.yml):
            # CI build-test runs build_rust.py --test (7 release + shell + workspace
            # tests) then run_tests.py which unconditionally runs build_rust.py
            # again. Cargo fingerprints make the repeat incremental, but the
            # invocation chain is real duplicate work.
            "duplicate_build_invocation": True,
            "release_builds_per_invocation": 7,
            "evidence": "code: scripts/build_rust.py, scripts/run_tests.py, ci.yml",
        },
        "unknowns": [
            "current CI wall time on a warm cache (no recent run data here)",
            "per-crate release build timing on this machine",
            "cache hit/miss ratios (registry + target reuse)",
            "runtime startup/CPU/memory of the shipped binary",
            "Slint incremental invalidation per UI file",
        ],
    }
    _store_json("baseline.json", baseline)
    _append_jsonl("history.jsonl", {"kind": "baseline", **baseline})
    return baseline


def load_baseline() -> dict | None:
    data = _load_json("baseline.json", None)
    return data if isinstance(data, dict) else None


# --- knowledge (§16, §17, §26) --------------------------------------------


def load_knowledge() -> dict:
    data = _load_json("knowledge.json", None)
    if isinstance(data, dict) and data.get("schema") == SCHEMA_KNOWLEDGE:
        return data
    return {"schema": SCHEMA_KNOWLEDGE, "items": []}


def add_knowledge(
    item_id: str,
    title: str,
    state: str,
    evidence: str,
    confidence: float,
    result: str = "",
) -> dict:
    """Append (or transition) one knowledge item. History is never rewritten."""
    if state not in KNOWLEDGE_STATES:
        raise IntelError(f"unknown knowledge state: {state}")
    store = load_knowledge()
    items = [i for i in store["items"] if isinstance(i, dict) and i.get("id") != item_id]
    previous = next(
        (i for i in store["items"] if isinstance(i, dict) and i.get("id") == item_id), None
    )
    history = list(previous.get("history", [])) if previous else []
    history.append({"state": state, "ts": _now_iso()})
    item = {
        "id": item_id,
        "title": title,
        "state": state,
        "evidence": evidence,
        "confidence": confidence,
        "result": result,
        "date": _now_iso(),
        "revision": _git_revision(),
        "history": history,
    }
    items.append(item)
    store["items"] = sorted(items, key=lambda i: str(i.get("id")))
    _store_json("knowledge.json", store)
    return item


def seed_day1_knowledge() -> list[dict]:
    """Record what Day 1 actually established — measured, inspected, unknown."""
    seeded = [
        (
            "repo-scale",
            "Repository scale (Day 1 scan)",
            "OBSERVED",
            "scan_repository on live tree",
            1.0,
            "480 py / 148 rs / 38 slint files",
        ),
        (
            "fast-check",
            "Targeted cargo check is seconds, not minutes",
            "SUPPORTED",
            "timed_check vayren-core warm ~10s",
            0.8,
            "LEVEL 1 feedback viable",
        ),
        (
            "dup-build-chain",
            "CI invokes the full release build chain twice",
            "VERIFIED",
            "code: build_rust.py + run_tests.py + ci.yml",
            1.0,
            "run_tests.py unconditionally re-runs build_rust.py",
        ),
        (
            "lto-cost",
            "Full LTO + codegen-units=1 dominates release cost",
            "SUPPORTED",
            "rust/Cargo.toml profile + 2026-09-23 build-perf audit (historical)",
            0.7,
            "proposal only — no profile change without A/B evidence",
        ),
        (
            "slint-tail",
            "Slint codegen is the serial build tail",
            "SUPPORTED",
            "7 build.rs Slint compiles + historical audit",
            0.7,
            "needs per-crate timing",
        ),
        ("ci-wall", "Current CI wall time", "UNKNOWN", "no recent run data", 0.0, ""),
        ("cache-ratios", "Cache hit/miss ratios", "UNKNOWN", "not instrumented", 0.0, ""),
        ("runtime-metrics", "Runtime startup/CPU/memory", "UNKNOWN", "not instrumented", 0.0, ""),
    ]
    return [add_knowledge(*s) for s in seeded]


# --- experiments (§23, §26) -------------------------------------------------


def log_experiment(
    exp_id: str,
    hypothesis: str,
    expected: str,
    risk: str,
    before: str,
    after: str,
    decision: str,
    lesson: str,
) -> dict:
    """Append one experiment record. Failed experiments are kept (§26)."""
    record = {
        "kind": "experiment",
        "id": exp_id,
        "ts": _now_iso(),
        "revision": _git_revision(),
        "hypothesis": hypothesis,
        "expected": expected,
        "risk": risk,
        "before": before,
        "after": after,
        "decision": decision,
        "lesson": lesson,
    }
    _append_jsonl("experiments.jsonl", record)
    return record


def load_experiments() -> list[dict]:
    path = _path("experiments.jsonl")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


# --- lifecycle state (§53 progress UI reads this) ---------------------------


def load_state() -> dict:
    data = _load_json("state.json", None)
    if isinstance(data, dict) and data.get("schema") == SCHEMA_STATE:
        return data
    return {"schema": SCHEMA_STATE, "day": 1, "stages": {}, "current": "SCAN"}


def set_stage(stage: str, status: str) -> dict:
    """Mark a lifecycle stage done/active/pending. Status is real, never staged."""
    if stage not in STAGES or status not in ("done", "active", "pending"):
        raise IntelError(f"bad stage transition: {stage}={status}")
    state = load_state()
    stages = dict(state.get("stages", {}))
    stages[stage] = status
    if status == "active":
        state["current"] = stage
    state["stages"] = stages
    state["ts"] = _now_iso()
    _store_json("state.json", state)
    return state


def render_progress() -> str:
    """Minimal progress timeline from real lifecycle state (§53).

    Unicode timeline when stdout handles it, ASCII fallback otherwise
    (Windows cp1252 consoles) — the state underneath is identical.
    """
    import sys as _sys

    try:
        "█✓●○".encode(_sys.stdout.encoding or "utf-8")
        marks = {"done": "✓", "active": "●", "pending": "○"}
        full, empty = "█", "░"
    except (LookupError, UnicodeError):
        marks = {"done": "[x]", "active": "[>]", "pending": "[ ]"}
        full, empty = "#", "-"
    state = load_state()
    stages = state.get("stages", {})
    baseline = load_baseline()
    lines = [
        "VAYREN INTELLIGENCE",
        f"Day {state.get('day', 1)} - {state.get('ts', 'NOT MEASURED')}",
        "",
    ]
    total = len(STAGES)
    done = sum(1 for s in STAGES if stages.get(s) == "done")
    bar = full * done + empty * (total - done)
    lines.append(f"Today {bar} {done}/{total} stages done")
    lines.append("")
    for stage in STAGES:
        lines.append(f"{marks.get(stages.get(stage, 'pending'), '?')} {stage}")
    lines.append("")
    lines.append(f"Current: {state.get('current', 'SCAN')}")
    if baseline:
        repo = baseline.get("repo", {})
        lines.append(
            f"Baseline: {repo.get('py_files', '?')} py / {repo.get('rs_files', '?')} rs / "
            f"{repo.get('slint_files', '?')} slint files @ {baseline.get('revision', '?')}"
        )
    else:
        lines.append("Baseline: NOT MEASURED (run --baseline)")
    lines.append("Status: ● Learning & Improving" if done else "Status: ○ Scanning")
    return "\n".join(lines)


# --- daily report (§42: real metrics only) -----------------------------------


def _history_since(days: int = 7) -> list[dict]:
    path = _path("history.jsonl")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
    out = []
    for line in lines[-200:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        try:
            ts = datetime.datetime.fromisoformat(str(item.get("ts", "")))
            if ts >= cutoff:
                out.append(item)
        except ValueError:
            continue
    return out


def _delta(base: object, now: object) -> str:
    if isinstance(base, (int, float)) and isinstance(now, (int, float)):
        diff = now - base
        sign = "+" if diff >= 0 else ""
        return f"{base} → {now} ({sign}{diff})"
    return "NOT MEASURED"


def daily_report() -> dict:
    """Compare the live tree against the Day-1 baseline (§19 questions)."""
    baseline = load_baseline()
    scan = scan_repository()
    store = load_knowledge()
    experiments = load_experiments()
    recent = _history_since(7)
    states: dict[str, int] = {}
    for item in store["items"]:
        states[str(item.get("state", "?"))] = states.get(str(item.get("state", "?")), 0) + 1
    report = {
        "ts": _now_iso(),
        "revision": scan["revision"],
        "baseline_revision": (baseline or {}).get("revision"),
        "today": scan["repo"],
        "baseline_repo": (baseline or {}).get("repo", {}),
        "deltas": {
            key: _delta((baseline or {}).get("repo", {}).get(key), scan["repo"].get(key))
            for key in ("py_files", "py_loc", "rs_files", "rs_loc", "slint_files", "slint_loc")
        },
        "rust": scan["rust"],
        "knowledge_states": states,
        "unknowns": [i["id"] for i in store["items"] if i.get("state") == "UNKNOWN"],
        "experiments": len(experiments),
        "recent_history": len(recent),
        "bottleneck": _current_bottleneck(store),
    }
    _append_jsonl("history.jsonl", {"kind": "daily", **report})
    return report


def _current_bottleneck(store: dict) -> dict:
    """Highest-confidence actionable bottleneck, or an honest UNKNOWN."""
    ranked = [
        i
        for i in store["items"]
        if isinstance(i, dict) and i.get("state") in ("SUPPORTED", "VERIFIED")
    ]
    ranked.sort(key=lambda i: float(i.get("confidence", 0.0)), reverse=True)
    if ranked:
        top = ranked[0]
        return {
            "id": top["id"],
            "title": top["title"],
            "evidence": top["evidence"],
            "confidence": top["confidence"],
        }
    return {
        "id": "UNKNOWN",
        "title": "no supported bottleneck yet — measure first",
        "evidence": "",
        "confidence": 0.0,
    }


def render_report(report: dict) -> str:
    lines = [
        "# Vayren engineering intelligence — daily report",
        f"measured: {report['ts']}  revision: {report.get('revision')}",
        f"baseline: {report.get('baseline_revision', 'NONE')}",
        "",
        "## Repository (today vs baseline)",
    ]
    for key, delta in report["deltas"].items():
        lines.append(f"- {key}: {delta}")
    rust = report["rust"]
    release_profile = json.dumps(rust.get("profiles", {}).get("release", {}), sort_keys=True)
    lines += [
        "",
        "## Build system (observed)",
        f"- workspace members: {rust.get('member_count')}  "
        f"locked packages: {rust.get('locked_packages')}  "
        f"slint build.rs: {rust.get('build_rs_count')}",
        f"- release profile: {release_profile}",
        "",
        "## Knowledge",
        f"- states: {json.dumps(report['knowledge_states'], sort_keys=True)}",
        f"- unknowns: {', '.join(report['unknowns']) or 'none tracked'}",
        f"- experiments logged: {report['experiments']}",
        "",
        "## Current bottleneck",
    ]
    bottleneck = report["bottleneck"]
    lines.append(
        f"- {bottleneck['title']} (confidence {bottleneck['confidence']}, "
        f"evidence: {bottleneck['evidence'] or 'NOT MEASURED'})"
    )
    return "\n".join(lines)


# --- goals (§22, §41) + unknowns (§32) ---------------------------------------


def next_goal() -> dict:
    """Generate the next engineering objective from evidence, not wishes."""
    store = load_knowledge()
    experiments = load_experiments()
    bottleneck = _current_bottleneck(store)
    unknowns = [i for i in store["items"] if isinstance(i, dict) and i.get("state") == "UNKNOWN"]
    if bottleneck["id"] == "dup-build-chain" and not any(
        e.get("id") == "skip-redundant-build" for e in experiments
    ):
        goal = {
            "goal": "Eliminate the duplicate full-build invocation (run_tests.py --skip-build)",
            "why": "VERIFIED duplicate chain; cheapest safe win before touching LTO/Slint",
            "evidence": bottleneck["evidence"],
            "risk": "low — fail-closed freshness check, full validation path untouched",
        }
    elif unknowns:
        first = unknowns[0]
        goal = {
            "goal": f"Instrument UNKNOWN area: {first['id']}",
            "why": "exploration over exploitation (§31): unknowns block causal claims",
            "evidence": str(first.get("evidence", "")),
            "risk": "none — measurement only",
        }
    else:
        goal = {
            "goal": "NO ACTION REQUIRED — re-measure and compare with history",
            "why": "no supported bottleneck and no untracked unknowns (§36, §47)",
            "evidence": "knowledge store",
            "risk": "none",
        }
    goal["ts"] = _now_iso()
    return goal


def impact_for(files: list[str]) -> dict:
    """Change-impact plan for files, reusing benchmark.impact_plan (§6)."""
    plan = benchmark.impact_plan(files)
    intel_level = LEVEL_MAP.get(int(plan.get("level", 4)), "LEVEL 3")
    return {"files": files, "intel_level": intel_level, "plan": plan}


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vayren living engineering intelligence")
    parser.add_argument("--scan", action="store_true", help="observe the live tree")
    parser.add_argument("--baseline", action="store_true", help="store the Day-1 baseline")
    parser.add_argument("--seed", action="store_true", help="record Day-1 knowledge items")
    parser.add_argument("--report", action="store_true", help="daily intelligence report")
    parser.add_argument("--progress", action="store_true", help="minimal progress timeline")
    parser.add_argument("--goal", action="store_true", help="next evidence-driven objective")
    parser.add_argument("--unknowns", action="store_true", help="list UNKNOWN areas")
    parser.add_argument("--impact", nargs="*", default=None, help="impact plan for files")
    parser.add_argument(
        "--stage",
        nargs=2,
        metavar=("STAGE", "STATUS"),
        default=None,
        help="set lifecycle stage (done/active/pending)",
    )
    parser.add_argument(
        "--learn",
        nargs=6,
        metavar=("ID", "TITLE", "STATE", "EVIDENCE", "CONFIDENCE", "RESULT"),
        default=None,
        help="record/transition one knowledge item",
    )
    parser.add_argument(
        "--experiment",
        nargs=8,
        metavar=("ID", "HYPOTHESIS", "EXPECTED", "RISK", "BEFORE", "AFTER", "DECISION", "LESSON"),
        default=None,
        help="append one experiment record (failures kept)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    if args.stage:
        state = set_stage(args.stage[0], args.stage[1])
        print(json.dumps(state, indent=2) if args.json else _to_console(render_progress()))
        return 0
    if args.learn:
        item = add_knowledge(
            args.learn[0],
            args.learn[1],
            args.learn[2],
            args.learn[3],
            float(args.learn[4]),
            args.learn[5],
        )
        print(json.dumps({"id": item["id"], "state": item["state"]}, indent=2))
        return 0
    if args.experiment:
        record = log_experiment(*args.experiment)
        print(json.dumps({"id": record["id"], "decision": record["decision"]}, indent=2))
        return 0
    if args.scan:
        scan = scan_repository()
        print(json.dumps(scan, indent=2) if args.json else json.dumps(scan, indent=2))
        return 0
    if args.baseline:
        baseline = build_baseline()
        print(json.dumps(baseline, indent=2))
        return 0
    if args.seed:
        items = seed_day1_knowledge()
        print(json.dumps([{"id": i["id"], "state": i["state"]} for i in items], indent=2))
        return 0
    if args.report:
        report = daily_report()
        print(json.dumps(report, indent=2) if args.json else _to_console(render_report(report)))
        return 0
    if args.progress:
        print(_to_console(render_progress()))
        return 0
    if args.goal:
        goal = next_goal()
        print(json.dumps(goal, indent=2))
        return 0
    if args.unknowns:
        store = load_knowledge()
        unknowns = [i for i in store["items"] if i.get("state") == "UNKNOWN"]
        if args.json:
            print(json.dumps(unknowns, indent=2))
        else:
            for item in unknowns:
                print(f"- {item['id']}: {item['title']}")
            if not unknowns:
                print("no UNKNOWN areas tracked")
        return 0
    if args.impact is not None:
        result = impact_for(args.impact)
        print(json.dumps(result, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
