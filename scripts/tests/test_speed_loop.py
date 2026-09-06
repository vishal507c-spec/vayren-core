"""Speed loop tests: markers, journal, dashboard (controlled timestamps)."""

from __future__ import annotations

import sys
from pathlib import Path

SPEED_DIR = Path(__file__).resolve().parent.parent / "speed"
sys.path.insert(0, str(SPEED_DIR))

from dashboard import dashboard_lines, median_costs, snapshot  # noqa: E402
from journal import (  # noqa: E402
    aggregate,
    append_jsonl,
    append_mark,
    build_record,
    find_pairs,
    load_jsonl,
    speed_tasks,
)
from markers import MILESTONES, PHASES, loop_segments, phase_totals  # noqa: E402

BASE = "2026-09-06T10:00:00.000+00:00"


def _ts(offset_s: float) -> str:
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(BASE)
    return (base + timedelta(seconds=offset_s)).isoformat(timespec="milliseconds")


def _milestones(task: str, offsets: dict[str, float]) -> list[dict]:
    return [
        {"task": task, "ts": _ts(off), "kind": "milestone", "name": name}
        for name, off in offsets.items()
    ]


def test_milestone_order_matches_spec() -> None:
    assert MILESTONES == (
        "TASK_START",
        "REPOSITORY_UNDERSTANDING_START",
        "PLAN_READY",
        "FIRST_EDIT",
        "IMPLEMENTATION_COMPLETE",
        "FIRST_VALIDATION",
        "FINAL_GREEN",
        "TASK_END",
    )
    assert PHASES == ("DISCOVERY", "PLANNING", "IMPLEMENTATION", "VALIDATION", "REPAIR", "DONE")


def test_full_chain_measures_every_segment() -> None:
    marks = _milestones(
        "T",
        {
            "TASK_START": 0,
            "REPOSITORY_UNDERSTANDING_START": 10,
            "PLAN_READY": 40,
            "FIRST_EDIT": 50,
            "IMPLEMENTATION_COMPLETE": 150,
            "FIRST_VALIDATION": 155,
            "FINAL_GREEN": 175,
            "TASK_END": 180,
        },
    )
    segments = loop_segments(marks, "T")
    assert segments["startup_s"] == 10.0
    assert segments["repo_understanding_s"] == 30.0
    assert segments["first_edit_latency_s"] == 10.0
    assert segments["implementation_s"] == 100.0
    assert segments["pre_validation_gap_s"] == 5.0
    assert segments["validation_window_s"] == 20.0
    assert segments["closeout_s"] == 5.0
    assert segments["total_s"] == 180.0


def test_missing_markers_are_unmeasured_not_zero() -> None:
    marks = _milestones("T", {"TASK_START": 0, "TASK_END": 100})
    segments = loop_segments(marks, "T")
    assert segments["total_s"] == 100.0
    assert segments["implementation_s"] is None
    assert segments["validation_window_s"] is None


def test_out_of_order_evidence_is_unmeasured() -> None:
    marks = _milestones("T", {"TASK_START": 0, "TASK_END": 100, "FIRST_EDIT": 90})
    marks.append({"task": "T", "ts": _ts(80), "kind": "milestone", "name": "PLAN_READY"})
    # PLAN_READY(80) -> FIRST_EDIT(90) is ordered and measured; the
    # IMPLEMENTATION segment has no end marker so it stays UNMEASURED.
    segments = loop_segments(marks, "T")
    assert segments["first_edit_latency_s"] == 10.0
    assert segments["implementation_s"] is None
    reversed_marks = _milestones(
        "R", {"TASK_START": 0, "PLAN_READY": 50, "REPOSITORY_UNDERSTANDING_START": 60}
    )
    assert loop_segments(reversed_marks, "R")["repo_understanding_s"] is None


def test_first_milestone_wins_repeated_marks() -> None:
    marks = _milestones("T", {"TASK_START": 0, "TASK_END": 100, "PLAN_READY": 40})
    marks.append({"task": "T", "ts": _ts(20), "kind": "milestone", "name": "PLAN_READY"})
    assert loop_segments(marks, "T")["repo_understanding_s"] is None  # no REPO_START
    marks.append(
        {
            "task": "T",
            "ts": _ts(5),
            "kind": "milestone",
            "name": "REPOSITORY_UNDERSTANDING_START",
        }
    )
    # The earlier PLAN_READY duplicate (t=20) wins over t=40, so the
    # understanding window is 20 - 5 = 15.
    assert loop_segments(marks, "T")["repo_understanding_s"] == 15.0


def test_phase_totals_persist_and_idle_splits_unknown() -> None:
    marks = [
        {"task": "T", "ts": _ts(0), "kind": "phase", "name": "DISCOVERY"},
        {"task": "T", "ts": _ts(30), "kind": "phase", "name": "PLANNING"},
        {"task": "T", "ts": _ts(300), "kind": "phase", "name": "IMPLEMENTATION"},
        {"task": "T", "ts": _ts(310), "kind": "phase", "name": "DONE"},
    ]
    totals = phase_totals(marks, "T", idle_s=120.0)
    assert totals["DISCOVERY"] == 30.0
    assert totals["PLANNING"] == 0.0  # 270s gap exceeds idle -> UNKNOWN
    assert totals["IMPLEMENTATION"] == 10.0
    assert totals["UNKNOWN"] == 270.0
    assert "DONE" not in totals


def test_phase_explicit_duration_ms_adds_measured_time() -> None:
    marks = [
        {
            "task": "T",
            "ts": _ts(0),
            "kind": "phase",
            "name": "VALIDATION",
            "duration_ms": 5000,
        },
    ]
    assert phase_totals(marks, "T")["VALIDATION"] == 5.0


def test_build_record_rejects_unknown_class_and_reuse() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_record("T", "NOPE", [])
    with pytest.raises(ValueError):
        build_record("T", "SMALL", [], reuse="lukewarm")


def test_build_record_embeds_derived_segments() -> None:
    marks = _milestones(
        "T",
        {"TASK_START": 0, "FIRST_EDIT": 10, "IMPLEMENTATION_COMPLETE": 60, "TASK_END": 100},
    )
    record = build_record(
        "T",
        "SMALL",
        marks,
        fingerprint="abc123",
        validation_s=17.0,
        tests=82,
        failures=0,
        repairs=0,
    )
    assert record["total_s"] == 100.0
    assert record["segments"]["implementation_s"] == 50.0
    assert record["validation_s"] == 17.0  # explicit clocked time preserved
    assert record["segments"]["validation_window_s"] is None  # not conflated


def test_mark_and_journal_roundtrip_append_only(tmp_path: Path) -> None:
    marks_file = tmp_path / "speed_marks.jsonl"
    journal_file = tmp_path / "speed_journal.jsonl"
    append_mark("T", "milestone", "TASK_START", ts=_ts(0), path=marks_file)
    append_mark("T", "milestone", "TASK_END", ts=_ts(42), path=marks_file)
    marks = load_jsonl(marks_file)
    record = build_record("T", "MICRO", marks, repairs=0, failures=0)
    append_jsonl(journal_file, record)
    append_jsonl(journal_file, {"kind": "speed-task", "task": "T2"})
    loaded = speed_tasks(load_jsonl(journal_file))
    assert [r["task"] for r in loaded] == ["T", "T2"]
    assert loaded[0]["total_s"] == 42.0


def test_find_pairs_only_matches_shared_fingerprint() -> None:
    def rec(task: str, fingerprint: str | None, total: float | None, start: str) -> dict:
        return {
            "kind": "speed-task",
            "task": task,
            "task_class": "SMALL",
            "fingerprint": fingerprint,
            "total_s": total,
            "start": start,
        }

    records = [
        rec("A1", "fp1", 100.0, "2026-09-01"),
        rec("A2", "fp1", 50.0, "2026-09-02"),
        rec("B1", "fp2", 70.0, "2026-09-01"),
        rec("C1", None, 10.0, "2026-09-01"),
        rec("D1", "fp3", None, "2026-09-01"),
    ]
    pairs = find_pairs(records)
    assert len(pairs) == 1
    assert pairs[0]["fingerprint"] == "fp1"
    assert pairs[0]["before_s"] == 100.0
    assert pairs[0]["current_s"] == 50.0
    assert pairs[0]["speedup"] == 2.0
    assert pairs[0]["delta_s"] == 50.0


def test_aggregate_keeps_metrics_separate() -> None:
    records = [
        {
            "kind": "speed-task",
            "task": "A",
            "total_s": 100.0,
            "validation_s": 20.0,
            "repairs": 0,
            "failures": 0,
            "human_interventions": 0,
            "segments": {"validation_window_s": 20.0},
            "reuse": "cold",
            "fingerprint": "fp1",
        },
        {
            "kind": "speed-task",
            "task": "B",
            "total_s": 50.0,
            "validation_s": None,
            "repairs": 2,
            "failures": 1,
            "human_interventions": 1,
            "segments": {"validation_window_s": 30.0},
            "reuse": "warm",
            "fingerprint": "fp1",
        },
    ]
    agg = aggregate(records)
    assert agg["n"] == 2
    assert agg["task_wall_s_median"] == 75.0
    assert agg["validation_s_median"] == 20.0
    assert agg["first_pass_rate"] == 0.5
    assert agg["repair_tax_median"] == 0.3  # clean 0.0 + window 30/50
    assert agg["reuse_speedup"] == 2.0
    assert agg["reuse_pairs"] == 1
    assert agg["human_interventions"] == 1
    assert "AI_UTILIZATION" not in str(agg)


def test_dashboard_empty_journal_is_honest() -> None:
    lines = dashboard_lines([])
    text = "\n".join(lines)
    assert "NOT MEASURED" in text
    assert "AI_UTILIZATION_SCORE: NOT MEASURED here" in text
    assert "no comparable pair" not in text  # empty table uses the (none) row
    assert "(no measured costs yet)" in text


def test_dashboard_pairs_and_unpaired_rows() -> None:
    records = [
        {
            "kind": "speed-task",
            "task": "A1",
            "task_class": "SMALL",
            "fingerprint": "fp1",
            "total_s": 100.0,
            "validation_s": 20.0,
            "repairs": 0,
            "failures": 0,
            "start": "2026-09-01",
        },
        {
            "kind": "speed-task",
            "task": "A2",
            "task_class": "SMALL",
            "fingerprint": "fp1",
            "total_s": 50.0,
            "validation_s": 10.0,
            "repairs": 0,
            "failures": 0,
            "start": "2026-09-02",
        },
        {
            "kind": "speed-task",
            "task": "SOLO",
            "task_class": "UI",
            "fingerprint": "fp9",
            "total_s": 30.0,
            "validation_s": None,
            "repairs": None,
            "failures": None,
            "start": "2026-09-03",
        },
    ]
    text = "\n".join(dashboard_lines(records))
    assert "2.00x" in text
    assert "HIGH (same fingerprint)" in text
    assert text.count("NOT MEASURED") >= 3  # solo row: speedup + validation ratio
    assert "— (no comparable pair) |" in text


def test_dashboard_flags_divergent_windows() -> None:
    records = [
        {
            "kind": "speed-task",
            "task": "LOOSE-COLD",
            "task_class": "MICRO",
            "fingerprint": "fp-loose",
            "total_s": 377.6,
            "validation_s": 3.66,
            "repairs": 0,
            "failures": 0,
            "start": "2026-09-01",
        },
        {
            "kind": "speed-task",
            "task": "LOOSE-WARM",
            "task_class": "MICRO",
            "fingerprint": "fp-loose",
            "total_s": 57.1,
            "validation_s": 13.93,
            "repairs": 0,
            "failures": 0,
            "start": "2026-09-02",
        },
    ]
    text = "\n".join(dashboard_lines(records))
    # Totals say 6.61x faster while clocked validation says 0.26x (slower):
    # the dashboard must refuse HIGH confidence, not print 6.61x as a win.
    assert "6.61x" in text  # data stays visible (append-only, never hidden)
    assert "0.26x" in text  # validation ratio shown beside it
    assert "DIVERGENT" in text
    assert "do not claim" in text
    assert "HIGH (same fingerprint)" not in text


def test_snapshot_and_median_costs_cover_unmeasured() -> None:
    records = [
        {
            "kind": "speed-task",
            "task": "A",
            "total_s": 100.0,
            "validation_s": 20.0,
            "segments": {"implementation_s": 60.0},
            "phases": {"UNKNOWN": 10.0},
        },
    ]
    snap = snapshot(records)
    assert snap["validation_share"] == 0.2
    assert snap["implementation_share"] == 0.6
    medians = median_costs(records)
    assert medians["editing"] == 60.0
    assert medians["planning"] is None
