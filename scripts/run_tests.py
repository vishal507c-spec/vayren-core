"""Suite-partitioned pytest driver.

Each entry runs in its OWN fresh interpreter. Cross-suite heap poisoning
(leaked PySide6/shiboken objects from one suite being collected inside
another — observed as Windows access violations under pymalloc) becomes
structurally impossible: every interpreter starts clean and exits before
the next one begins.

The chart domain is expanded PER TEST FILE — its render/pixmap-heavy tests
are the most GC-sensitive, and file-level isolation makes even that
partition deterministic.

Stdlib only. Exit code 0 iff every partition passed.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

PARTS = (
    "00_app/app/tests",
    "01_core/core/tests",
    "02_data/data/tests",
    "03_market/market/tests",
    *sorted(glob.glob("04_chart/chart/tests/test_*.py")),
    "05_strategy/strategy/tests",
    "05_strategy/strategy/research/tests",
    "06_backtest/backtest/tests",
    "07_risk/risk/tests",
    "08_execution/execution/tests",
    "09_broker/broker/tests",
    "scripts/forensics/tests",
    "scripts/tests",
)


def main() -> int:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # pymalloc + shiboken interplay is the AV source; plain malloc is stable.
    env.setdefault("PYTHONMALLOC", "malloc")

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
