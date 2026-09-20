"""Launch the native Rust + Slint production shell.

Composition root for native mode: resolves the repo root, computes the data
and strategy directories (same precedence as the Python CLI), picks the
release binary when built (else debug), and execs it from the repo root so
the headless Python backend (`python -m app.headless`) resolves exactly as
verified. No UI toolkit anywhere on this path.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_LEGACY_DATA_DIR = r"D:\ZerodhaTradingData"
_LEGACY_STRATEGY_DIR = r"D:\VAYREN_STRATEGIES"


def default_data_dir() -> str:
    """Same precedence as the Python CLI: env, legacy folder, per-user."""
    override = os.environ.get("VAYREN_DATA_DIR")
    if override:
        return override
    if Path(_LEGACY_DATA_DIR).is_dir():
        return _LEGACY_DATA_DIR
    return str(Path.home() / ".vayren" / "data")


def default_strategy_dir() -> str:
    """Same precedence as the Python CLI: env, legacy folder, per-user."""
    override = os.environ.get("VAYREN_STRATEGIES")
    if override:
        return override
    if Path(_LEGACY_STRATEGY_DIR).is_dir():
        return _LEGACY_STRATEGY_DIR
    return str(Path.home() / ".vayren" / "strategies")


def find_binary(force_debug: bool) -> Path:
    """Prefer the release binary, fall back to debug. Fail closed if none."""
    release = REPO_ROOT / "rust" / "target" / "release" / "vayren-shell.exe"
    debug = REPO_ROOT / "rust" / "target" / "debug" / "vayren-shell.exe"
    if not force_debug and release.is_file():
        return release
    if debug.is_file():
        return debug
    raise FileNotFoundError(
        "no native binary found — build it first: "
        "`cargo build -p vayren-shell` (debug) or `cargo build --release -p vayren-shell`"
    )


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse launcher flags; everything unknown forwards to the binary."""
    parser = argparse.ArgumentParser(
        prog="launch_native", description="Launch the VAYREN native shell"
    )
    parser.add_argument("--data-dir", default=None, help="Candle store folder")
    parser.add_argument("--strategy-dir", default=None, help="Strategy library folder")
    parser.add_argument("--debug", action="store_true", help="Force the debug binary")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Resolve paths, pick the binary, exec it from the repo root."""
    args, extra = parse_args(argv)
    try:
        binary = find_binary(args.debug)
    except FileNotFoundError as exc:
        print(f"NATIVE LAUNCH FAILED: {exc}")
        return 2
    cmd = [
        str(binary),
        "--data-dir",
        args.data_dir or default_data_dir(),
        "--strategy-dir",
        args.strategy_dir or default_strategy_dir(),
        *extra,
    ]
    print(f"Launching native shell: {binary.name} (cwd={REPO_ROOT})")
    try:
        completed = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    except KeyboardInterrupt:
        print("NATIVE SHELL INTERRUPTED: shutdown clean")
        return 130
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
