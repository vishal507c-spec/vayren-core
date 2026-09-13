"""Background migration worker: Pipeline B runs beside new development.

The worker processes BLOCKED units in dependency order while the user works
on features. Concurrency safety:

- A lockfile (PID + timestamp) prevents two workers interleaving writes.
  Stale locks (>30 min or dead PID) are stolen with an evidence note.
- Units with uncommitted user changes in their files are skipped unless
  explicitly included — the worker never overwrites in-flight work.
- Slint/UI units are never picked up: UI migrates through the Slint shell
  track, not this pipeline.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ROOT
from .analyzer import NATIVE_UI_PREFIXES, QT_MARKERS
from .models import AgentOutcome
from .order import blocked_order

LOCK_NAME = ".worker.lock"
STALE_SECONDS = 1800


@dataclass
class WorkerOutcome:
    started: str = ""
    finished: str = ""
    processed: int = 0
    skipped: list[str] = field(default_factory=list)
    outcomes: list[AgentOutcome] = field(default_factory=list)
    lock_note: str = ""

    def to_dict(self) -> dict:
        return {
            "started": self.started,
            "finished": self.finished,
            "processed": self.processed,
            "skipped": list(self.skipped),
            "outcomes": [
                {"unit_id": outcome.unit_id, "verdict": outcome.verdict}
                for outcome in self.outcomes
            ],
            "lock_note": self.lock_note,
        }


def lock_path(root: Path = ROOT) -> Path:
    override = os.environ.get("VAYREN_MIGRATION_LOCK", "")
    if override:
        return Path(override)
    return root / "90_brain" / "migration" / LOCK_NAME


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pid_alive(pid: int) -> bool:
    """Process-liveness probe without signals (os.kill is unreliable here)."""
    if pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError, ValueError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_lock(root: Path = ROOT, actor: str = "background-worker") -> tuple[bool, str]:
    """Atomically claim the worker lock. Returns (ok, note)."""
    path = lock_path(root)
    now = time.time()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        pid = int(data.get("pid", -1) or -1)
        started = float(data.get("started_epoch", 0) or 0)
        if _pid_alive(pid) and now - started < STALE_SECONDS:
            return False, f"locked by live pid {pid} since {data.get('started', '?')}"
        steal_note = f"stole stale lock (pid={pid}, age={int(now - started)}s)"
        for _ in range(3):
            try:
                path.unlink()
                break
            except PermissionError:
                # Transient AV/indexer lock on Windows; brief backoff.
                time.sleep(0.05)
            except OSError:
                break
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, OSError) as exc:
            return False, f"lost lock race after steal ({steal_note}): {exc}"
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": os.getpid(),
                    "actor": actor,
                    "started": _stamp(),
                    "started_epoch": now,
                    "note": steal_note,
                },
                handle,
            )
        return True, f"lock acquired ({steal_note})"
    except OSError as exc:
        return False, f"cannot create lock: {exc}"
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(
            {"pid": os.getpid(), "actor": actor, "started": _stamp(), "started_epoch": now},
            handle,
        )
    return True, "lock acquired"


def release_lock(root: Path = ROOT) -> None:
    import contextlib

    with contextlib.suppress(OSError):
        lock_path(root).unlink()


def dirty_files(root: Path = ROOT) -> set[str]:
    """Repo-relative paths with uncommitted changes (tracked + untracked)."""
    try:
        modified = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    out = set()
    for blob in ((modified.stdout or ""), (untracked.stdout or "")):
        out.update(line.strip() for line in blob.splitlines() if line.strip())
    return out


def unit_files(unit_id: str) -> list[str]:
    """Production files a unit migration may write (for dirty-tree guard)."""
    from ..registry import seed_units
    from ..validator import BRIDGES

    files: list[str] = []
    for unit in seed_units():
        if unit.unit_id == unit_id:
            if unit.python_source:
                files.append(unit.python_source)
            if unit.rust_target:
                files.append(unit.rust_target)
    files.extend(BRIDGES.get(unit_id, ()))
    return sorted(set(files))


def is_ui_unit(unit_id: str, root: Path = ROOT) -> bool:
    """True when the unit is a Slint-track UI surface (pipeline excluded)."""
    from ..registry import unit_by_id

    unit = unit_by_id(unit_id)
    if unit is None:
        return False
    if unit.python_source.startswith(NATIVE_UI_PREFIXES):
        return True
    try:
        text = (root / unit.python_source).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    return any(marker in text for marker in QT_MARKERS)


def run_background(
    units: list[str] | None = None,
    max_units: int | None = None,
    include_dirty: bool = False,
    actor: str = "background-worker",
    root: Path = ROOT,
    runner=None,
) -> WorkerOutcome:
    """Process the migration queue once. Never touches UI units."""
    from . import pipeline as _pipeline

    outcome = WorkerOutcome(started=_stamp())
    ok, note = acquire_lock(root, actor)
    outcome.lock_note = note
    if not ok:
        outcome.finished = _stamp()
        return outcome
    try:
        targets = list(units) if units else [entry.unit_id for entry in blocked_order()]
        if max_units is not None:
            targets = targets[:max_units]
        dirty = set() if include_dirty else dirty_files(root)
        run = runner or _pipeline.run_unit
        for unit_id in targets:
            if is_ui_unit(unit_id, root):
                outcome.skipped.append(f"{unit_id}: Slint track owns UI (pipeline excluded)")
                continue
            touched = [path for path in unit_files(unit_id) if path in dirty]
            if touched:
                outcome.skipped.append(f"{unit_id}: uncommitted user changes in {touched}")
                continue
            try:
                outcome.outcomes.append(run(unit_id))
            except Exception as exc:  # noqa: BLE001 — autonomy continues with evidence
                outcome.outcomes.append(AgentOutcome(unit_id, "FAILED", (), (str(exc)[:300],)))
            outcome.processed += 1
    finally:
        release_lock(root)
        outcome.finished = _stamp()
    return outcome


__all__ = [
    "WorkerOutcome",
    "acquire_lock",
    "release_lock",
    "dirty_files",
    "unit_files",
    "is_ui_unit",
    "run_background",
    "lock_path",
]
