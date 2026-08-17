"""Forensics store: JSONL event log, tree snapshots, git evidence. Stdlib only."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

REPO_SKIP_DIRS = {
    ".git",
    ".forensics",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".pyright",
    ".idea",
    ".vscode",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "htmlcov",
    "logs",
}

DATA_ROOT = ".forensics"
TASKS_DIR = "tasks"


def repo_root() -> Path:
    """Repository root: nearest ancestor directory containing .git."""
    path = Path.cwd().resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return path


ROOT = repo_root()


def now_iso() -> str:
    """Current local time as ISO-8601 with offset, millisecond precision."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def state_path() -> Path:
    """Global forensics state: the currently open measurement session."""
    return ROOT / DATA_ROOT / "state.json"


def load_state() -> dict:
    path = state_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    (ROOT / DATA_ROOT).mkdir(parents=True, exist_ok=True)
    with state_path().open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)


def task_dir(task_id: str) -> Path:
    return ROOT / DATA_ROOT / TASKS_DIR / task_id


def events_path(task_id: str) -> Path:
    return task_dir(task_id) / "events.jsonl"


def append_event(task_id: str, event: dict) -> None:
    task_dir(task_id).mkdir(parents=True, exist_ok=True)
    with events_path(task_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_events(task_id: str) -> list[dict]:
    path = events_path(task_id)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def save_json(task_id: str, name: str, data: dict) -> None:
    with (task_dir(task_id) / name).open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def load_json(task_id: str, name: str, default: dict) -> dict:
    path = task_dir(task_id) / name
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def tree_scan(root: Path = ROOT) -> dict[str, tuple[float, int]]:
    """Walk the repo; return {posix_relpath: (mtime, size)} for every file."""
    result: dict[str, tuple[float, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in REPO_SKIP_DIRS]
        for filename in filenames:
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            try:
                stat = full.stat()
            except OSError:
                continue
            result[rel] = (stat.st_mtime, stat.st_size)
    return result


def git_numstat() -> dict[str, tuple[int, int]]:
    """Working-tree diff vs HEAD: {path: (added, removed)}. Empty on failure."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    result: dict[str, tuple[int, int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[0] == "-" or parts[1] == "-":
            continue
        result[parts[2]] = (int(parts[0]), int(parts[1]))
    return result
