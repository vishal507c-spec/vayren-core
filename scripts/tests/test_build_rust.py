"""Tests for the Rust build driver (scripts/build_rust.py) — AST only, no cargo.

Pins the Day-2 batching: all six view crates build in ONE release
invocation (shared shell build) while the kernel release, the debug shell,
and the full `cargo test --workspace` safety net stay intact.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import build_rust  # noqa: E402

VIEWS = (
    "vayren-portfolio-view",
    "vayren-live-view",
    "vayren-system-view",
    "vayren-strategy-lab-view",
    "vayren-research-view",
    "vayren-market-view",
)


def _run_calls() -> list[list[str]]:
    """Every argv list literal passed to _run() in build_rust.main."""
    tree = ast.parse((SCRIPTS_DIR / "build_rust.py").read_text(encoding="utf-8"))
    calls: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_run"):
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        items: list[str] = []
        for elt in node.args[0].elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                items.append(elt.value)
        calls.append(items)
    return calls


def _p_flags(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "-p"]


def test_view_packages_cover_all_six() -> None:
    assert tuple(build_rust.VIEW_PACKAGES) == VIEWS


def test_single_batched_view_release_invocation() -> None:
    release_builds = [
        argv for argv in _run_calls() if "cargo" in argv and "build" in argv and "--release" in argv
    ]
    batched = [argv for argv in release_builds if set(VIEWS) <= set(_p_flags(argv))]
    assert len(batched) == 1, [(_p_flags(a)) for a in release_builds]
    solo_views = [
        argv
        for argv in release_builds
        if _p_flags(argv)
        and not set(VIEWS) <= set(_p_flags(argv))
        and "vayren-core" not in _p_flags(argv)
    ]
    assert solo_views == [], "no single-view release invocation may remain"


def test_kernel_release_stays_separate() -> None:
    release_builds = [
        argv for argv in _run_calls() if "cargo" in argv and "build" in argv and "--release" in argv
    ]
    assert any(_p_flags(argv) == ["vayren-core"] for argv in release_builds)


def test_full_workspace_test_safety_net_intact() -> None:
    calls = _run_calls()
    assert any(argv[:3] == ["cargo", "test", "--workspace"] for argv in calls), (
        "cargo test --workspace must never be weakened"
    )
    assert any(
        argv[:3] == ["cargo", "build", "-p"] and "vayren-shell" in argv and "--release" not in argv
        for argv in calls
    ), "debug shell build must stay"


def test_all_six_dlls_still_asserted() -> None:
    text = (SCRIPTS_DIR / "build_rust.py").read_text(encoding="utf-8")
    for helper in (
        "_view_lib_name",
        "_live_lib_name",
        "_system_lib_name",
        "_lab_lib_name",
        "_research_lib_name",
        "_market_lib_name",
    ):
        assert helper in text, f"artifact assertion lost for {helper}"


def test_no_new_process_shapes() -> None:
    calls = _run_calls()
    for argv in calls:
        assert argv[0] == "cargo", f"unexpected process shape: {argv[:2]}"
    assert len(calls) == 4, "expected: core release + batched views + shell debug + workspace test"


def test_failure_summary_surfaces_culprits() -> None:
    output = (
        "   Compiling foo v0.1.0\n"
        "test bar::baz ... FAILED\n"
        "thread 'bar::baz' panicked at src/lib.rs:9:5\n"
        "test result: FAILED. 1 passed; 1 failed\n"
        "error: could not compile `foo` (lib test)\n"
    )
    hits = build_rust._failure_summary(output)
    assert any("bar::baz ... FAILED" in line for line in hits)
    assert any("panicked" in line for line in hits)
    assert any("could not compile" in line for line in hits)
    assert build_rust._failure_summary("all green\n") == []
    long = "\n".join(f"test t{i} ... FAILED" for i in range(100))
    assert len(build_rust._failure_summary(long)) == 40


def _fake_tree(root: Path) -> None:
    # Platform-correct names via the driver's own helpers (a hardcoded
    # `.dll`/`.exe` tree goes stale on Linux CI).
    (root / "rust" / "target" / "release").mkdir(parents=True)
    (root / "rust" / "target" / "debug").mkdir(parents=True)
    for artifact in (
        f"rust/target/release/{build_rust._lib_name()}",
        f"rust/target/release/{build_rust._view_lib_name()}",
        f"rust/target/release/{build_rust._live_lib_name()}",
        f"rust/target/release/{build_rust._system_lib_name()}",
        f"rust/target/release/{build_rust._lab_lib_name()}",
        f"rust/target/release/{build_rust._research_lib_name()}",
        f"rust/target/release/{build_rust._market_lib_name()}",
        f"rust/target/debug/{build_rust._bin_name()}",
    ):
        path = root / artifact
        path.write_text("x", encoding="utf-8")


def test_lean_test_skips_view_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_rust, "ROOT", tmp_path)
    _fake_tree(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(build_rust, "_run", lambda argv: calls.append(argv) or 0)
    assert build_rust.main(["--lean-test"]) == 0
    assert [argv[1] for argv in calls] == ["build", "build", "test"]
    assert calls[0][2:5] == ["--release", "-p", "vayren-core"]
    assert calls[1][2:4] == ["-p", "vayren-shell"]
    assert calls[2][2:4] == ["--workspace", "--manifest-path"]
    assert all("vayren-portfolio-view" not in argv for argv in calls)
    assert all("vayren-market-view" not in argv for argv in calls)
    capsys.readouterr()


def test_full_test_keeps_batched_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_rust, "ROOT", tmp_path)
    _fake_tree(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(build_rust, "_run", lambda argv: calls.append(argv) or 0)
    assert build_rust.main(["--test"]) == 0
    batched = [argv for argv in calls if "--release" in argv and set(VIEWS) <= set(_p_flags(argv))]
    assert len(batched) == 1
    for view in VIEWS:
        assert view in batched[0]
    capsys.readouterr()
