"""Desktop launch + active-docs hygiene (Phase 7 §12/§15, §10 verdicts).

Desktop shortcut test runs only on Windows with the shortcut present
(CI runs Ubuntu -> skipped there); everything else is cross-platform.
Doc/retention checks pin the Phase 7 cleanup verdicts: active docs must not
reference verified-deleted files, and the retention ledger must stay healthy
(all retained files exist, no migrated record points at a live file).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

ROOT = SCRIPTS_DIR.parent
# build_rust.py names the debug shell binary per platform (cargo adds .exe
# on Windows only); the existence check below must match the builder.
_BIN_NAME = "vayren-shell.exe" if sys.platform == "win32" else "vayren-shell"
EXPECTED_BINARY = ROOT / "rust" / "target" / "debug" / _BIN_NAME

ACTIVE_DOCS = [
    "AI_ENTRY.md",
    "AGENTS.md",
    "README.md",
    "90_brain/architecture.md",
    "90_brain/event_catalog.md",
    "90_brain/module_contracts.md",
    "00_app/app/README.md",
    "05_strategy/strategy/README.md",
    "09_broker/broker/README.md",
    "scripts/README.md",
    "scripts/forensics/README.md",
    "scripts/speed/README.md",
]

# NOTE: runs/scoreboard/speed jsonl paths are intentionally absent here — the
# harnesses recreate those outputs at runtime (missing files are tolerated),
# so docs describing the live tool paths are not stale.
DELETED_NAMES = [
    "seed_sample_db",
    "benchmark_corpus.md",
    "core/tests/helpers",
    "chart_view.slint",
    "core/observable",
]


def _shortcut_target() -> tuple[str, str] | None:
    """Resolve VAYREN.lnk via the shell (Windows only). Returns (target, args)."""
    lnk = Path("C:/Users/visha/Desktop/VAYREN.lnk")
    if sys.platform != "win32" or not lnk.is_file():
        return None
    script = (
        "$sh = New-Object -ComObject WScript.Shell; "
        "$lnk = $sh.CreateShortcut('C:/Users/visha/Desktop/VAYREN.lnk'); "
        '"TARGET:$($lnk.TargetPath)|ARGS:$($lnk.Arguments)"'
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0 or "TARGET:" not in proc.stdout:
        raise RuntimeError(f"shortcut unreadable: {proc.stderr.strip()}")
    payload = proc.stdout[proc.stdout.index("TARGET:") :].strip()
    target = payload.split("|ARGS:")
    return target[0].replace("TARGET:", ""), target[1] if len(target) > 1 else ""


def test_shell_binary_exists() -> None:
    assert EXPECTED_BINARY.is_file(), "debug shell binary must exist (shortcut target)"


def test_shell_accepts_data_dirs() -> None:
    main_rs = (ROOT / "rust" / "vayren-shell" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "--data-dir" in main_rs and "--strategy-dir" in main_rs


def test_shell_spawns_backend_itself() -> None:
    bridge_path = ROOT / "rust" / "vayren-shell" / "src" / "python_bridge.rs"
    bridge = bridge_path.read_text(encoding="utf-8")
    assert "app.headless" in bridge, "shell must spawn the headless backend directly"


@pytest.mark.skipif(
    sys.platform != "win32" or not Path("C:/Users/visha/Desktop/VAYREN.lnk").is_file(),
    reason="desktop shortcut present on Windows dev machine only",
)
def test_desktop_shortcut_launches_rust_directly() -> None:
    resolved = _shortcut_target()
    assert resolved is not None
    target, args = resolved
    assert target.lower().endswith("vayren-shell.exe"), (
        f"shortcut must target the Rust binary: {target}"
    )
    assert "python" not in target.lower(), "no Python launcher in the desktop path"
    assert "launch_native" not in args, "shortcut must bypass scripts/launch_native.py"
    assert "--data-dir" in args and "--strategy-dir" in args


def test_no_stale_deleted_refs_in_active_docs() -> None:
    offenders: list[str] = []
    for rel in ACTIVE_DOCS:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for name in DELETED_NAMES:
            if name in text:
                offenders.append(f"{rel}: {name}")
    assert offenders == [], f"stale references to deleted files: {offenders}"


def test_retention_ledger_healthy() -> None:
    retention_path = ROOT / "90_brain" / "language_retention.json"
    retention = json.loads(retention_path.read_text(encoding="utf-8"))
    # Compact schema lock: only live per-file entries (no history objects).
    assert "migrated" not in retention, "migrated ledger must stay removed"
    assert "classes" not in retention, "class-level docs must stay removed"
    assert set(retention.keys()) >= {"files"}
    for path, entry in retention["files"].items():
        state = entry.get("state", "") if isinstance(entry, dict) else ""
        if state in ("TEMPORARILY_RETAINED", "EXEMPT_WITH_JUSTIFICATION", "MIGRATION_REQUIRED"):
            assert (ROOT / path).is_file(), f"retained file missing: {path}"
            for field in ("reason", "migration_target", "migration_condition"):
                assert entry.get(field), f"{path}: missing '{field}'"
