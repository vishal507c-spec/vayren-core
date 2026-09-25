"""Tests for the living engineering-intelligence layer (scripts/vayren_intel.py).

Store isolation: every test points VAYREN_INTEL_DIR at a tmp dir, so the
live `.repo_index/intel/` tree is never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import vayren_intel  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(vayren_intel, "STORE_DIR", tmp_path / "intel")


def test_stages_cover_lifecycle() -> None:
    assert vayren_intel.STAGES == (
        "SCAN",
        "MEASURE",
        "ANALYZE",
        "PLAN",
        "EXPERIMENT",
        "VALIDATE",
        "LEARN",
        "UPDATE_MODEL",
    )


def test_knowledge_states_cover_lifecycle() -> None:
    for state in (
        "UNKNOWN",
        "OBSERVED",
        "HYPOTHESIS",
        "SUPPORTED",
        "VERIFIED",
        "REPEATEDLY_VERIFIED",
        "STALE",
        "OBSOLETE",
        "REJECTED",
    ):
        assert state in vayren_intel.KNOWLEDGE_STATES


def test_scan_measures_live_tree() -> None:
    scan = vayren_intel.scan_repository()
    assert scan["repo"]["py_files"] > 100
    assert scan["repo"]["rs_files"] > 50
    assert scan["repo"]["slint_files"] > 10
    assert scan["rust"]["member_count"] == 8
    assert scan["rust"]["locked_packages"] > 100
    assert scan["rust"]["profiles"]["release"]["lto"] is True


def test_scan_never_fabricates() -> None:
    scan = vayren_intel.scan_repository()
    assert scan["revision"]  # real git revision, not a placeholder


def test_baseline_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never shell out to real cargo in unit tests (lock contention, minutes).
    monkeypatch.setattr(
        vayren_intel,
        "timed_check",
        lambda crate: {"crate": crate, "rc": 0, "elapsed_s": 9.9, "measured": True},
    )
    baseline = vayren_intel.build_baseline()
    assert baseline["schema"] == "intel-baseline/v1"
    assert baseline["day"] == 1
    assert (vayren_intel.STORE_DIR / "baseline.json").is_file()
    assert vayren_intel.load_baseline() == baseline


def test_knowledge_rejects_bad_state() -> None:
    with pytest.raises(vayren_intel.IntelError):
        vayren_intel.add_knowledge("x", "y", "GUESS", "none", 0.0)


def test_knowledge_transition_keeps_history() -> None:
    vayren_intel.add_knowledge("k1", "title", "HYPOTHESIS", "ev", 0.5)
    item = vayren_intel.add_knowledge("k1", "title", "REJECTED", "ev2", 0.9, "failed")
    assert item["state"] == "REJECTED"
    assert [h["state"] for h in item["history"]] == ["HYPOTHESIS", "REJECTED"]
    # Rejected (negative) knowledge survives.
    assert any(i["id"] == "k1" for i in vayren_intel.load_knowledge()["items"])


def test_experiment_log_keeps_failures() -> None:
    vayren_intel.log_experiment(
        "e1", "hyp", "exp", "low", "before", "after", "REJECT", "lesson learned"
    )
    experiments = vayren_intel.load_experiments()
    assert len(experiments) == 1
    assert experiments[0]["lesson"] == "lesson learned"


def test_stage_rejects_bad_transition() -> None:
    with pytest.raises(vayren_intel.IntelError):
        vayren_intel.set_stage("BOGUS", "done")
    with pytest.raises(vayren_intel.IntelError):
        vayren_intel.set_stage("SCAN", "almost")


def test_progress_reflects_real_state() -> None:
    vayren_intel.set_stage("SCAN", "done")
    vayren_intel.set_stage("MEASURE", "active")
    text = vayren_intel.render_progress()
    assert "SCAN" in text and "MEASURE" in text and "ANALYZE" in text
    assert "1/8 stages done" in text
    # Done/active/pending marks differ by console encoding — either is real.
    assert "✓ SCAN" in text or "[x] SCAN" in text
    assert "● MEASURE" in text or "[>] MEASURE" in text
    assert "○ ANALYZE" in text or "[ ] ANALYZE" in text


def test_console_fallback_is_ascii() -> None:
    text = vayren_intel._to_console("✓ ● ○ █ ░ → — ·")
    text.encode("cp1252")


def test_progress_current_matches_active_stage() -> None:
    vayren_intel.set_stage("PLAN", "active")
    text = vayren_intel.render_progress()
    assert "Current: PLAN" in text
    line = next(ln for ln in text.splitlines() if ln.endswith(" PLAN"))
    assert "[ ]" not in line and "○" not in line


def test_report_compares_against_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vayren_intel,
        "timed_check",
        lambda crate: {"crate": crate, "rc": 0, "elapsed_s": 9.9, "measured": True},
    )
    vayren_intel.build_baseline()
    report = vayren_intel.daily_report()
    assert report["baseline_revision"] == report["revision"]
    assert "py_files" in report["deltas"]
    assert "→" in report["deltas"]["py_files"]


def test_bottleneck_unknown_when_no_evidence() -> None:
    assert vayren_intel._current_bottleneck({"items": []})["id"] == "UNKNOWN"


def test_goal_prefers_verified_bottleneck() -> None:
    vayren_intel.add_knowledge("dup-build-chain", "dup", "VERIFIED", "code", 1.0)
    goal = vayren_intel.next_goal()
    assert "duplicate" in goal["goal"].lower()
    assert goal["evidence"] == "code"


def test_goal_says_no_action_when_healthy() -> None:
    vayren_intel.add_knowledge("k1", "t", "REPEATEDLY_VERIFIED", "ev", 1.0)
    goal = vayren_intel.next_goal()
    assert goal["goal"].startswith("NO ACTION REQUIRED")


def test_impact_reuses_benchmark_levels() -> None:
    result = vayren_intel.impact_for(["README.md"])
    assert result["intel_level"] == "LEVEL 0"
    result = vayren_intel.impact_for(["pyproject.toml"])
    assert result["intel_level"] == "LEVEL 3"
    assert result["plan"]["full_gate"] is True


def test_impact_tiny_rust_change_avoids_full_gate() -> None:
    """§48: a single kernel file gets a targeted cargo filter, not the gate."""
    result = vayren_intel.impact_for(["rust/vayren-core/src/throttle.rs"])
    assert result["intel_level"] == "LEVEL 2"
    assert result["plan"]["cargo"] == ["cargo test -p vayren-core"]
    assert result["plan"]["pytest"] == []
    assert result["plan"]["full_gate"] is False


def test_impact_ui_change_targets_shell() -> None:
    """§49: a single Slint file targets shell tests, never the full gate."""
    result = vayren_intel.impact_for(["rust/vayren-shell/ui/market.slint"])
    assert result["intel_level"] == "LEVEL 2"
    assert result["plan"]["cargo"] == ["cargo test -p vayren-shell"]
    assert result["plan"]["full_gate"] is False


def test_timed_check_reports_shape_without_compiling() -> None:
    # Bogus crate: cargo fails fast on resolution, no compilation, no lock wait.
    result = vayren_intel.timed_check("__no_such_crate__")
    assert result["crate"] == "__no_such_crate__"
    assert set(result) == {"crate", "rc", "elapsed_s", "measured"}


def test_level_map_keeps_full_safety_net() -> None:
    assert vayren_intel.LEVEL_MAP[4] == "LEVEL 3"


def test_learn_cli_records_item(capsys: pytest.CaptureFixture[str]) -> None:
    rc = vayren_intel.main(
        ["--learn", "k9", "title nine", "OBSERVED", "test evidence", "0.6", "ok"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"id": "k9", "state": "OBSERVED"}
    assert any(i["id"] == "k9" for i in vayren_intel.load_knowledge()["items"])


def test_learn_cli_rejects_confidence_text() -> None:
    with pytest.raises(ValueError):
        vayren_intel.main(["--learn", "k9", "t", "OBSERVED", "ev", "high", ""])


def test_experiment_cli_keeps_record(capsys: pytest.CaptureFixture[str]) -> None:
    rc = vayren_intel.main(
        ["--experiment", "e9", "hyp", "exp", "low", "before", "after", "KEEP", "lesson"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"id": "e9", "decision": "KEEP"}
    assert vayren_intel.load_experiments()[0]["lesson"] == "lesson"
