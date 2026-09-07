"""Build the Rust workspace (constitution §8: Rust owns core/perf).

Builds the `vayren-core` cdylib (release) consumed by the Python boundary
via ctypes, and verifies the ABI handshake. Also runs `cargo test` with
`--test`. Fail-closed: any cargo failure exits nonzero with the log tail.
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

    if args.test:
        rc = _run(["cargo", "test", "-p", "vayren-core", "--manifest-path", "rust/Cargo.toml"])
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
