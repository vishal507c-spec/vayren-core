"""Build the Rust workspace (AI_ENTRY.md §1: Rust owns core/perf).

Builds the `vayren-core` cdylib (release) consumed by the Python boundary
via ctypes, the `vayren-portfolio-view` cdylib (release) hosting the native
Slint Portfolio screen, builds the `vayren-shell` native UI binary, and
verifies the ABI handshake. Also runs `cargo test` for the whole workspace
with `--test`. Fail-closed: any cargo failure exits nonzero with the log
tail.
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


def _run(argv: list[str]) -> int:
    print(f"+ {' '.join(argv)}", flush=True)
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-3000:]
        print(f"cargo failed (rc={proc.returncode}):\n{tail}")
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Rust workspace")
    parser.add_argument("--test", action="store_true", help="run `cargo test` too")
    parser.add_argument(
        "--check-only", action="store_true", help="verify the cdylib exists + handshake"
    )
    args = parser.parse_args()

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

    rc = _run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "vayren-portfolio-view",
            "--manifest-path",
            "rust/Cargo.toml",
        ]
    )
    if rc != 0:
        return rc
    view_lib = ROOT / "rust" / "target" / "release" / _view_lib_name()
    if not view_lib.is_file():
        print(f"ERROR: expected portfolio view cdylib missing after build: {view_lib}")
        return 1
    print(f"built {view_lib} ({view_lib.stat().st_size} bytes)")

    rc = _run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "vayren-live-view",
            "--manifest-path",
            "rust/Cargo.toml",
        ]
    )
    if rc != 0:
        return rc
    live_lib = ROOT / "rust" / "target" / "release" / _live_lib_name()
    if not live_lib.is_file():
        print(f"ERROR: expected live view cdylib missing after build: {live_lib}")
        return 1
    print(f"built {live_lib} ({live_lib.stat().st_size} bytes)")

    rc = _run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "vayren-system-view",
            "--manifest-path",
            "rust/Cargo.toml",
        ]
    )
    if rc != 0:
        return rc
    system_lib = ROOT / "rust" / "target" / "release" / _system_lib_name()
    if not system_lib.is_file():
        print(f"ERROR: expected system view cdylib missing after build: {system_lib}")
        return 1
    print(f"built {system_lib} ({system_lib.stat().st_size} bytes)")

    rc = _run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "vayren-strategy-lab-view",
            "--manifest-path",
            "rust/Cargo.toml",
        ]
    )
    if rc != 0:
        return rc
    lab_lib = ROOT / "rust" / "target" / "release" / _lab_lib_name()
    if not lab_lib.is_file():
        print(f"ERROR: expected strategy lab view cdylib missing after build: {lab_lib}")
        return 1
    print(f"built {lab_lib} ({lab_lib.stat().st_size} bytes)")

    rc = _run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "vayren-research-view",
            "--manifest-path",
            "rust/Cargo.toml",
        ]
    )
    if rc != 0:
        return rc
    research_lib = ROOT / "rust" / "target" / "release" / _research_lib_name()
    if not research_lib.is_file():
        print(f"ERROR: expected research view cdylib missing after build: {research_lib}")
        return 1
    print(f"built {research_lib} ({research_lib.stat().st_size} bytes)")

    rc = _run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "vayren-market-view",
            "--manifest-path",
            "rust/Cargo.toml",
        ]
    )
    if rc != 0:
        return rc
    market_lib = ROOT / "rust" / "target" / "release" / _market_lib_name()
    if not market_lib.is_file():
        print(f"ERROR: expected market view cdylib missing after build: {market_lib}")
        return 1
    print(f"built {market_lib} ({market_lib.stat().st_size} bytes)")

    rc = _run(["cargo", "build", "-p", "vayren-shell", "--manifest-path", "rust/Cargo.toml"])
    if rc != 0:
        return rc
    shell_bin = ROOT / "rust" / "target" / "debug" / _bin_name()
    if not shell_bin.is_file():
        print(f"ERROR: expected native UI binary missing after build: {shell_bin}")
        return 1
    print(f"built {shell_bin} ({shell_bin.stat().st_size} bytes)")

    if args.test:
        rc = _run(["cargo", "test", "--workspace", "--manifest-path", "rust/Cargo.toml"])
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
