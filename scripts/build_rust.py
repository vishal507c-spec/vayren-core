"""Build the Rust workspace (AI_ENTRY.md §1: Rust owns core/perf).

Builds the `vayren-core` cdylib (release) consumed by the Python boundary
via ctypes, the six `vayren-*-view` cdylibs (release, one batched
invocation sharing a single shell build) hosting the native Slint view
screens, builds the `vayren-shell` native UI binary (debug), and verifies
the ABI handshake. `--test` also runs `cargo test` for the whole
workspace. `--lean-test` is the CI test path: core release + shell debug
+ workspace tests, skipping the six consumer-less view release DLLs.
Fail-closed: any cargo failure exits nonzero with the log tail.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _lib_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "vayren_core.dll"
    if system == "darwin":
        return "libvayren_core.dylib"
    return "libvayren_core.so"


def _view_lib_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "vayren_portfolio_view.dll"
    if system == "darwin":
        return "libvayren_portfolio_view.dylib"
    return "libvayren_portfolio_view.so"


def _live_lib_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "vayren_live_view.dll"
    if system == "darwin":
        return "libvayren_live_view.dylib"
    return "libvayren_live_view.so"


def _lab_lib_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "vayren_strategy_lab_view.dll"
    if system == "darwin":
        return "libvayren_strategy_lab_view.dylib"
    return "libvayren_strategy_lab_view.so"


def _system_lib_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "vayren_system_view.dll"
    if system == "darwin":
        return "libvayren_system_view.dylib"
    return "libvayren_system_view.so"


def _research_lib_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "vayren_research_view.dll"
    if system == "darwin":
        return "libvayren_research_view.dylib"
    return "libvayren_research_view.so"


def _market_lib_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "vayren_market_view.dll"
    if system == "darwin":
        return "libvayren_market_view.dylib"
    return "libvayren_market_view.so"


def _bin_name() -> str:
    system = platform.system().lower()
    return "vayren-shell.exe" if system.startswith("win") else "vayren-shell"


VIEW_PACKAGES = (
    "vayren-portfolio-view",
    "vayren-live-view",
    "vayren-system-view",
    "vayren-strategy-lab-view",
    "vayren-research-view",
    "vayren-market-view",
)


def _failure_summary(output: str, limit: int = 40) -> list[str]:
    """High-signal failure lines (test names, panics, errors).

    Cargo output on failure can be megabytes; the tail alone hid the failing
    test name twice (Day-5 CI diagnoses). This prints at most `limit`
    matching lines so the culprit is always visible.
    """
    patterns = ("FAILED", "panicked", "test result: FAILED", "could not compile", "error[")
    hits = [line for line in output.splitlines() if any(p in line for p in patterns)]
    return hits[:limit]


def _run(argv: list[str]) -> int:
    print(f"+ {' '.join(argv)}", flush=True)
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-3000:]
        print(f"cargo failed (rc={proc.returncode}):\n{tail}")
        for line in _failure_summary(proc.stderr or proc.stdout or ""):
            print(f"  ! {line.strip()[:200]}", flush=True)
    return proc.returncode


def _build_shell_debug() -> int:
    """Build the debug shell binary (desktop runtime + dev test target)."""
    rc = _run(["cargo", "build", "-p", "vayren-shell", "--manifest-path", "rust/Cargo.toml"])
    if rc != 0:
        return rc
    shell_bin = ROOT / "rust" / "target" / "debug" / _bin_name()
    if not shell_bin.is_file():
        print(f"ERROR: expected native UI binary missing after build: {shell_bin}")
        return 1
    print(f"built {shell_bin} ({shell_bin.stat().st_size} bytes)")
    return 0


def _run_workspace_tests() -> int:
    """Run the full workspace test suite (never narrowed, never skipped)."""
    return _run(["cargo", "test", "--workspace", "--manifest-path", "rust/Cargo.toml"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Rust workspace")
    parser.add_argument("--test", action="store_true", help="run `cargo test` too")
    parser.add_argument(
        "--lean-test",
        action="store_true",
        help="lean test path: core release + shell debug + workspace tests, "
        "skipping the six consumer-less view release DLLs (test gate only; "
        "packaging still uses the full path)",
    )
    parser.add_argument(
        "--check-only", action="store_true", help="verify the cdylib exists + handshake"
    )
    args = parser.parse_args(argv)

    if shutil.which("cargo") is None:
        print("ERROR: `cargo` not on PATH (install the Rust toolchain, then re-run).")
        return 2

    if args.check_only:
        sys.path.insert(0, str(ROOT / "01_core"))
        from core.native.loader import load_vayren_core

        load_vayren_core()
        print("native handshake OK")
        return 0

    rc = _run(
        ["cargo", "build", "--release", "-p", "vayren-core", "--manifest-path", "rust/Cargo.toml"]
    )
    if rc != 0:
        return rc
    lib = ROOT / "rust" / "target" / "release" / _lib_name()
    if not lib.is_file():
        print(f"ERROR: expected cdylib missing after build: {lib}")
        return 1
    print(f"built {lib} ({lib.stat().st_size} bytes)")

    if args.lean_test:
        # Lean test path (verified Day-4): the test gate needs the shipped
        # kernel (vayren_core release) + the debug shell + workspace tests.
        # The six view release DLLs have no in-repo consumer on this path,
        # so the batched view invocation below is skipped entirely.
        rc = _build_shell_debug()
        if rc != 0:
            return rc
        return _run_workspace_tests()

    rc = _run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "vayren-portfolio-view",
            "-p",
            "vayren-live-view",
            "-p",
            "vayren-system-view",
            "-p",
            "vayren-strategy-lab-view",
            "-p",
            "vayren-research-view",
            "-p",
            "vayren-market-view",
            "--manifest-path",
            "rust/Cargo.toml",
        ]
    )
    if rc != 0:
        return rc
    # Batched single invocation (Day-2 A/B: one shared shell build instead of
    # one per view). Same artifacts, same profile — only scheduling changed.
    for label, lib in (
        ("portfolio view", ROOT / "rust" / "target" / "release" / _view_lib_name()),
        ("live view", ROOT / "rust" / "target" / "release" / _live_lib_name()),
        ("system view", ROOT / "rust" / "target" / "release" / _system_lib_name()),
        ("strategy lab view", ROOT / "rust" / "target" / "release" / _lab_lib_name()),
        ("research view", ROOT / "rust" / "target" / "release" / _research_lib_name()),
        ("market view", ROOT / "rust" / "target" / "release" / _market_lib_name()),
    ):
        if not lib.is_file():
            print(f"ERROR: expected {label} cdylib missing after build: {lib}")
            return 1
    print(f"built {lib} ({lib.stat().st_size} bytes)")

    rc = _build_shell_debug()
    if rc != 0:
        return rc

    if args.test:
        rc = _run_workspace_tests()
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
