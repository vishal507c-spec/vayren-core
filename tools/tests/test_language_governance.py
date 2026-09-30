"""Language-governance guardrails — negative tests for the ownership validators.

Every case runs against temporary fixtures (tmp_path) or inline rule lists:
these tests never write to the repository and never depend on git state.
They pin Phase 3 invariants:

- unauthorized programming language  -> FAIL
- new file in a Rust-owned domain without retention -> FAIL (via pure rule match)
- Python-owned domain absorbed into Rust  -> FAIL
- migrated authority reintroduced in Python -> FAIL
- unauthorized core.native importer (rogue bridge) -> FAIL
- valid strategy / kernel / Slint / bridge / test paths -> PASS
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from validate_architecture_gate import (  # noqa: E402
    _is_first_party_root,
    _required_language,
    check_changes,
    check_new_file_extension,
)
from validate_language_ownership import (  # noqa: E402
    NO_REINTRODUCE,
    _classify_file,
    _defines,
    _imports_core_native,
    core_native_importer_allowed,
    rust_module_forbidden,
)

ROOT = SCRIPTS_DIR.parent


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ── §2/§5: new language = denied ──────────────────────────────────────────


def test_unauthorized_programming_extension_fails() -> None:
    for rel in (
        "08_execution/execution/bot.go",
        "02_data/data/driver.java",
        "rust/vayren-core/src/helper.cpp",
        "00_app/app/ui/widget.ts",
    ):
        violation = check_new_file_extension(rel)
        assert violation is not None, rel
        assert violation.rule == "unauthorized-language", rel


def test_script_shell_and_binary_extensions_fail() -> None:
    for rel in (
        "scripts/deploy.sh",
        "scripts/setup.ps1",
        "scripts/run.bat",
        "scripts/run.cmd",
        "rust/vayren-core/prebuilt/vayren.dll",
        "tools/vayren.exe",
        "tools/vayren.jar",
    ):
        violation = check_new_file_extension(rel)
        assert violation is not None, rel
        assert violation.rule == "unauthorized-language", rel


def test_udf_like_extensions_still_pass() -> None:
    for rel in (
        "05_strategy/strategy/ok.py",
        "rust/vayren-core/src/kernel.rs",
        "rust/vayren-shell/ui/screen.slint",
        "90_brain/note.md",
        "scripts/data.jsonl",
        "scripts/config.yaml",
        "pyproject.toml",
        "Makefile",
    ):
        assert check_new_file_extension(rel) is None, rel


def test_allowed_languages_and_non_languages_pass() -> None:
    for rel in (
        "05_strategy/strategy/ok.py",
        "rust/vayren-core/src/kernel.rs",
        "rust/vayren-shell/ui/screen.slint",
        "90_brain/note.md",
        "scripts/data.jsonl",
        "pyproject.toml",
        "Makefile",
    ):
        assert check_new_file_extension(rel) is None, rel


# ── §5: new-file domain ownership (pure rule match) ────────────────────────

_INLINE_RULES = [
    {
        "id": "PYTHON_AI",
        "domain": "AI_ADJACENT",
        "required_language": "PYTHON",
        "directory_prefixes": ["01_core/core/ai/"],
        "excluded_subpaths": [],
    },
    {
        "id": "RUST_CORE",
        "domain": "CORE",
        "required_language": "RUST",
        "directory_prefixes": ["01_core/core/"],
        "excluded_subpaths": ["01_core/core/tests/", "01_core/core/ai/"],
    },
]


def test_wrong_language_in_rust_owned_domain_detected() -> None:
    assert _required_language("01_core/core/sneaky_engine.py", _INLINE_RULES) == "RUST"


def test_excluded_subpaths_never_inherit_parent_language() -> None:
    # Earlier specific rules still win by first-match order (ai/ is PYTHON).
    assert _required_language("01_core/core/ai/sneak.py", _INLINE_RULES) == "PYTHON"
    assert _required_language("01_core/core/tests/t_x.py", _INLINE_RULES) is None
    # Regression test for the old `prefix + sub` concat (which never matched):
    # with only the general rule present, an excluded path must yield None,
    # never the parent's language.
    general_only = [_INLINE_RULES[1]]
    assert _required_language("01_core/core/ai/sneak.py", general_only) is None
    assert _required_language("01_core/core/tests/t_x.py", general_only) is None
    assert _required_language("01_core/core/engine.py", general_only) == "RUST"


def test_real_policy_marks_rust_and_python_paths() -> None:
    policy = json.loads((ROOT / "docs" / "ownership_policy.json").read_text(encoding="utf-8"))
    rules = policy["rules"]
    assert _required_language("src/execution/sneaky.py", rules) == "RUST"
    # The rust/ tree itself has no Python-file ownership rule (it is governed
    # by the Rust-side checks: forbidden-module stems + workspace hygiene,
    # covered by the rust_module_forbidden tests below).
    assert _required_language("rust/vayren-shell/src/evil.py", rules) is None
    rule = _classify_file("src/strategy/ok.py", rules)
    assert rule is not None and rule["required_language"] == "PYTHON"
    # Excluded test paths yield no rule (the validator skips them) — they never
    # inherit the parent domain's required language.
    assert _classify_file("src/core/tests/t_x.py", rules) is None


# ── §6/§14: no reintroduction, no rogue bridges ───────────────────────────


def test_reintroduced_authority_detected(tmp_path: Path) -> None:
    bad = _write(tmp_path / "order.py", "TRANSITIONS = {('A', 'B'): True}\n")
    assert _defines(bad, "TRANSITIONS", "no-assign") is True
    good = _write(tmp_path / "clean.py", "STATE_CODES = (1, 2, 3)\n")
    assert _defines(good, "TRANSITIONS", "no-assign") is False


def test_no_assign_catches_every_store_shape(tmp_path: Path) -> None:
    for label, source in (
        ("annotated", "TRANSITIONS: dict = {}\n"),
        ("attribute", "self.TRANSITIONS = {}\n"),
        ("annotated-attribute", "state.TRANSITIONS: dict = {}\n"),
        ("subscript", "TABLE['TRANSITIONS'] = {}\n"),
        ("tuple-unpack", "TRANSITIONS, other = {}, None\n"),
        ("nested-unpack", "first, (TRANSITIONS, x) = 1, ({}, 2)\n"),
    ):
        path = _write(tmp_path / f"{label}.py", source)
        assert _defines(path, "TRANSITIONS", "no-assign") is True, label


def test_no_loop_catches_comprehensions(tmp_path: Path) -> None:
    for label, source in (
        ("list", "def _sharpe(x):\n    return [r for r in x]\n"),
        ("set", "def _sharpe(x):\n    return {r for r in x}\n"),
        ("dict", "def _sharpe(x):\n    return {r: r for r in x}\n"),
        ("generator", "def _sharpe(x):\n    return (r for r in x)\n"),
        ("for", "def _sharpe(x):\n    for r in x:\n        pass\n    return 0.0\n"),
    ):
        path = _write(tmp_path / f"{label}.py", source)
        assert _defines(path, "_sharpe", "no-loop") is True, label


def test_no_loop_ignores_helper_calls(tmp_path: Path) -> None:
    # A helper call is a different symbol: not this guard's business.
    path = _write(tmp_path / "helper.py", "def _sharpe(x):\n    return _mean(x) / _std(x)\n")
    assert _defines(path, "_sharpe", "no-loop") is False


def test_every_no_reintroduce_guard_targets_a_live_file() -> None:
    # A guard pointing at a deleted/renamed file silently enforces nothing,
    # which is how the order-state table became re-introducible again.
    for name, (files, _kind) in NO_REINTRODUCE.items():
        assert files, f"{name} has no guard target"
        for rel in files:
            assert (ROOT / rel).is_file(), f"dead NO_REINTRODUCE guard: {name} -> {rel}"


def test_order_state_vocabulary_carries_no_transition_table() -> None:
    # The file language_retention.json describes as "enum vocabulary only".
    order_state = ROOT / "src" / "execution" / "models" / "order_state.py"
    assert order_state.is_file()
    for name in ("TRANSITIONS", "TERMINAL_STATES"):
        assert _defines(order_state, name, "no-assign") is False


def test_rust_must_not_absorb_python_owned_modules() -> None:
    assert rust_module_forbidden("rust/vayren-core/src/strategy.rs") is True
    assert rust_module_forbidden("rust/vayren-core/src/research.rs") is True
    assert rust_module_forbidden("rust/vayren-core/src/execution_engine.rs") is False
    assert rust_module_forbidden("rust/vayren-core/src/metrics.rs") is False


def test_repo_local_sibling_import_is_first_party() -> None:
    # Regression test: test files import sibling scripts tooling by
    # path-insertion (`from validate_architecture_gate import ...`) — the gate
    # must treat repo-local bare modules as first-party, not third-party.
    local = {"validate_architecture_gate", "dashboard", "journal"}
    assert _is_first_party_root("validate_architecture_gate", local) is True
    assert _is_first_party_root("dashboard", local) is True
    assert _is_first_party_root("json", local) is True
    assert _is_first_party_root("strategy", local) is True
    assert _is_first_party_root("requests", local) is False
    assert _is_first_party_root("kiteconnect", local) is False


def test_core_native_importer_boundary(tmp_path: Path) -> None:
    assert core_native_importer_allowed("src/market/native_x.py") is True
    assert core_native_importer_allowed("src/core/native/__init__.py") is True
    assert core_native_importer_allowed("tools/build_rust.py") is True
    assert core_native_importer_allowed("scripts/build_rust.py") is True
    assert core_native_importer_allowed("src/execution/session.py") is False
    assert core_native_importer_allowed("src/strategy/evil.py") is False

    importer = _write(tmp_path / "rogue.py", "from core.native.loader import load_vayren_core\n")
    assert _imports_core_native(importer) is True
    clean = _write(tmp_path / "plain.py", "from core import Event\n")
    assert _imports_core_native(clean) is False


def test_core_native_dynamic_import_detected(tmp_path: Path) -> None:
    for label, source in (
        ("importlib", "import importlib\nimportlib.import_module('core.native.loader')\n"),
        ("dunder", "__import__('core.native.loader')\n"),
    ):
        path = _write(tmp_path / f"{label}.py", source)
        assert _imports_core_native(path) is True, label
    for label, source in (
        ("importlib-other", "import importlib\nimportlib.import_module('market.models.bar')\n"),
        ("dunder-other", "__import__('json')\n"),
    ):
        path = _write(tmp_path / f"{label}.py", source)
        assert _imports_core_native(path) is False, label


def test_declared_test_tooling_is_not_third_party() -> None:
    # Test tooling is checked against pyproject, never blanket-exempt, and
    # never mislabelled "third-party".
    assert _is_first_party_root("pytest", set()) is False
    assert _is_first_party_root("coverage", set()) is False


def test_gate_without_git_warns_instead_of_erroring(monkeypatch) -> None:
    import validate_architecture_gate as gate

    monkeypatch.setattr(gate, "_git_available", lambda: False)
    monkeypatch.setattr(gate, "_working_tree", lambda **_kwargs: ["05_strategy/strategy/sma.py"])
    report = check_changes()
    assert report.verdict == "PASS"
    assert any("no git work tree" in warning for warning in report.warnings)
