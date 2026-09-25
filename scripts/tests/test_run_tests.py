"""Tests for the partitioned runner's fail-closed --skip-build (run_tests.py).

The freshness gate must never skip a needed build: missing artifacts,
older artifacts, and unreadable trees all force the build. Only a fully
fresh tree may skip.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import run_tests  # noqa: E402


@pytest.fixture
def _fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "rust" / "target" / "release").mkdir(parents=True)
    (root / "rust" / "target" / "debug").mkdir(parents=True)
    monkeypatch.setattr(run_tests, "ROOT", root)
    return root


def _touch(path: Path, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    import os

    os.utime(path, ns=(mtime_ns, mtime_ns))


def _make_tree(root: Path, *, fresh: bool) -> None:
    base = 1_700_000_000_000_000_000
    for artifact in run_tests._expected_artifacts():
        _touch(root / artifact.relative_to(run_tests.ROOT), base + 100)
    source = root / "rust" / "vayren-core" / "src" / "lib.rs"
    _touch(source, base if fresh else base + 200)


def test_expected_artifacts_cover_build_chain() -> None:
    names = [p.name for p in run_tests._expected_artifacts()]
    assert len(names) == 8  # 7 release cdylibs + shell binary
    assert any("vayren_core" in name for name in names)
    assert any("shell" in name for name in names)


def test_lean_artifacts_cover_kernel_and_shell_only() -> None:
    names = [p.name for p in run_tests._expected_artifacts(lean=True)]
    assert len(names) == 2  # kernel cdylib + shell binary, never the views
    assert any("vayren_core" in name for name in names)
    assert any("shell" in name for name in names)


def test_lean_fresh_ignores_missing_view_dlls(_fake_root: Path) -> None:
    base = 1_700_000_000_000_000_000
    for artifact in run_tests._expected_artifacts(lean=True):
        _touch(_fake_root / artifact.relative_to(run_tests.ROOT), base + 100)
    source = _fake_root / "rust" / "vayren-core" / "src" / "lib.rs"
    _touch(source, base)
    assert run_tests.native_fresh(lean=True) is True
    assert run_tests.native_fresh() is False  # full set still needs the views


def test_missing_artifacts_force_build(_fake_root: Path) -> None:
    assert run_tests.native_fresh() is False


def test_fresh_tree_may_skip(_fake_root: Path) -> None:
    _make_tree(_fake_root, fresh=True)
    assert run_tests.native_fresh() is True


def test_stale_source_forces_build(_fake_root: Path) -> None:
    _make_tree(_fake_root, fresh=False)
    assert run_tests.native_fresh() is False


def test_unreadable_tree_forces_build(_fake_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_tree(_fake_root, fresh=True)
    monkeypatch.setattr(
        run_tests.Path,
        "stat",
        lambda self: (_ for _ in ()).throw(OSError()),  # noqa: ARG005
    )
    assert run_tests.native_fresh() is False


def test_skip_build_skips_subprocess(_fake_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_tree(_fake_root, fresh=True)
    calls: list[list[str]] = []

    def _fail(argv: list[str], **kwargs: object) -> object:  # noqa: ARG001
        calls.append(argv)
        raise AssertionError("build must be skipped")

    monkeypatch.setattr(run_tests.subprocess, "run", _fail)
    assert run_tests.ensure_native({}, True) == 0
    assert calls == []


def test_stale_tree_builds_even_with_skip_flag(
    _fake_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_tree(_fake_root, fresh=False)

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(run_tests.subprocess, "run", lambda *a, **k: _Proc())  # noqa: ARG005
    assert run_tests.ensure_native({}, True) == 0


def test_lean_fallback_builds_lean_test(_fake_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cmds: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _record(argv: list[str], **kwargs: object) -> object:  # noqa: ARG001
        cmds.append(argv)
        return _Proc()

    monkeypatch.setattr(run_tests.subprocess, "run", _record)
    assert run_tests.ensure_native({}, False, lean=True) == 0
    assert cmds == [[run_tests.sys.executable, "scripts/build_rust.py", "--lean-test"]]
    assert run_tests.ensure_native({}, False, lean=False) == 0
    assert cmds[-1] == [run_tests.sys.executable, "scripts/build_rust.py"]


def test_parts_stays_tuple_literal() -> None:
    assert isinstance(run_tests.PARTS, tuple)
    assert len(run_tests.PARTS) == 6
