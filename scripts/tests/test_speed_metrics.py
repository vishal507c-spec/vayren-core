"""Speed metrics tests: context, edits, bottleneck (no estimates, no sleeps)."""

from __future__ import annotations

import sys
from pathlib import Path

SPEED_DIR = Path(__file__).resolve().parent.parent / "speed"
sys.path.insert(0, str(SPEED_DIR))

from bottleneck import costs_from_record, rank_costs, recommend  # noqa: E402
from context import (  # noqa: E402
    KNOWN_CONSTRAINTS,
    compile_context,
    complexity_signals,
    fingerprint,
    is_stale,
    normalize_spec,
    proxy_metrics,
)
from edits import edit_efficiency, first_pass, repair_tax  # noqa: E402


def test_fingerprint_stable_and_normalized() -> None:
    assert fingerprint("SMALL", "Add  zoom") == fingerprint("SMALL", "add zoom!")
    assert fingerprint("SMALL", "Add zoom") != fingerprint("UI", "Add zoom")
    assert fingerprint("SMALL", "a", ("b.py", "a.py")) == fingerprint(
        "SMALL", "a", ("a.py", "b.py")
    )
    assert normalize_spec("  Hello,\tWorld! ") == "hello world"


def test_is_stale_revision_and_mtime() -> None:
    assert is_stale({"revision": "abc"}, revision="abc") is False
    assert is_stale({"revision": "abc"}, revision="def") is True
    assert is_stale({"task": "x"}, revision="abc") is None  # no baseline: unknown
    record = {"files": {"a.py": 100.0}}
    assert is_stale(record, mtimes={"a.py": 100.0}) is False
    assert is_stale(record, mtimes={"a.py": 101.0}) is True
    assert is_stale(record, mtimes={}) is True  # file vanished: treat as changed


def test_compile_context_minimal_relevant_package() -> None:
    journal = [
        {
            "kind": "speed-task",
            "task": "OLD",
            "task_class": "SMALL",
            "fingerprint": "fp-old",
            "repairs": 0,
            "revision": "same",
            "notes": "did it",
        },
        {
            "kind": "speed-task",
            "task": "BROKEN",
            "task_class": "SMALL",
            "fingerprint": "fp-bad",
            "repairs": 3,
            "revision": "same",
        },
        {
            "kind": "speed-task",
            "task": "STALE",
            "task_class": "SMALL",
            "fingerprint": "fp-stale",
            "repairs": 0,
            "revision": "older",
        },
    ]
    package = compile_context(
        "wire the widget",
        task_class="SMALL",
        files=("04_chart/chart/widgets/candle_chart_widget.py",),
        journal_records=journal,
        revision="same",
    )
    assert package["domains"] == ["chart"]
    assert any("04_chart/chart/tests" in t for t in package["tests"])
    assert "pyright" in package["validators"]
    assert [p["task"] for p in package["plans"]] == ["OLD"]  # broken + stale excluded
    assert package["stale_excluded"] == 1
    assert package["constraints"] == list(KNOWN_CONSTRAINTS)
    assert package["constraint_provenance"] == "static"


def test_compile_context_caps_files_and_handles_no_files() -> None:
    files = tuple(f"f{i}.py" for i in range(40))
    package = compile_context("x", files=files, max_files=25)
    assert len(package["files"]) == 25
    assert package["truncated_files"] == 15
    empty = compile_context("x")
    assert empty["domains"] == []
    assert empty["tests"] == []
    assert empty["plans"] == []


def test_compile_context_reuses_impact_planner() -> None:
    package = compile_context("tooling", files=("scripts/speed/markers.py",))
    plan = package["validation_plan"]
    assert plan["level"] == 3  # tooling change: scripts tests + validators
    assert plan["full_gate"] is False
    docs = compile_context("docs", files=("README.md",))
    assert docs["validation_plan"]["level"] == 0
    chart = compile_context("chart fix", files=("04_chart/chart/engine/__init__.py",))
    assert chart["domains"] == ["chart"]
    assert any("04_chart/chart/tests" in t for t in chart["tests"])


def test_proxy_metrics_counts_and_rates() -> None:
    metrics = proxy_metrics(("a.py", "b.py", "a.py"), searches=4)
    assert metrics["files_inspected"] == 3
    assert metrics["files_unique"] == 2
    assert metrics["files_reread"] == 1
    assert metrics["duplicate_reads"] == 1
    assert metrics["duplicate_read_rate"] == 0.333
    assert metrics["search_count"] == 4
    assert metrics["proxy"] is True
    assert proxy_metrics()["duplicate_read_rate"] is None  # no reads: no rate


def test_complexity_tiers_are_documented_heuristic() -> None:
    assert complexity_signals()["tier"] == "TRIVIAL"
    assert complexity_signals(files_changed=2, lines_added=50)["tier"] == "SMALL"
    assert complexity_signals(files_changed=5)["tier"] == "MEDIUM"
    assert complexity_signals(domains=("market", "chart"))["tier"] == "MEDIUM"
    assert complexity_signals(domains=("a", "b", "c"))["tier"] == "LARGE"
    big = complexity_signals(files_changed=1, lines_added=600)
    assert big["tier"] == "LARGE" and big["lines_total"] == 600
    assert "heuristic" in big["tier_heuristic"].lower() or "LARGE" in big["tier_heuristic"]


def test_repair_tax_zero_unmeasured_and_window() -> None:
    clean = {"repairs": 0, "segments": {"validation_window_s": 5.0}, "total_s": 50.0}
    assert repair_tax(clean)["repair_tax_s"] == 0.0
    # Zero repairs needs no clock: window unmeasured still means zero tax.
    assert repair_tax({"repairs": 0, "segments": {}, "total_s": 10.0})["repair_tax_ratio"] == 0.0
    dirty = {"repairs": 2, "segments": {"validation_window_s": 30.0}, "total_s": 100.0}
    taxed = repair_tax(dirty)
    assert taxed["repair_tax_s"] == 30.0
    assert taxed["repair_tax_ratio"] == 0.3
    assert "upper bound" in taxed["basis"]
    unknown = {"repairs": None, "segments": {}, "total_s": 10.0}
    assert repair_tax(unknown)["basis"] == "UNMEASURED"


def test_first_pass_true_false_unknown() -> None:
    assert first_pass({"repairs": 0, "failures": 0}) is True
    assert first_pass({"repairs": 1, "failures": 0}) is False
    assert first_pass({"repairs": 0, "failures": 1}) is False
    assert first_pass({"repairs": None, "failures": 0}) is None


def test_edit_efficiency_rates_and_none_safety() -> None:
    outcome = edit_efficiency(total_edits=10, failed_edits=1, repeated_edits=2, reverted_edits=1)
    assert outcome["failed_rate"] == 0.1
    assert outcome["repeated_rate"] == 0.2
    assert outcome["rework_rate"] == 0.4
    empty = edit_efficiency()
    assert empty["failed_rate"] is None and empty["rework_rate"] is None
    zero = edit_efficiency(total_edits=0, failed_edits=0)
    assert zero["failed_rate"] is None


def test_rank_costs_measured_first_unmeasured_last() -> None:
    ranked = rank_costs({"a": 5.0, "b": None, "c": 9.0, "d": None})
    assert [r["phase"] for r in ranked] == ["c", "a", "b", "d"]
    assert ranked[0]["basis"] == "measured"
    assert ranked[-1]["basis"] == "UNMEASURED"


def test_costs_from_record_labels_unmeasured() -> None:
    record = {
        "segments": {"repo_understanding_s": 12.0, "implementation_s": 40.0},
        "validation_s": 8.0,
        "phases": {"PLANNING": 5.0, "REPAIR": 2.0},
        "planning_s": None,
        "repair_s": None,
    }
    costs = costs_from_record(record)
    assert costs["repository_discovery"] == 12.0
    assert costs["validation"] == 8.0
    assert costs["context_preparation"] is None


def test_recommend_needs_evidence() -> None:
    items = recommend({})
    rules = {i["rule"] for i in items}
    assert "insufficient_evidence" in rules
    assert "parallel_validation" in rules  # standing static rule


def test_recommend_fires_on_measured_thresholds() -> None:
    items = recommend(
        {
            "repair_tax_median": 0.5,
            "duplicate_read_rate": 0.4,
            "validation_share": 0.6,
            "n": 5,
            "first_pass_rate": 0.4,
        }
    )
    rules = {i["rule"] for i in items}
    assert {"repair_tax", "duplicate_search", "validation_share", "first_pass"} <= rules
    assert "insufficient_evidence" not in rules


def test_recommend_unknown_share_blocks_other_guesses() -> None:
    items = recommend({"unknown_share": 0.9, "n": 1})
    rules = {i["rule"] for i in items}
    assert "unknown_dominates" in rules
    assert "implementation_dominates" not in rules
