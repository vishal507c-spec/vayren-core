"""Change gate: wrong-language edits FAIL, allowed Python passes.

All repository-mutating cases run against synthetic git repos under tmp_path
so the real worktree is never touched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.migration.change_gate import (
    added_definitions,
    added_qt_subclasses,
    check_changes,
)

POLICY = {
    "rules": [
        {
            "id": "RUST_RISK",
            "domain": "RISK",
            "required_language": "RUST",
            "directory_prefixes": ["07_risk/risk/"],
            "excluded_subpaths": ["07_risk/risk/tests/"],
            "allow_python_glue": True,
        },
        {
            "id": "RUST_UI",
            "domain": "NATIVE_UI",
            "required_language": "RUST_SLINT",
            "directory_prefixes": ["00_app/app/ui/"],
            "excluded_subpaths": [],
            "allow_python_glue": False,
        },
        {
            "id": "PYTHON_STRATEGY",
            "domain": "STRATEGY",
            "required_language": "PYTHON",
            "directory_prefixes": ["05_strategy/"],
            "excluded_subpaths": [],
            "allow_python_glue": True,
        },
    ]
}


def _git(root: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def _mkrepo(root: Path, retention: dict | None = None) -> Path:
    (root / "90_brain").mkdir(parents=True, exist_ok=True)
    (root / "90_brain" / "ownership_policy.json").write_text(json.dumps(POLICY), encoding="utf-8")
    (root / "90_brain" / "language_retention.json").write_text(
        json.dumps({"files": retention or {}}), encoding="utf-8"
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "gate@test")
    _git(root, "config", "user.name", "gate")
    (root / "07_risk" / "risk").mkdir(parents=True, exist_ok=True)
    (root / "07_risk" / "risk" / "engine.py").write_text(
        "class RiskEngine:\n    def evaluate(self, request):\n        return True\n",
        encoding="utf-8",
    )
    (root / "05_strategy").mkdir(parents=True, exist_ok=True)
    (root / "05_strategy" / "sma.py").write_text("def signal():\n    return 1\n", encoding="utf-8")
    (root / "00_app" / "app" / "ui").mkdir(parents=True, exist_ok=True)
    (root / "00_app" / "app" / "ui" / "panel.py").write_text(
        "class Panel:\n    pass\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def test_added_definitions_detects_new_toplevel() -> None:
    old = "def a():\n    pass\n"
    new = "def a():\n    pass\n\ndef b():\n    pass\n\nclass C:\n    pass\n"
    funcs, classes = added_definitions(old, new)
    assert funcs == {"b"}
    assert classes == {"C"}


def test_added_definitions_syntax_error_is_empty() -> None:
    assert added_definitions("def broken(:", "def broken(:") == (set(), set())


def test_added_qt_subclasses_detects_widgets() -> None:
    old = "class Panel:\n    pass\n"
    new = "class Panel:\n    pass\n\nclass Board(QWidget):\n    pass\n"
    assert added_qt_subclasses(old, new) == {"Board"}
    assert added_qt_subclasses(old, old) == set()


def test_strategy_change_passes(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "05_strategy" / "sma.py").write_text(
        "def signal():\n    return 1\n\ndef confirmer():\n    return 2\n", encoding="utf-8"
    )
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "PASS"
    assert report.debt_created == 0


def test_new_python_file_in_rust_domain_fails(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "07_risk" / "risk" / "extra.py").write_text(
        "def helper():\n    pass\n", encoding="utf-8"
    )
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "FAIL"
    assert any(v.rule == "new-python-in-rust-domain" for v in report.violations)


def test_new_bridge_with_retention_passes(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path, retention={"07_risk/risk/native_extra.py": {"state": "x"}})
    (root / "07_risk" / "risk" / "native_extra.py").write_text(
        "def check_mask():\n    pass\n", encoding="utf-8"
    )
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "PASS"


def test_canonical_unit_extension_fails(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "07_risk" / "risk" / "engine.py").write_text(
        "class RiskEngine:\n"
        "    def evaluate(self, request):\n"
        "        return True\n"
        "\n"
        "def new_check():\n"
        "    pass\n",
        encoding="utf-8",
    )
    report = check_changes(root=root, unit_states={"risk.engine.evaluate": "RUST_CANONICAL"})
    assert report.verdict == "FAIL"
    assert any(v.rule == "canonical-python-extension" for v in report.violations)


def test_precanonical_glue_change_warns_not_fails(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "07_risk" / "risk" / "engine.py").write_text(
        "class RiskEngine:\n"
        "    def evaluate(self, request):\n"
        "        return True\n"
        "\n"
        "def helper():\n"
        "    pass\n",
        encoding="utf-8",
    )
    report = check_changes(root=root, unit_states={"risk.engine.evaluate": "BLOCKED"})
    assert report.verdict == "PASS"
    assert report.debt_created == 1
    assert report.warnings


def test_new_qt_widget_fails(tmp_path: Path) -> None:
    root = _mkrepo(
        tmp_path, retention={"00_app/app/ui/panel.py": {"state": "TEMPORARILY_RETAINED"}}
    )
    (root / "00_app" / "app" / "ui" / "panel.py").write_text(
        "class Panel:\n    pass\n\nclass Board(QWidget):\n    pass\n", encoding="utf-8"
    )
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "FAIL"
    assert any(v.rule == "new-qt-widget" for v in report.violations)


def test_new_python_ui_without_retention_fails(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "00_app" / "app" / "ui" / "fresh.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "FAIL"
    assert any(v.rule == "new-python-ui" for v in report.violations)


def test_retained_ui_plain_defs_warn(tmp_path: Path) -> None:
    root = _mkrepo(
        tmp_path, retention={"00_app/app/ui/panel.py": {"state": "TEMPORARILY_RETAINED"}}
    )
    (root / "00_app" / "app" / "ui" / "panel.py").write_text(
        "class Panel:\n    pass\n\ndef helper():\n    pass\n", encoding="utf-8"
    )
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "PASS"
    assert report.warnings


def test_non_repo_dir_is_error(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    report = check_changes(root=plain, unit_states={})
    assert report.verdict == "ERROR"


def test_rust_and_slint_files_pass(tmp_path: Path) -> None:
    root = _mkrepo(tmp_path)
    (root / "risk.rs").write_text("pub fn f() {}\n", encoding="utf-8")
    (root / "panel.slint").write_text("component App {}\n", encoding="utf-8")
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "PASS"
    assert report.files_checked == 2


def test_ui_redesign_acceptance_matrix(tmp_path: Path) -> None:
    """A UI redesign must be Slint/Rust; new Python UI is a violation.

    Encodes the five acceptance outcomes for a Strategy-Lab-style task:
      new Python UI file          -> FAIL (new-python-ui)
      new Qt widget in Qt file    -> FAIL (new-qt-widget)
      new Slint surface           -> PASS
      new Rust backend            -> PASS
      justified legacy Python glue-> PASS (warn)
    """
    root = _mkrepo(
        tmp_path, retention={"00_app/app/ui/panel.py": {"state": "TEMPORARILY_RETAINED"}}
    )
    (root / "00_app" / "app" / "ui" / "lab_redesign.py").write_text(
        "class Lab(QWidget):\n    pass\n", encoding="utf-8"
    )
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "FAIL"
    assert any(v.rule == "new-python-ui" for v in report.violations)

    (root / "00_app" / "app" / "ui" / "lab_redesign.py").unlink()
    (root / "rust").mkdir(exist_ok=True)
    (root / "rust" / "vayren-shell").mkdir(exist_ok=True)
    (root / "rust" / "vayren-shell" / "lab.slint").write_text(
        "export component Lab inherits Rectangle {}\n", encoding="utf-8"
    )
    (root / "rust" / "vayren-shell" / "lab_state.rs").write_text(
        "pub struct LabState;\n", encoding="utf-8"
    )
    report = check_changes(root=root, unit_states={})
    assert report.verdict == "PASS"
    assert any(".slint" in d for d in report.details)
