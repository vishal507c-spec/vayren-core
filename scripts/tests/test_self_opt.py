"""Self-optimizing loop tests (Phase 8 matrix §28).

Store I/O is isolated to tmp dirs (monkeypatched STORE_DIR); only a small
set of live read-only golden runs touches the real warm index. No test
edits production source, weakens validators, or invents measurements.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import self_opt  # noqa: E402

SUBSET = (
    {
        "name": "broker-registry",
        "task_class": "broker",
        "task": "add validation to broker registry",
        "expect_route": "broker-ubl",
    },
    {
        "name": "data-settings",
        "task_class": "data",
        "task": "download settings",
        "expect_route": "download-config",
    },
)


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(self_opt, "STORE_DIR", tmp_path / "optimization")


@pytest.fixture(scope="session", autouse=True)
def _warm_index() -> None:
    self_opt.repo_index.ensure_fresh()


def _seed_records(n: int = 3, **overrides: object) -> list[dict]:
    base: dict = {
        "schema": self_opt.SCHEMA_TELEMETRY,
        "task": "add validation to broker registry",
        "task_class": "broker",
        "task_fingerprint": "fp",
        "params": {"packet_level": "auto", "use_cache": True},
        "result": "PASS",
        "context_bytes": 50000,
        "tool_calls": {"scope_compute": 1},
    }
    base.update(overrides)
    return [dict(base) for _ in range(n)]


def test_telemetry_generation() -> None:
    record = self_opt.run_task(dict(SUBSET[0]))
    assert record["schema"] == self_opt.SCHEMA_TELEMETRY
    assert record["result"] == "PASS"
    assert record["route"] == "broker-ubl"
    assert record["context_bytes"] > 0
    assert record["searches"] == 0
    assert record["tool_calls"]["packet_build"] == 1
    dumped = json.dumps(record)
    assert "snippet" not in dumped and '"body"' not in dumped
    stats = self_opt.record_telemetry(record)
    assert stats["records"] == 1
    assert self_opt._store_path("telemetry.jsonl").is_file()


def test_telemetry_determinism() -> None:
    first = self_opt.run_task(dict(SUBSET[1]), {"packet_level": "auto", "use_cache": True})
    second = self_opt.run_task(dict(SUBSET[1]), {"packet_level": "auto", "use_cache": True})
    for key in ("task_fingerprint", "route", "validation_scope", "validation_commands"):
        assert first[key] == second[key]
    assert self_opt.fingerprint_task(dict(SUBSET[1])) == first["task_fingerprint"]


def test_telemetry_bounded() -> None:
    for _ in range(5):
        self_opt.record_telemetry(_seed_records(1)[0])
    lines = self_opt._store_path("telemetry.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert all(json.loads(line)["schema"] == self_opt.SCHEMA_TELEMETRY for line in lines)


def test_baseline_creation() -> None:
    baseline = self_opt.build_baseline(SUBSET)
    assert baseline["id"] == self_opt.BASELINE_ID
    assert baseline["aggregate"]["tasks"] == 2
    assert baseline["aggregate"]["passed"] == 2
    assert baseline["aggregate"]["searches"] == 0
    assert self_opt.load_baseline() is not None


def test_optimization_candidate_generation() -> None:
    self_opt.build_baseline(SUBSET)
    for _ in range(3):
        for record in _seed_records(1):
            self_opt.record_telemetry(record)
    candidates = self_opt.propose_candidates()
    ids = [c["id"] for c in candidates]
    assert "CTX-L1-broker" in ids
    candidate = next(c for c in candidates if c["id"] == "CTX-L1-broker")
    for field in (
        "problem",
        "evidence",
        "proposed_change",
        "expected_benefit",
        "safety_impact",
        "acceptance_threshold",
        "rollback_condition",
    ):
        assert candidate[field]
    assert candidate["observations"] >= self_opt.MIN_OBSERVATIONS


def test_evidence_requirements() -> None:
    self_opt.build_baseline(SUBSET)
    self_opt.record_telemetry(_seed_records(1)[0])
    candidates = self_opt.propose_candidates()
    assert all(c["id"] != "CTX-L1-broker" for c in candidates)


def test_failure_candidate_proposal() -> None:
    self_opt.build_baseline(SUBSET)
    for _ in range(3):
        self_opt.record_telemetry(
            _seed_records(
                1,
                task="failure-known-unavailable",
                task_class="failure",
                failure_command="python scripts/context.py --check",
                failure_category="INFRASTRUCTURE_FAILURE",
            )[0]
        )
    candidates = self_opt.propose_candidates()
    assert "FAIL-FAST-known" in [c["id"] for c in candidates]


def test_confidence_thresholds() -> None:
    self_opt.build_baseline(SUBSET)
    for _ in range(3):
        for record in _seed_records(1):
            self_opt.record_telemetry(record)
    candidates = self_opt.propose_candidates()
    candidate = next(c for c in candidates if c["id"] == "CTX-L1-broker")
    assert candidate["confidence"] == 1.0
    assert candidate["observations"] >= 3


def test_learned_context_optimization() -> None:
    self_opt.build_baseline(SUBSET)
    store_data = self_opt.load_store()
    candidate = {
        "id": "CTX-L1-broker",
        "type": "context-level",
        "scope": {"task_class": "broker", "packet_level": "L1"},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    self_opt.store_candidate(store_data, candidate)
    self_opt.save_store(store_data)
    evaluation = self_opt.evaluate_candidate(candidate, SUBSET)
    assert evaluation["verdict"] == "ACCEPT"
    assert evaluation["cand_bytes"] < evaluation["base_bytes"]
    accepted = self_opt.accept_candidate(self_opt.load_store(), "CTX-L1-broker", evaluation)
    assert accepted["status"] == "ACCEPTED"
    assert accepted["version"] >= 2


def test_learned_validation_optimization() -> None:
    manifest, _ = self_opt.execute_surface.build_manifest("download settings")
    assert manifest["status"] == "READY"
    store_data = self_opt.load_store()
    first, first_status = self_opt.compute_scope_memoized(manifest, store_data)
    assert first_status == "miss"
    assert first.get("status") in ("VALID", "ESCALATED")
    second, second_status = self_opt.compute_scope_memoized(manifest, store_data)
    assert second_status == "hit"
    assert second["commands"] == first["commands"]
    assert second["scope"] == first["scope"]
    tampered = dict(manifest)
    tampered["hashes"] = dict(manifest.get("hashes", {}))
    tampered_target = next(iter(tampered["hashes"]), "")
    assert tampered_target
    tampered["hashes"][tampered_target] = "0" * 64
    third, third_status = self_opt.compute_scope_memoized(tampered, store_data)
    assert third_status == "miss"


def test_learned_failure_optimization() -> None:
    raw = {
        "kind": "command",
        "command": "python scripts/context.py --check",
        "rc": None,
        "tail": "",
        "note": "validator script missing: scripts/context.py",
        "outcome": "UNAVAILABLE",
    }
    store_data = self_opt.load_store()
    first, first_status = self_opt.recover_memoized(raw, store_data)
    assert first_status == "miss"
    assert first.get("category") == "INFRASTRUCTURE_FAILURE"
    self_opt.save_store(store_data)
    reloaded = self_opt.load_store()
    second, second_status = self_opt.recover_memoized(raw, reloaded)
    assert second_status == "hit"
    assert second["category"] == first["category"]
    assert second["next_action"] == first["next_action"]
    assert second["final"] == first["final"]


def test_canonical_rule_protection() -> None:
    params = self_opt.apply_candidate_params(
        dict(SUBSET[0]),
        {"type": "context-level", "scope": {"task_class": "broker", "packet_level": "L1"}},
    )
    assert set(params) <= set(self_opt.LEARNABLE_KEYS)
    for forbidden in ("route", "owner", "language", "forbidden", "contract"):
        assert forbidden not in params
    with pytest.raises(self_opt.OptError):
        self_opt.apply_candidate_params(
            dict(SUBSET[0]),
            {
                "type": "context-level",
                "scope": {"task_class": "broker", "packet_level": "L9"},
            },
        )


def test_optimization_acceptance() -> None:
    store_data = self_opt.load_store()
    candidate = {
        "id": "CTX-L1-data",
        "type": "context-level",
        "scope": {"task_class": "data", "packet_level": "L1"},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    self_opt.store_candidate(store_data, candidate)
    evaluation = self_opt.evaluate_candidate(candidate, SUBSET)
    assert evaluation["verdict"] == "ACCEPT"
    assert evaluation["violations"] == 0


def test_optimization_rejection() -> None:
    store_data = self_opt.load_store()
    candidate = {
        "id": "CTX-EMPTY",
        "type": "context-level",
        "scope": {"task_class": "no-such-class", "packet_level": "L1"},
        "problem": "p",
        "evidence": "none",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 0,
        "confidence": 0.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    self_opt.store_candidate(store_data, candidate)
    self_opt.save_store(store_data)
    evaluation = self_opt.evaluate_candidate(candidate, SUBSET)
    assert evaluation["verdict"] == "REJECT"
    rejected = self_opt.reject_candidate(self_opt.load_store(), "CTX-EMPTY", evaluation["reason"])
    assert rejected["status"] == "REJECTED"
    assert rejected["reject_reason"]


def test_rollback() -> None:
    store_data = self_opt.load_store()
    candidate = {
        "id": "CTX-L1-broker",
        "type": "context-level",
        "scope": {"task_class": "broker", "packet_level": "L1"},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    self_opt.store_candidate(store_data, candidate)
    self_opt.save_store(store_data)
    evaluation = self_opt.evaluate_candidate(candidate, SUBSET)
    assert evaluation["verdict"] == "ACCEPT"
    self_opt.accept_candidate(self_opt.load_store(), "CTX-L1-broker", evaluation)
    result = self_opt.rollback_candidate(self_opt.load_store(), "CTX-L1-broker", "test rollback")
    assert result["status"] == "ROLLED_BACK"
    assert result["verification_result"] == "PASS"
    assert self_opt.active_candidates(self_opt.load_store()) == []
    with pytest.raises(self_opt.OptError):
        self_opt.rollback_candidate(self_opt.load_store(), "CTX-L1-broker", "twice")


def test_corrupted_optimization_state() -> None:
    self_opt._store_path("").mkdir(parents=True, exist_ok=True)
    self_opt._store_path("optimizations.json").write_text("{not json", encoding="utf-8")
    loaded = self_opt.load_store()
    assert loaded["optimizations"] == {}
    assert list(self_opt._store_path("").glob("optimizations.json.corrupt-*")) != []


def test_stale_optimization() -> None:
    store_data = self_opt.load_store()
    candidate = {
        "id": "OLD-OPT",
        "type": "context-level",
        "scope": {"task_class": "broker", "packet_level": "L1"},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "ACCEPTED",
        "created_from": "BASELINE_V0",
    }
    self_opt.store_candidate(store_data, candidate)
    self_opt.save_store(store_data)
    assert self_opt.active_candidates(self_opt.load_store()) == []


def test_deterministic_optimization_state() -> None:
    self_opt.build_baseline(SUBSET)
    for _ in range(3):
        for record in _seed_records(1):
            self_opt.record_telemetry(record)
    first = self_opt.propose_candidates()
    second = self_opt.propose_candidates()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_ab_benchmark() -> None:
    candidate = {
        "id": "CTX-L1-broker",
        "type": "context-level",
        "scope": {"task_class": "broker", "packet_level": "L1"},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    evaluation = self_opt.evaluate_candidate(candidate, SUBSET)
    assert set(evaluation) >= {
        "candidate",
        "verdict",
        "reason",
        "violations",
        "base_bytes",
        "cand_bytes",
        "comparisons",
        "benchmark_ms",
    }
    assert len(evaluation["comparisons"]) == 1
    assert evaluation["comparisons"][0]["task"] == "broker-registry"


def test_no_correctness_regression() -> None:
    candidate = {
        "id": "CTX-L1-broker",
        "type": "context-level",
        "scope": {"task_class": "broker", "packet_level": "L1"},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    evaluation = self_opt.evaluate_candidate(candidate, SUBSET)
    assert all(item["cand_result"] == "PASS" for item in evaluation["comparisons"])
    assert all(item["base_result"] == "PASS" for item in evaluation["comparisons"])


def test_zero_search_preservation() -> None:
    source = (self_opt.SCRIPTS_DIR / "self_opt.py").read_text(encoding="utf-8")
    for token in ("rglob", ".glob(", "os.walk(", "build_graph(", "repo_index.refresh("):
        assert token not in source, token
    record = self_opt.run_task(dict(SUBSET[0]))
    assert record["searches"] == 0


def test_cache_correctness_preservation() -> None:
    manifest, _ = self_opt.execute_surface.build_manifest("download settings")
    assert manifest["status"] == "READY"
    store_data = self_opt.load_store()
    first, first_status = self_opt.compute_scope_memoized(manifest, store_data)
    assert first_status == "miss"
    second, second_status = self_opt.compute_scope_memoized(manifest, store_data)
    assert second_status == "hit"
    assert second["commands"] == first["commands"]
    assert second["scope"] == first["scope"]
    tampered = dict(manifest)
    tampered["hashes"] = dict(manifest.get("hashes", {}))
    first_target = next(iter(tampered["hashes"]), "")
    assert first_target
    tampered["hashes"][first_target] = "0" * 64
    third, third_status = self_opt.compute_scope_memoized(tampered, store_data)
    assert third_status == "miss"


def test_safety_boundary_preservation() -> None:
    base = {
        "result": "PASS",
        "route": "broker-ubl",
        "owner": "BROKER_CONTRACT",
        "language": ["PYTHON"],
        "forbidden": ["05_strategy/strategy/*"],
        "validation_commands": ["pytest x -q"],
        "target_files": ["a.py"],
    }
    tampered = dict(base, route="strategy-change")
    violations = self_opt.verify_safety(base, tampered)
    assert any(v.startswith("route:") for v in violations)
    lost = dict(base, target_files=[])
    assert any("coverage" in v for v in self_opt.verify_safety(base, lost))
    assert self_opt.verify_safety(base, dict(base)) == []


def test_benchmark_suite_contract() -> None:
    suite = self_opt.benchmark_suite(SUBSET)
    assert suite["aggregate"]["tasks"] == 2
    assert suite["aggregate"]["passed"] == 2
    assert suite["aggregate"]["searches"] == 0
    assert set(suite["tasks"]) == {"broker-registry", "data-settings"}


def test_report_contract() -> None:
    self_opt.build_baseline(SUBSET)
    report = self_opt.diagnostic_report()
    assert report["baseline"] == self_opt.BASELINE_ID
    assert report["suite_tasks"] >= 2
    assert set(report) >= {
        "baseline",
        "optimizations",
        "accepted",
        "rejected",
        "rollbacks",
        "regressions",
        "current_context_bytes",
    }


FAILURE_SUBSET = (
    {
        "name": "failure-known-unavailable",
        "task_class": "failure",
        "failure_command": "python scripts/context.py --check",
        "expect_category": "INFRASTRUCTURE_FAILURE",
    },
)


def test_scope_memo_evaluation() -> None:
    candidate = {
        "id": "SCOPE-REUSE",
        "type": "scope-memo",
        "scope": {},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    evaluation = self_opt.evaluate_candidate(candidate, SUBSET)
    assert evaluation["metric_name"] == "scope_ms"
    assert evaluation["violations"] == 0
    assert evaluation["verdict"] == "ACCEPT"
    assert evaluation["metric_cand"] < evaluation["metric_base"]


def test_decision_memo_evaluation() -> None:
    candidate = {
        "id": "FAIL-FAST-known",
        "type": "decision-memo",
        "scope": {},
        "problem": "p",
        "evidence": "e",
        "proposed_change": "c",
        "expected_benefit": "b",
        "safety_impact": "s",
        "acceptance_threshold": "t",
        "rollback_condition": "r",
        "observations": 3,
        "confidence": 1.0,
        "status": "PROPOSED",
        "created_from": self_opt.BASELINE_ID,
    }
    scoped_out = self_opt.evaluate_candidate(candidate, SUBSET)
    assert scoped_out["verdict"] == "REJECT"
    assert scoped_out["reason"] == "no failure tasks in scope"
    evaluation = self_opt.evaluate_candidate(candidate, FAILURE_SUBSET)
    assert evaluation["metric_name"] == "decision_ms"
    assert evaluation["violations"] == 0
    assert evaluation["verdict"] == "ACCEPT"
    assert evaluation["metric_cand"] < evaluation["metric_base"]


def test_unknown_candidate_type_rejected() -> None:
    evaluation = self_opt.evaluate_candidate({"id": "X", "type": "telepathy"})
    assert evaluation["verdict"] == "REJECT"
