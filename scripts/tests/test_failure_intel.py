"""Predictive failure recovery tests (Phase 7 matrix §22 + benchmarks §20).

Most cases use synthetic command output (fast and deterministic). A small set
of live checks proves the known signatures against real repository behavior:
the ambient FYERS failure, the missing context.py validator, a real timeout,
a real missing target, a real stale-index refresh cycle, real packet/scope
recovery, a real transient retry, a real escalated scope, and the real cargo
fmt baseline. Live tree mutations restore byte-identical content.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import execute_surface  # noqa: E402
import failure_intel  # noqa: E402
import repo_index  # noqa: E402
from failure_intel import (  # noqa: E402
    NEXT_ACTIONS,
    RecoveryChain,
    attribute,
    benchmark,
    classify,
    decide,
    escalation_target,
    evidence_fingerprint,
    normalize,
    precheck,
    recover,
    retry_budget,
    signature_lookup,
    signature_record,
)

ROOT = SCRIPTS_DIR.parent
FYERS_COMMAND = (
    "pytest 02_data/data/tests/test_fyers_provider.py::"
    "test_reload_credentials_falls_back_to_layered_loader -q"
)
FYERS_TAIL = (
    "E       AssertionError: assert 'H8TZYVITK2-200' == 'A-NEW'\n"
    "FAILED 02_data/data/tests/test_fyers_provider.py::"
    "test_reload_credentials_falls_back_to_layered_loader\n"
    "02_data\\data\\tests\\test_fyers_provider.py:136: AssertionError"
)
BROKER_TEST = "09_broker/broker/tests/test_broker_contracts.py::test_contract_shape"
CHANGED_BROKER = ["09_broker/broker/registry.py"]
CHANGED_ROUTE = ["scripts/route.py"]


@pytest.fixture(scope="session", autouse=True)
def _warm_index() -> None:
    """Heal the repo index once: staleness probes below assume READY.

    The full suite warms this incidentally via earlier modules; sharded or
    single-file runs start cold (REBUILD_REQUIRED) without it.
    """
    _heal_index()


def _heal_index() -> None:
    repo_index.ensure_fresh()


class TreeEdit:
    """One live-tree mutation with guaranteed byte-identical restore."""

    def __init__(self) -> None:
        self.originals: dict[str, bytes] = {}

    def append(self, relpath: str, extra: str) -> None:
        path = ROOT / relpath
        if relpath not in self.originals:
            self.originals[relpath] = path.read_bytes()
        ending = "\r\n" if b"\r\n" in self.originals[relpath] else "\n"
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(extra.replace("\n", ending))

    def restore(self) -> None:
        for relpath, content in self.originals.items():
            (ROOT / relpath).write_bytes(content)
        for relpath, content in self.originals.items():
            assert (ROOT / relpath).read_bytes() == content, relpath
        self.originals.clear()


@pytest.fixture()
def tree() -> Iterator[TreeEdit]:
    edit = TreeEdit()
    try:
        yield edit
    finally:
        edit.restore()
        repo_index.refresh()


def _command(
    command: str,
    rc: int | None,
    tail: str = "",
    outcome: str = "FAIL",
    note: str = "",
) -> dict:
    return {
        "kind": "command",
        "command": command,
        "rc": rc,
        "tail": tail,
        "note": note,
        "outcome": outcome,
    }


def _without_timing(result: dict) -> dict:
    cleaned = dict(result)
    cleaned.pop("timing_ms", None)
    cleaned.pop("chain", None)
    return cleaned


CATEGORY_CASES = [
    (
        "code",
        _command(
            "ruff check scripts/route.py",
            1,
            "F401 `os` imported but unused\n --> scripts/route.py:1:8\n",
        ),
        {"changed": CHANGED_ROUTE},
        "CODE_FAILURE",
        "source_code",
        "REPORT_CODE_FAILURE",
        "CODE_FAILURE",
    ),
    (
        "test",
        _command(
            f"pytest {BROKER_TEST} -q",
            1,
            f"FAILED {BROKER_TEST} - AssertionError\n"
            "09_broker/broker/tests/test_broker_contracts.py:25: AssertionError\n",
        ),
        {"changed": CHANGED_BROKER},
        "TEST_FAILURE",
        "test",
        "REPORT_CODE_FAILURE",
        "CODE_FAILURE",
    ),
    (
        "validation",
        _command("pyright", 1, "48 errors, 0 warnings, 0 informations\n"),
        {},
        "VALIDATION_FAILURE",
        "repository_infrastructure",
        "STOP",
        "STOPPED",
    ),
    (
        "syntax",
        _command(
            "ruff check scripts/route.py",
            1,
            "E999 SyntaxError: invalid syntax\n --> scripts/route.py:1:1\n",
        ),
        {"changed": CHANGED_ROUTE},
        "SYNTAX_FAILURE",
        "source_code",
        "REPORT_CODE_FAILURE",
        "CODE_FAILURE",
    ),
    (
        "import",
        _command(
            "pytest 09_broker/broker/tests -q",
            1,
            "ERROR collecting 09_broker/broker/tests/test_x.py\n"
            "ModuleNotFoundError: No module named 'missing_dep'\n",
        ),
        {},
        "IMPORT_FAILURE",
        "test",
        "REPORT_CODE_FAILURE",
        "CODE_FAILURE",
    ),
    (
        "contract",
        _command(
            "python scripts/validate_authority.py",
            1,
            "Authority validation FAILED (1 error):\n  - scripts/route.py\n",
        ),
        {"changed": CHANGED_ROUTE},
        "CONTRACT_FAILURE",
        "source_code",
        "REPORT_CODE_FAILURE",
        "CODE_FAILURE",
    ),
    (
        "ownership",
        {
            "kind": "surface",
            "status": "BLOCKED",
            "reason": "ownership gate: 09_broker/broker/registry.py is must_not_change",
        },
        {},
        "OWNERSHIP_FAILURE",
        "route",
        "STOP",
        "BLOCKED",
    ),
    (
        "language",
        _command(
            "python scripts/validate_language_ownership.py",
            1,
            "Language ownership validation FAILED (1 error):\n  - scripts/route.py\n",
        ),
        {"changed": CHANGED_ROUTE},
        "LANGUAGE_FAILURE",
        "source_code",
        "REPORT_CODE_FAILURE",
        "CODE_FAILURE",
    ),
    (
        "stale",
        {"kind": "freshness", "status": "STALE", "reason": "3 source file(s) changed"},
        {},
        "STALE_STATE",
        "graph_index",
        "REFRESH_INDEX",
        "RETRYING",
    ),
    (
        "concurrent",
        {
            "kind": "surface",
            "status": "STALE",
            "reason": "STALE_EXECUTION_SURFACE: concurrent modification",
        },
        {},
        "CONCURRENT_MODIFICATION",
        "execution_surface",
        "REGENERATE_EXECUTION_SURFACE",
        "STOPPED",
    ),
    (
        "missing_target",
        {
            "kind": "surface",
            "status": "STALE",
            "reason": "STALE_EXECUTION_SURFACE: target deleted",
        },
        {},
        "MISSING_TARGET",
        "graph_index",
        "REFRESH_INDEX",
        "RETRYING",
    ),
    (
        "missing_test",
        _command(
            "pytest 09_broker/broker/tests/test_ghost.py -q",
            5,
            "collected 0 items\nno tests ran\n",
        ),
        {},
        "MISSING_TEST",
        "test",
        "REFRESH_INDEX",
        "RETRYING",
    ),
    (
        "unavailable",
        _command(
            "python scripts/validate_missing.py",
            None,
            "",
            outcome="UNAVAILABLE",
            note="validator script missing: scripts/validate_missing.py",
        ),
        {},
        "VALIDATION_UNAVAILABLE",
        "repository_infrastructure",
        "ESCALATE_VALIDATION",
        "ESCALATED",
    ),
    (
        "environment",
        _command("pytest x -q", None, "", outcome="ENV_FAIL", note="runner error: boom"),
        {},
        "ENVIRONMENT_FAILURE",
        "environment",
        "REPORT_ENVIRONMENT",
        "ENVIRONMENT_FAILURE",
    ),
    (
        "toolchain",
        _command(
            "cargo fmt --check",
            1,
            "error: rustfmt failed internally: unexpected condition\n",
        ),
        {},
        "TOOLCHAIN_FAILURE",
        "toolchain",
        "STOP",
        "STOPPED",
    ),
    (
        "timeout",
        _command("pytest 02_data/data/tests -q", None, "timed out after 240s", outcome="TIMEOUT"),
        {},
        "TIMEOUT",
        "environment",
        "ESCALATE_VALIDATION",
        "ESCALATED",
    ),
    (
        "infrastructure",
        _command(
            "python scripts/context.py --check",
            None,
            "",
            outcome="UNAVAILABLE",
            note="validator script missing: scripts/context.py",
        ),
        {},
        "INFRASTRUCTURE_FAILURE",
        "repository_infrastructure",
        "STOP",
        "STOPPED",
    ),
    (
        "unknown",
        _command("frobnicate --warp 9", 42, "wibbly wobbly drive disengaged\n"),
        {},
        "UNKNOWN",
        "repository_infrastructure",
        "ESCALATE_VALIDATION",
        "UNKNOWN",
    ),
]


@pytest.mark.parametrize(
    "name,raw,context,category,ownership,next_action,final",
    CATEGORY_CASES,
    ids=[case[0] for case in CATEGORY_CASES],
)
def test_taxonomy_categories(
    name: str,
    raw: dict,
    context: dict,
    category: str,
    ownership: str,
    next_action: str,
    final: str,
) -> None:
    result = recover(raw, context=context)
    assert result["category"] == category, name
    assert result["ownership"] == ownership, name
    assert result["next_action"] == next_action, name
    assert result["next_action"] in NEXT_ACTIONS, name
    assert result["final"] == final, name
    assert result["evidence"], name
    assert result["confidence"] > 0, name


def test_benchmark_matrix_contract() -> None:
    result = benchmark()
    cases = result["cases"]
    expected = {
        "01_environment_fyers": (
            "ENVIRONMENT_FAILURE",
            "REPORT_ENVIRONMENT",
            "ENVIRONMENT_FAILURE",
        ),
        "02_code_test": ("TEST_FAILURE", "REPORT_CODE_FAILURE", "CODE_FAILURE"),
        "03_stale_packet": ("STALE_STATE", "REFRESH_PACKET", "RETRYING"),
        "04_stale_index": ("STALE_STATE", "REFRESH_INDEX", "RETRYING"),
        "05_concurrent": ("CONCURRENT_MODIFICATION", "REGENERATE_EXECUTION_SURFACE", "STOPPED"),
        "06_missing_target": ("MISSING_TARGET", "REFRESH_INDEX", "RETRYING"),
        "07_missing_test": ("MISSING_TEST", "REFRESH_INDEX", "RETRYING"),
        "08_unavailable": ("INFRASTRUCTURE_FAILURE", "STOP", "STOPPED"),
        "09_timeout": ("TIMEOUT", "ESCALATE_VALIDATION", "ESCALATED"),
        "10_cross_module": ("TEST_FAILURE", "REPORT_CODE_FAILURE", "CODE_FAILURE"),
        "11_cross_language": ("TEST_FAILURE", "REPORT_CODE_FAILURE", "CODE_FAILURE"),
        "12_repeated": ("TEST_FAILURE", "STOP", "STOPPED"),
        "13_validator_inconsistent": ("VALIDATION_FAILURE", "ESCALATE_VALIDATION", "ESCALATED"),
        "14_incremental_full_mismatch": (
            "VALIDATION_FAILURE",
            "ESCALATE_VALIDATION",
            "ESCALATED",
        ),
        "15_unknown": ("UNKNOWN", "ESCALATE_VALIDATION", "UNKNOWN"),
    }
    assert set(cases) == set(expected)
    for name, (category, action, final) in expected.items():
        assert cases[name]["category"] == category, name
        assert cases[name]["next_action"] == action, name
        assert cases[name]["final"] == final, name
        assert cases[name]["evidence_items"] > 0, name
    assert result["counts"]["cases"] == 15
    assert set(result["timing_ms"]) == {
        "classify_cold",
        "classify_warm",
        "decide",
        "precheck",
        "escalate",
        "sig_cache_cold",
        "sig_cache_hit",
    }


def test_validation_apply_threads_revalidate(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def fake_escalate(decision: dict, context: dict) -> dict:
        seen.append(context)
        assert decision["next_action"] == "ESCALATE_VALIDATION"
        return {"ok": True, "escalated_to": "L3", "commands": []}

    monkeypatch.setitem(failure_intel.ACTION_EXECUTORS, "ESCALATE_VALIDATION", fake_escalate)
    result = recover(
        {
            "kind": "validation",
            "status": "FAILED",
            "results": [{"command": "pytest x -q", "outcome": "TIMEOUT", "rc": None}],
        },
        context={"scope": "L2"},
        apply=True,
        revalidate=True,
    )
    assert seen and seen[0].get("revalidate") is True
    assert result["final"] == "ESCALATED"


def test_fyers_stays_environment_even_when_other_code_changed() -> None:
    result = recover(
        _command(FYERS_COMMAND, 1, FYERS_TAIL),
        context={"changed": CHANGED_BROKER},
    )
    assert result["category"] == "ENVIRONMENT_FAILURE"
    assert result["subtype"] == "fyers_ambient_credentials"
    assert result["ownership"] == "environment"
    assert result["next_action"] == "REPORT_ENVIRONMENT"
    assert result["final"] == "ENVIRONMENT_FAILURE"
    assert result["retryable"] is False
    assert "02_data/data/tests/test_fyers_provider.py" in result["affected_files"]


def test_pyright_error_inside_changed_set_is_code_failure() -> None:
    tail = (
        "09_broker/broker/registry.py:10:5 - error: expression is unknown\n1 errors, 0 warnings\n"
    )
    result = recover(_command("pyright", 1, tail), context={"changed": CHANGED_BROKER})
    assert result["category"] == "CODE_FAILURE"
    assert result["ownership"] == "source_code"
    assert result["next_action"] == "REPORT_CODE_FAILURE"


def test_rustfmt_drift_outside_changed_set_is_not_code_failure() -> None:
    tail = "Diff in rust/vayren-shell/src/broker_connection.rs:109:\n- x\n+ y\n"
    result = recover(_command("cargo fmt --check", 1, tail), context={"changed": CHANGED_BROKER})
    assert result["category"] == "VALIDATION_FAILURE"
    assert result["subtype"] == "rustfmt_baseline_drift"
    assert result["ownership"] == "repository_infrastructure"
    assert result["next_action"] == "STOP"


def test_crashed_validator_owns_its_failure() -> None:
    tail = "Traceback (most recent call last):\n  File validation\nValueError: boom\n"
    result = recover(_command("python scripts/validate_authority.py", 1, tail))
    assert result["category"] == "VALIDATION_FAILURE"
    assert result["ownership"] == "validation_rule"
    assert result["next_action"] == "ESCALATE_VALIDATION"


def test_retry_budgets_are_bounded() -> None:
    stale = normalize({"kind": "freshness", "status": "STALE", "reason": "x changed"})
    classify(stale)
    attribute(stale)
    assert stale["retryable"] is True
    assert retry_budget(stale) == 1

    timeout = normalize(_command("pytest x -q", None, "timed out", outcome="TIMEOUT"))
    classify(timeout)
    attribute(timeout)
    assert timeout["retryable"] is False
    assert retry_budget(timeout) == 0

    code = normalize(_command("ruff check scripts/route.py", 1, "F401\n"))
    classify(code, {"changed": CHANGED_ROUTE})
    attribute(code, {"changed": CHANGED_ROUTE})
    assert retry_budget(code) == 0


def test_recovery_loop_detection_stops() -> None:
    raw = _command("pytest x -q", 1, "FAILED x.py::test_y - assert False\n")
    chain = RecoveryChain()
    record = normalize(raw)
    classify(record)
    attribute(record)
    fingerprint = evidence_fingerprint(record)
    chain.record(
        attempt=1,
        command="pytest x -q",
        category="TEST_FAILURE",
        subtype="assertion_failed",
        evidence_fp=fingerprint,
        recovery="REPORT_CODE_FAILURE",
        result="CODE_FAILURE",
    )
    result = recover(raw, chain=chain)
    assert result["next_action"] == "STOP"
    assert result["final"] == "STOPPED"
    assert "RECOVERY_LOOP_DETECTED" in result["reason"]


def test_stale_repeat_escalates_after_budget_spent() -> None:
    chain = RecoveryChain()
    prior = normalize({"kind": "freshness", "status": "STALE", "reason": "prior probe changed"})
    classify(prior)
    attribute(prior)
    chain.record(
        attempt=1,
        command="",
        category="STALE_STATE",
        subtype="stale_index",
        evidence_fp=evidence_fingerprint(prior),
        recovery="REFRESH_INDEX",
        result="FAILED",
    )
    result = recover(
        {"kind": "freshness", "status": "STALE", "reason": "new probe changed"},
        context={"scope": "L1"},
        chain=chain,
    )
    assert result["next_action"] == "ESCALATE_VALIDATION"
    assert result["final"] == "ESCALATED"
    assert result["escalation_target"] == "L2"


def test_escalation_targets_are_deterministic() -> None:
    cross_module = {
        "category": "TEST_FAILURE",
        "subtype": "assertion_failed",
        "impact": {"affected_modules": ["broker", "data"]},
    }
    assert (
        escalation_target("L1", cross_module, {"impact": {"affected_modules": ["broker", "data"]}})
        == "L4"
    )
    cross_language = {
        "category": "TEST_FAILURE",
        "subtype": "assertion_failed",
        "impact": {"affected_modules": ["core"], "languages": ["python", "rust"]},
    }
    assert escalation_target("L1", cross_language, {"impact": cross_language["impact"]}) == "L4"
    unknown = {"category": "UNKNOWN", "subtype": "unmatched_output"}
    assert escalation_target("L2", unknown, {}) == "L3"
    code = {"category": "CODE_FAILURE", "subtype": "ruff_lint"}
    assert escalation_target("L2", code, {}) is None


def test_incremental_full_mismatch_goes_l5() -> None:
    result = recover(
        {
            "kind": "comparison",
            "mode": "incremental_full",
            "mismatches": ["pytest broker: incr=PASS full=FAIL"],
        }
    )
    assert result["category"] == "VALIDATION_FAILURE"
    assert result["subtype"] == "incremental_full_mismatch"
    assert result["next_action"] == "ESCALATE_VALIDATION"
    assert result["escalation_target"] == "L5"


def test_validator_inconsistency_goes_l5() -> None:
    result = recover(
        {"kind": "comparison", "mode": "repeat", "mismatches": ["ruff: pass then fail"]}
    )
    assert result["subtype"] == "validator_inconsistent"
    assert result["escalation_target"] == "L5"


def test_recovery_is_deterministic() -> None:
    raw = _command(
        f"pytest {BROKER_TEST} -q",
        1,
        f"FAILED {BROKER_TEST} - AssertionError\n",
    )
    first = _without_timing(recover(raw, context={"changed": CHANGED_BROKER}))
    second = _without_timing(recover(raw, context={"changed": CHANGED_BROKER}))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_chain_serialization_has_no_timestamps() -> None:
    chain = RecoveryChain()
    chain.record(
        attempt=1,
        command="pytest x -q",
        category="TEST_FAILURE",
        subtype="assertion_failed",
        evidence_fp="fp-one",
        recovery="REPORT_CODE_FAILURE",
        result="CODE_FAILURE",
    )
    dumped = chain.to_dict()
    assert json.loads(json.dumps(dumped)) == dumped
    for step in dumped["steps"]:
        assert "timestamp" not in step
        assert "time" not in step
        assert "date" not in step
    twin = RecoveryChain()
    twin.record(
        attempt=1,
        command="pytest x -q",
        category="TEST_FAILURE",
        subtype="assertion_failed",
        evidence_fp="fp-one",
        recovery="REPORT_CODE_FAILURE",
        result="CODE_FAILURE",
    )
    assert twin.chain_id() == chain.chain_id()


def test_precheck_predictions() -> None:
    missing = "pytest 02_data/data/tests/test_missing_xyz.py -q"
    result = precheck(
        ["ruff check scripts/route.py", missing, "python scripts/validate_missing.py"],
        {},
    )
    by_command = {item["command"]: item for item in result["predictions"]}
    assert result["runnable"] == ["ruff check scripts/route.py"]
    assert by_command[missing]["skip"] is True
    assert by_command[missing]["predicted_category"] == "MISSING_TARGET"
    assert by_command["python scripts/validate_missing.py"]["predicted_category"] in (
        "VALIDATION_UNAVAILABLE",
        "INFRASTRUCTURE_FAILURE",
    )


def test_precheck_detects_proven_fyers_ambient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAYREN_FYERS_APP_ID", "PRECHECK-PROBE")
    result = precheck([FYERS_COMMAND], {})
    assert result["skipped"] == [FYERS_COMMAND]
    assert result["predictions"][0]["predicted_category"] == "ENVIRONMENT_FAILURE"
    assert result["predictions"][0]["next_action"] == "REPORT_ENVIRONMENT"


def test_precheck_detects_stale_manifest() -> None:
    probe = repo_index.freshness()
    assert probe["status"] == "READY"
    manifest = {
        "schema": execute_surface.MANIFEST_SCHEMA,
        "status": "READY",
        "index_fingerprint": probe.get("fingerprint"),
        "hashes": {"scripts/route.py": "0" * 64},
    }
    result = precheck(["ruff check scripts/route.py"], {"manifest": manifest})
    assert result["skipped"] == ["ruff check scripts/route.py"]
    assert result["predictions"][0]["predicted_category"] == "CONCURRENT_MODIFICATION"


def test_signature_cache_never_overrides_live_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        failure_intel,
        "_sig_cache_load",
        lambda: {
            "unit-conflict": {
                "key": "unit-conflict",
                "evidence_pattern": "H8TZYVITK2-200",
                "category": "CODE_FAILURE",
                "subtype": "unit_wrong",
                "ownership": "source_code",
                "retry_policy": "never",
                "escalation_policy": "none",
            }
        },
    )
    cached = signature_lookup("saw H8TZYVITK2-200 in output", "pytest x -q")
    assert cached is not None and cached["source"] == "cache"
    result = recover(_command(FYERS_COMMAND, 1, FYERS_TAIL))
    assert result["category"] == "ENVIRONMENT_FAILURE"


def test_signature_cache_fills_only_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        failure_intel,
        "_sig_cache_load",
        lambda: {
            "unit-timeout": {
                "key": "unit-timeout",
                "evidence_pattern": "wibbly wobbly",
                "category": "TIMEOUT",
                "subtype": "unit_pattern",
                "ownership": "environment",
                "retry_policy": "never",
                "escalation_policy": "plus-one",
            }
        },
    )
    result = recover(_command("frobnicate --warp 9", 42, "wibbly wobbly drive disengaged\n"))
    assert result["category"] == "TIMEOUT"
    assert any(item["type"] == "signature" for item in result["evidence"])


def test_signature_record_validation() -> None:
    with pytest.raises(failure_intel.IntelError):
        signature_record({"key": "", "evidence_pattern": "x", "category": "TIMEOUT"})
    with pytest.raises(failure_intel.IntelError):
        signature_record({"key": "bad", "evidence_pattern": "x", "category": "NOT_A_CATEGORY"})


def test_success_input_recovers_without_action() -> None:
    result = recover({"kind": "command", "command": "ruff check x", "rc": 0, "outcome": "PASS"})
    assert result["final"] == "RECOVERED"
    assert result["next_action"] is None


def test_no_source_modification_during_diagnosis() -> None:
    watched = [
        "scripts/failure_intel.py",
        "scripts/route.py",
        "09_broker/broker/registry.py",
    ]
    before = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in watched}
    result = recover(
        {"kind": "freshness", "status": "STALE", "reason": "unit probe changed"},
        apply=True,
    )
    after = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in watched}
    assert before == after
    assert result["next_action"] == "REFRESH_INDEX"
    assert result["chain"]["steps"]


def test_live_fyers_failure_classification() -> None:
    completed = execute_surface.run_validation(
        {"validate": {"commands": [FYERS_COMMAND]}}, timeout_s=60
    )
    entry = completed["results"][0]
    assert entry["command"] == FYERS_COMMAND
    if entry["rc"] == 0:
        pytest.skip("ambient FYERS credential issue is absent in this environment")
    result = recover(_command(FYERS_COMMAND, entry["rc"], entry.get("tail", ""), outcome="FAIL"))
    assert result["category"] == "ENVIRONMENT_FAILURE"
    assert result["ownership"] == "environment"
    assert result["final"] == "ENVIRONMENT_FAILURE"


def test_live_missing_context_py_classification() -> None:
    verified = execute_surface._verify_command("python scripts/context.py --check")
    assert verified["available"] is False
    result = recover(
        _command(
            "python scripts/context.py --check",
            None,
            "",
            outcome="UNAVAILABLE",
            note=verified.get("reason", ""),
        )
    )
    assert result["category"] == "INFRASTRUCTURE_FAILURE"
    assert result["next_action"] == "STOP"
    assert result["final"] == "STOPPED"
    assert result["replacement"] is None or result["replacement"]["auto_applied"] is False


def test_live_timeout_classification() -> None:
    command = "python -m http.server 0"
    completed = execute_surface.run_validation({"validate": {"commands": [command]}}, timeout_s=2)
    entry = completed["results"][0]
    assert entry["rc"] is None
    result = recover(
        _command(command, None, entry.get("note", ""), outcome="TIMEOUT"),
        context={"scope": "L2"},
    )
    assert result["category"] == "TIMEOUT"
    assert result["next_action"] == "ESCALATE_VALIDATION"
    assert result["escalation_target"] == "L3"


def test_live_missing_target_recovery() -> None:
    missing = "pytest 02_data/data/tests/test_missing_xyz.py -q"
    predicted = precheck([missing], {})
    assert predicted["skipped"] == [missing]
    result = recover(
        {
            "kind": "surface",
            "status": "STALE",
            "reason": "STALE_EXECUTION_SURFACE: target deleted",
        },
        apply=True,
    )
    assert result["category"] == "MISSING_TARGET"
    assert result["next_action"] == "REFRESH_INDEX"
    assert result["chain"]["steps"]
    assert repo_index.freshness()["status"] == "READY"


def test_live_stale_index_recovery(tree: TreeEdit) -> None:
    tree.append("09_broker/broker/registry.py", "\n# phase7 stale-index probe\n")
    probe = repo_index.freshness()
    assert probe["status"] == "STALE"
    result = recover(
        {"kind": "freshness", "status": probe["status"], "reason": probe["reason"]},
        apply=True,
    )
    assert result["category"] == "STALE_STATE"
    assert result["next_action"] == "REFRESH_INDEX"
    assert result["final"] == "RETRYING"
    assert repo_index.freshness()["status"] == "READY"


def test_live_packet_refresh_recovery() -> None:
    result = recover(
        {"kind": "surface", "status": "STALE", "reason": "target changed during packet build"},
        context={
            "packet_args": {
                "task": "inspect broker registry",
                "file": "09_broker/broker/registry.py",
            }
        },
        apply=True,
    )
    assert result["category"] == "STALE_STATE"
    assert result["next_action"] == "REFRESH_PACKET"
    assert result["final"] == "RETRYING"


def test_live_scope_recompute_recovery() -> None:
    manifest, _ = execute_surface.build_manifest("add validation to broker registry")
    assert manifest["status"] == "READY"
    result = recover(
        {"kind": "validation", "status": "STALE", "reason": "tree moved after scope computation"},
        context={"manifest": manifest},
        apply=True,
    )
    assert result["category"] == "STALE_STATE"
    assert result["next_action"] == "RECOMPUTE_SCOPE"
    assert result["final"] == "RETRYING"


def test_live_transient_retry_recovery() -> None:
    result = recover(
        _command("ruff check scripts/route.py", 1, "Connection reset by peer\n"),
        context={"timeout_s": 60},
        apply=True,
    )
    assert result["category"] == "ENVIRONMENT_FAILURE"
    assert result["next_action"] == "RETRY_VALIDATION"
    assert result["final"] == "RECOVERED"


def test_live_escalated_scope_computation() -> None:
    manifest, _ = execute_surface.build_manifest("add validation to broker registry")
    assert manifest["status"] == "READY"
    result = recover(
        _command("pytest 02_data/data/tests -q", None, "timed out", outcome="TIMEOUT"),
        context={"scope": "L2", "manifest": manifest, "timeout_s": 120},
        apply=True,
    )
    assert result["category"] == "TIMEOUT"
    assert result["next_action"] == "ESCALATE_VALIDATION"
    assert result["escalation_target"] == "L3"
    assert result["final"] == "ESCALATED"


def test_live_surface_regeneration_path() -> None:
    result = recover(
        {
            "kind": "surface",
            "status": "STALE",
            "reason": "STALE_EXECUTION_SURFACE: concurrent modification",
        },
        context={"task": "add validation to broker registry"},
        apply=True,
    )
    assert result["category"] == "CONCURRENT_MODIFICATION"
    assert result["next_action"] == "REGENERATE_EXECUTION_SURFACE"
    assert result["final"] == "RETRYING"


def test_live_cargo_fmt_baseline_classification() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not installed")
    proc = subprocess.run(
        ["cargo", "fmt", "--check"],
        cwd=ROOT / "rust",
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (proc.stdout + proc.stderr)[-2000:]
    if proc.returncode == 0:
        result = recover(
            {"kind": "command", "command": "cargo fmt --check", "rc": 0, "outcome": "PASS"}
        )
        assert result["final"] == "RECOVERED"
        return
    result = recover(
        _command("cargo fmt --check", proc.returncode, output),
        context={"changed": CHANGED_BROKER},
    )
    assert result["category"] in ("VALIDATION_FAILURE", "TOOLCHAIN_FAILURE", "CODE_FAILURE")
    assert result["next_action"] in ("STOP", "REPORT_CODE_FAILURE")
    assert result["final"] in ("STOPPED", "CODE_FAILURE")


def test_cli_command_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = failure_intel.main(
        [
            "--command",
            f"pytest {BROKER_TEST} -q",
            "--rc",
            "1",
            "--tail",
            f"FAILED {BROKER_TEST} - AssertionError\n",
            "--outcome",
            "FAIL",
            "--json",
        ]
    )
    out, _ = capsys.readouterr()
    assert rc == 1
    assert json.loads(out)["category"] == "TEST_FAILURE"


def test_cli_precheck_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = failure_intel.main(["--precheck", "--commands", "ruff check scripts/route.py", "--json"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert json.loads(out)["runnable"] == ["ruff check scripts/route.py"]


def test_decide_and_attribute_are_directly_usable() -> None:
    record = normalize(_command("frobnicate --warp 9", 42, "wibbly\n"))
    classify(record)
    attribute(record)
    decision = decide(record, {"scope": "L2"}, RecoveryChain())
    assert decision["category"] == "UNKNOWN"
    assert decision["next_action"] == "ESCALATE_VALIDATION"
    assert decision["escalation_target"] == "L3"


def test_warm_index_heals_before_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[None] = []
    monkeypatch.setattr(repo_index, "ensure_fresh", lambda: calls.append(None))
    _heal_index()
    assert calls == [None]
