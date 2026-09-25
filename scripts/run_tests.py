"""Suite-partitioned pytest driver.

Each entry runs in its OWN fresh interpreter for suite isolation: every
interpreter starts clean and exits before the next one begins.

Stdlib only. Exit code 0 iff every partition passed.

`--skip-build` skips the unconditional `build_rust.py` re-run when the
native artifacts are provably fresh (all 7 release cdylibs + the shell
binary exist and are newer than every Rust source). The check is
fail-closed: anything missing, older, or unreadable triggers the build.
This removes the duplicate full-chain invocation when the caller (e.g. CI
`build-test`, which runs `build_rust.py --test` first) already built.
`--lean` uses the lean artifact set instead (kernel cdylib + shell
binary, mirroring `build_rust.py --lean-test`) in both the gate and the
fallback build, so the six view release DLLs are never required here.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Partitions with live test files only (2026-09-21 cleanup): every entry
# must exist AND collect tests, otherwise the partition runner fails the
# gate. Empty/missing tests/ dirs stay covered by
# scripts/tests/test_gate_coverage.py only when they gain test files.
PARTS = (
    "00_app/app/tests",
    "02_data/data/tests",
    "05_strategy/strategy/tests",
    "09_broker/broker/tests",
    "scripts/forensics/tests",
    "scripts/tests",
)


def _expected_artifacts(*, lean: bool = False) -> list[Path]:
    """Native artifacts the test path requires.

    Full path: everything build_rust.py guarantees. Lean path (mirrors
    `build_rust.py --lean-test`): only the shipped kernel cdylib + the
    debug shell binary — the six view release DLLs have no consumer here.
    """
    system = platform.system().lower()
    ext = "dll" if system.startswith("win") else ("dylib" if system == "darwin" else "so")
    libs = (
        "vayren_core",
        "vayren_portfolio_view",
        "vayren_live_view",
        "vayren_system_view",
        "vayren_strategy_lab_view",
        "vayren_research_view",
        "vayren_market_view",
    )
    if lean:
        libs = ("vayren_core",)
    prefix = "" if system.startswith("win") else "lib"
    artifacts = [ROOT / "rust" / "target" / "release" / f"{prefix}{name}.{ext}" for name in libs]
    shell = "vayren-shell.exe" if system.startswith("win") else "vayren-shell"
    artifacts.append(ROOT / "rust" / "target" / "debug" / shell)
    return artifacts


def native_fresh(*, lean: bool = False) -> bool:
    """True only if every artifact exists and postdates every Rust source.

    Fail-closed: any missing/older/unreadable path returns False (build).
    """
    try:
        artifacts = _expected_artifacts(lean=lean)
        if any(not path.is_file() for path in artifacts):
            return False
        oldest_artifact = min(path.stat().st_mtime for path in artifacts)
        newest_source = 0.0
        for path in (ROOT / "rust").rglob("*"):
            if "target" in path.parts or not path.is_file():
                continue
            if path.suffix not in (".rs", ".slint", ".toml", ".lock"):
                continue
            newest_source = max(newest_source, path.stat().st_mtime)
            if newest_source > oldest_artifact:
                return False
        return newest_source <= oldest_artifact
    except OSError:
        return False


def ensure_native(env: dict[str, str], skip_build: bool, lean: bool = False) -> int:
    """Build the workspace unless --skip-build proves artifacts fresh.

    Lean mode mirrors `build_rust.py --lean-test` in both the freshness
    gate and the fallback build, so the six view release DLLs are never
    required (or built) on this path.
    """
    if skip_build and native_fresh(lean=lean):
        print("[        SKIP] rust build  (native artifacts fresh)", flush=True)
        return 0
    # The Rust kernels back execution/backtest/market tests: ensure the
    # cdylib exists before any partition imports the bridge (fail-closed).
    cmd = [sys.executable, "scripts/build_rust.py"]
    if lean:
        cmd.append("--lean-test")
    build = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        print(build.stdout[-2000:] if build.stdout else "")
        print(build.stderr[-2000:] if build.stderr else "")
        print("[        FAIL] rust build  (native kernels required by the gate)")
        return 1
    print("[        PASS] rust build", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suite-partitioned pytest driver")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="skip build_rust.py when native artifacts are provably fresh",
    )
    parser.add_argument(
        "--lean",
        action="store_true",
        help="lean test path: require only the kernel cdylib + shell binary "
        "(mirrors build_rust.py --lean-test; never needs the view DLLs)",
    )
    args = parser.parse_args(argv)
    env = os.environ.copy()

    if ensure_native(env, args.skip_build, args.lean) != 0:
        return 1

    failed: list[str] = []
    total_start = time.perf_counter()
    for part in PARTS:
        start = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", part, "-q", "--no-header"],
            env=env,
        )
        elapsed = time.perf_counter() - start
        status = "PASS" if proc.returncode == 0 else f"FAIL rc={proc.returncode}"
        print(f"[{status:>12}] {part}  ({elapsed:.1f}s)", flush=True)
        if proc.returncode != 0:
            failed.append(part)

    total = time.perf_counter() - total_start
    print("")
    print("SUITE PARTITION SUMMARY")
    passed = len(PARTS) - len(failed)
    print(f"  partitions passed: {passed}/{len(PARTS)}   total {total:.1f}s")
    if failed:
        print("  failed partitions:")
        for name in failed:
            print(f"    - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
