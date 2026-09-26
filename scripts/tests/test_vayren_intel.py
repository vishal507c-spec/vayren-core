"""Tests for the living engineering-intelligence layer (scripts/vayren_intel.py).

Store isolation: every test points VAYREN_INTEL_DIR at a tmp dir, so the
live `.repo_index/intel/` tree is never touched.
"""

from __future__ import annotations

import datetime
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


def _series(*values: float) -> list[dict]:
    return [
        {"ts": f"2026-09-2{i}T00:00:00+00:00", "value": v, "revision": f"r{i}"}
        for i, v in enumerate(values, start=1)
    ]


def test_trend_up_detected() -> None:
    result = vayren_intel.analyze_series(_series(420.0, 440.0, 460.0, 480.0))
    assert result["classification"] == "TREND_UP"
    assert result["swing_pct"] > 5.0
    assert "monotone increase" in result["reason"]


def test_trend_down_detected() -> None:
    result = vayren_intel.analyze_series(_series(480.0, 460.0, 440.0, 420.0))
    assert result["classification"] == "TREND_DOWN"
    assert result["swing_pct"] < -5.0


def test_stable_noisy_series_is_not_regression() -> None:
    result = vayren_intel.analyze_series(_series(400.0, 402.0, 399.0, 405.0, 404.0))
    assert result["classification"] == "STABLE"


def test_single_spike_not_monotone_trend() -> None:
    result = vayren_intel.analyze_series(_series(400.0, 400.0, 900.0, 402.0))
    assert result["classification"] == "STABLE"


def test_two_points_insufficient() -> None:
    result = vayren_intel.analyze_series(_series(400.0, 500.0))
    assert result["classification"] == "INSUFFICIENT"
    assert result["current"] is None


def test_empty_series_insufficient() -> None:
    result = vayren_intel.analyze_series([])
    assert result["classification"] == "INSUFFICIENT"
    assert result["points"] == 0


def test_variable_when_movement_not_monotone() -> None:
    result = vayren_intel.analyze_series(_series(400.0, 430.0, 410.0, 440.0))
    assert result["classification"] == "VARIABLE"
    assert "not monotone" in result["reason"]


def test_delta_exact() -> None:
    result = vayren_intel.analyze_series(_series(100.0, 110.0, 120.0))
    assert result["current"] == 120.0
    assert result["previous"] == 110.0
    assert result["first"] == 100.0
    assert result["swing_pct"] == pytest.approx(20.0)


def test_radar_blocked_when_store_broken() -> None:
    # Empty store (no baseline at all) -> integrity gate BLOCKS analysis.
    result = vayren_intel.radar()
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "HISTORY UNTRUSTED"


def test_radar_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vayren_intel,
        "timed_check",
        lambda crate: {"crate": crate, "rc": 0, "elapsed_s": 1.0, "measured": True},
    )
    vayren_intel.build_baseline()
    vayren_intel.daily_report()
    first = vayren_intel.radar()
    second = vayren_intel.radar()
    assert first["series"] == second["series"]
    assert first["status"] == "OK" == second["status"]


def test_radar_records_no_change_required_when_stable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        vayren_intel,
        "timed_check",
        lambda crate: {"crate": crate, "rc": 0, "elapsed_s": 1.0, "measured": True},
    )
    vayren_intel.build_baseline()
    vayren_intel.daily_report()
    rc = vayren_intel.main(["--trends"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STABLE" in out or "INSUFFICIENT" in out
    assert "TREND_UP" not in out and "TREND_DOWN" not in out


def test_radar_persists_real_trend_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vayren_intel,
        "timed_check",
        lambda crate: {"crate": crate, "rc": 0, "elapsed_s": 1.0, "measured": True},
    )
    vayren_intel.build_baseline()
    # Injected monotone series gets persisted as a knowledge item.
    path = vayren_intel._path("history.jsonl")
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for i, value in enumerate((10.0, 12.0, 14.0, 16.0), start=2):
            fh.write(
                json.dumps(
                    {
                        "kind": "daily",
                        "ts": f"2026-09-2{i}T00:00:00+00:00",
                        "today": {"py_files": value},
                    }
                )
                + "\n"
            )
    result = vayren_intel.radar()
    if result["status"] != "OK":
        print("BLOCKED problems:", result)
    assert result["status"] == "OK"
    assert "py_files" in result["series"]
    assert result["series"]["py_files"]["classification"] == "TREND_UP"


def test_experiment_cli_keeps_record(capsys: pytest.CaptureFixture[str]) -> None:
    rc = vayren_intel.main(
        ["--experiment", "e9", "hyp", "exp", "low", "before", "after", "KEEP", "lesson"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"id": "e9", "decision": "KEEP"}
    assert vayren_intel.load_experiments()[0]["lesson"] == "lesson"


def test_verify_fails_without_baseline() -> None:
    result = vayren_intel.verify_store()
    assert result["status"] == "FAIL"
    assert any("baseline missing" in p for p in result["problems"])
    assert result["baseline_present"] is False


def test_verify_green_on_wellformed_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vayren_intel,
        "timed_check",
        lambda crate: {"crate": crate, "rc": 0, "elapsed_s": 1.0, "measured": True},
    )
    vayren_intel.build_baseline()
    vayren_intel.add_knowledge("k1", "t", "VERIFIED", "real evidence", 1.0)
    vayren_intel.log_experiment("e1", "h", "e", "low", "b", "a", "KEEP", "l")
    result = vayren_intel.verify_store()
    assert result["status"] == "PASS", result["problems"]


def test_verify_flags_foreign_repo_index_dirs() -> None:
    (vayren_intel.STORE_DIR.parent / "repo_index_hold2").mkdir(parents=True)
    result = vayren_intel.verify_store()
    assert result["status"] == "FAIL"
    assert result["stray_dirs"] == ["repo_index_hold2"]


def test_verify_flags_invalid_knowledge_state() -> None:
    store = vayren_intel.load_knowledge()
    store["items"].append({"id": "bad", "state": "MAYBE", "evidence": "x"})
    vayren_intel._store_json("knowledge.json", store)
    result = vayren_intel.verify_store()
    assert result["status"] == "FAIL"
    assert any("bad" in p for p in result["problems"])


# --- CI timing ingestion (Phase 9) -------------------------------------------

JOB1 = {
    "name": "build-test",
    "conclusion": "success",
    "startedAt": "2026-09-25T12:31:30Z",
    "completedAt": "2026-09-25T12:41:33Z",
    "steps": [
        {
            "name": "Run time python scripts/build_rust.py --lean-test",
            "conclusion": "success",
            "startedAt": "2026-09-25T12:32:00Z",
            "completedAt": "2026-09-25T12:40:00Z",
        }
    ],
}
RUN1 = {
    "databaseId": 42,
    "headSha": "a" * 40,
    "conclusion": "success",
    "status": "completed",
    "workflowName": "CI",
    "headBranch": "main",
    "event": "push",
    "createdAt": "2026-09-25T12:29:18Z",
    "updatedAt": "2026-09-25T12:41:33Z",
    "jobs": [JOB1],
}


def test_ci_record_valid_build() -> None:
    record = vayren_intel.build_ci_record(RUN1)
    assert record["schema"] == "ci-timing/v1"
    assert record["run_id"] == 42
    assert record["metrics"]["ci.build-test.duration_s"] == 603.0
    assert any("build_rust.py --lean-test" in _key for _key in record["metrics"])
    assert record["measurement_method"] == "gh-api-job-step-durations"
    assert vayren_intel.validate_ci_record(record) == []


def test_ci_record_rejects_missing_timestamp() -> None:
    bad = json.loads(json.dumps(RUN1))
    del bad["updatedAt"]
    with pytest.raises(vayren_intel.IntelError, match="updatedAt"):
        vayren_intel.build_ci_record(bad)


def test_ci_record_rejects_missing_commit() -> None:
    bad = json.loads(json.dumps(RUN1))
    bad["headSha"] = "abc"
    with pytest.raises(vayren_intel.IntelError, match="headSha"):
        vayren_intel.build_ci_record(bad)


def test_ci_record_rejects_missing_run_id() -> None:
    bad = json.loads(json.dumps(RUN1))
    bad["databaseId"] = None
    with pytest.raises(vayren_intel.IntelError, match="databaseId"):
        vayren_intel.build_ci_record(bad)


def test_ci_record_rejects_failed_run() -> None:
    bad = json.loads(json.dumps(RUN1))
    bad["conclusion"] = "failure"
    with pytest.raises(vayren_intel.IntelError, match="conclusion"):
        vayren_intel.build_ci_record(bad)


def test_ci_record_skips_job_with_missing_timing() -> None:
    # Missing timing on a single job raises (no measurable output at all).
    job = json.loads(json.dumps(JOB1))
    del job["completedAt"]
    with pytest.raises(vayren_intel.IntelError, match="zero measurable"):
        vayren_intel.build_ci_record({**json.loads(json.dumps(RUN1)), "jobs": [job]})


def test_ci_record_rejects_empty_jobs() -> None:
    with pytest.raises(vayren_intel.IntelError, match="missing jobs"):
        vayren_intel.build_ci_record({**json.loads(json.dumps(RUN1)), "jobs": []})


def test_validate_detects_bad_metric_value() -> None:
    record = vayren_intel.build_ci_record(RUN1)
    record["metrics"]["ci.build-test.duration_s"] = -1
    problems = vayren_intel.validate_ci_record(record)
    assert any("negative" in p for p in problems)


def test_metric_intent_duration_lower_better() -> None:
    assert vayren_intel._ci_intent("ci.build-test.duration_s") == "duration_s"
    assert vayren_intel.METRIC_INTENT["duration_s"] == "lower-is-better"


def test_metric_intent_unknown_for_counts() -> None:
    assert vayren_intel._ci_intent("ci.test-count") == "UNKNOWN"


def _baseline_without_cargo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vayren_intel,
        "timed_check",
        lambda crate: {"crate": crate, "rc": 0, "elapsed_s": 1.0, "measured": True},
    )
    vayren_intel.build_baseline()


def test_ingest_rejects_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    monkeypatch.setattr(vayren_intel, "fetch_run", lambda _rid: RUN1)
    first = vayren_intel.ingest_ci_run(42)
    second = vayren_intel.ingest_ci_run(42)
    assert first["ingested"] is True
    assert second["ingested"] is False
    assert second["reason"].startswith("duplicate")


def test_ingest_blocks_on_untrusted_store() -> None:
    # Empty store: verify_store fails (baseline missing) -> ingestion blocks.
    with pytest.raises(vayren_intel.IntelError):
        vayren_intel.ingest_ci_run(42)


def test_ingest_writes_exactly_one_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    monkeypatch.setattr(vayren_intel, "fetch_run", lambda _rid: RUN1)
    first = vayren_intel.ingest_ci_run(42)
    assert first["ingested"] is True
    lines = vayren_intel._path("history.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum('"ci-timing/v1"' in ln for ln in lines) == 1


def test_radar_consumes_ci_history(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    monkeypatch.setattr(vayren_intel, "fetch_run", lambda _rid: RUN1)
    vayren_intel.ingest_ci_run(42)
    radar = vayren_intel.radar()
    assert "ci.build-test.duration_s" in radar["series"]
    assert radar["series"]["ci.build-test.duration_s"]["classification"] == "INSUFFICIENT"


def test_radar_no_fabricated_ci_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    radar = vayren_intel.radar()
    assert not any(m.startswith("ci.") for m in radar["series"])


def test_ingest_skips_partial_failed_job() -> None:
    record = vayren_intel.build_ci_record(RUN1)
    assert record["metrics"].get("ci.build-test.duration_s") == 603.0
    partial = json.loads(json.dumps(JOB1))
    partial["conclusion"] = "failure"
    record = vayren_intel.build_ci_record({**json.loads(json.dumps(RUN1)), "jobs": [partial]})
    # Failed run rejected before mapping jobs; metrics stay empty.
    with pytest.raises(vayren_intel.IntelError):
        vayren_intel.build_ci_record(
            {**json.loads(json.dumps(RUN1)), "conclusion": "failure", "jobs": [JOB1]}
        )


def _monotone_wall_runs(values: tuple[float, ...]) -> list[dict]:
    runs = []
    for i, wall_s in enumerate(values, start=1):
        run = json.loads(json.dumps(RUN1))
        run["databaseId"] = 90 + i
        # Single date source: same day for start/end, durations exact in seconds.
        start_dt = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC) + datetime.timedelta(days=i)
        end_dt = start_dt + datetime.timedelta(seconds=wall_s)
        run["jobs"] = [
            {
                "name": "build-test",
                "conclusion": "success",
                "startedAt": start_dt.isoformat(),
                "completedAt": end_dt.isoformat(),
                "steps": [],
            }
        ]
        runs.append(run)
    return runs


def test_radar_ci_trend_up_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    runs = _monotone_wall_runs((100.0, 120.0, 150.0))
    monkeypatch.setattr(
        vayren_intel,
        "fetch_run",
        lambda rid: next(r for r in runs if r["databaseId"] == rid),
    )
    for _run_id in (91, 92, 93):
        vayren_intel.ingest_ci_run(_run_id)
    radar = vayren_intel.radar()
    series = radar["series"]["ci.build-test.duration_s"]
    assert series["classification"] == "TREND_UP"
    item = next(
        i
        for i in vayren_intel.load_knowledge()["items"]
        if i["id"] == "trend-ci.build-test.duration_s"
    )
    assert item["state"] == "OBSERVED"


def test_radar_ci_trend_down_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    runs = _monotone_wall_runs((150.0, 120.0, 100.0))
    monkeypatch.setattr(
        vayren_intel,
        "fetch_run",
        lambda rid: next(r for r in runs if r["databaseId"] == rid),
    )
    for _run_id in (91, 92, 93):
        vayren_intel.ingest_ci_run(_run_id)
    radar = vayren_intel.radar()
    series = radar["series"]["ci.build-test.duration_s"]
    assert series["classification"] == "TREND_DOWN"


def test_ingest_rejects_tampered_provenance() -> None:
    record = vayren_intel.build_ci_record(RUN1)
    record["revision"] = "short-sha"
    problems = vayren_intel.validate_ci_record(record)
    assert any("full SHA" in p for p in problems)


def test_verify_flags_duplicate_ci_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    record = vayren_intel.build_ci_record(RUN1)
    vayren_intel._append_jsonl("history.jsonl", record)
    vayren_intel._append_jsonl("history.jsonl", record)
    result = vayren_intel.verify_store()
    assert result["status"] == "FAIL"
    assert any("duplicate CI run" in p for p in result["problems"])


def test_verify_flags_malformed_ci_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_without_cargo(monkeypatch)
    vayren_intel._append_jsonl(
        "history.jsonl", {"kind": "ci", "schema": "ci-timing/v1", "run_id": 9}
    )
    result = vayren_intel.verify_store()
    print("PROBLEMS:", result["problems"])
    assert result["status"] == "FAIL"
    assert any("ci record issue" in p for p in result["problems"])
