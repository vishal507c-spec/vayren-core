"""Task report: §25 sections computed from evidence, plus self-check."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.migration.change_gate import check_changes
from scripts.migration.task_report import build_task_report

from .test_change_gate import POLICY, _git


def _mkrepo(root: Path) -> Path:
    (root / "90_brain").mkdir(parents=True, exist_ok=True)
    (root / "90_brain" / "ownership_policy.json").write_text(json.dumps(POLICY), encoding="utf-8")
    (root / "90_brain" / "language_retention.json").write_text(
        json.dumps({"files": {}}), encoding="utf-8"
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "gate@test")
    _git(root, "config", "user.name", "gate")
    (root / "07_risk" / "risk").mkdir(parents=True, exist_ok=True)
    (root / "07_risk" / "risk" / "engine.py").write_text(
        "class RiskEngine:\n    pass\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def test_task_report_failure_sections(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "07_risk" / "risk" / "engine.py").write_text(
        "class RiskEngine:\n    pass\n\ndef new_check():\n    pass\n", encoding="utf-8"
    )
    report = build_task_report(
        root=root, manifests={"risk.engine.evaluate": _fake_manifest("RUST_CANONICAL")}
    )
    assert report["status"] == "FAILED"
    assert report["architecture"]["files"]
    assert report["validation"]["architecture_gate"] == "FAIL"
    # Canonical violations fail outright instead of counting as debt.
    assert report["migration_impact"]["new_migration_debt"] == 0
    assert "self_check" in report
    assert report["self_check"]["architecture_validation_run"] is True


def test_task_report_clean_strategy_passes(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "05_strategy").mkdir(parents=True, exist_ok=True)
    (root / "05_strategy" / "sma.py").write_text("def signal():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "strategy base")
    (root / "05_strategy" / "sma.py").write_text(
        "def signal():\n    return 1\n\ndef confirmer():\n    return 2\n", encoding="utf-8"
    )
    report = build_task_report(root=root, manifests={})
    assert report["status"] == "PASS"
    assert report["changes"]["python"] == ["05_strategy/sma.py"]
    assert report["changes"]["accidental_python"] == []


def _fake_manifest(state: str):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    return SimpleNamespace(
        source="07_risk/risk/engine.py", target="rust/vayren-core/src/risk.rs", state=state
    )


def test_gate_and_report_agree(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    gate = check_changes(root=root, unit_states={})
    assert gate.verdict == "PASS"
    report = build_task_report(root=root, manifests={})
    assert report["validation"]["architecture_gate"] == "PASS"
