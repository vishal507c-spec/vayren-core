"""Suite-partitioned pytest driver.

Each entry runs in its OWN fresh interpreter for suite isolation: every
interpreter starts clean and exits before the next one begins.

Stdlib only. Exit code 0 iff every partition passed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

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


def main() -> int:
    env = os.environ.copy()

    # The Rust kernels back execution/backtest/market tests: ensure the
    # cdylib exists before any partition imports the bridge (fail-closed).
    build = subprocess.run(
        [sys.executable, "scripts/build_rust.py"],
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
