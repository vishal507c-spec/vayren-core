"""Server code status verification (tri-SHA equality).

Verifies whether the running service matches GitHub origin/main and the
deployed working directory on EC2:
1. github_sha: Latest commit SHA on GitHub origin/main (via git ls-remote).
2. deployed_sha: Current commit SHA of working directory on EC2 (via git rev-parse HEAD).
3. running_sha: In-memory commit SHA of the running Python process captured at launch.

Status rule:
- "SERVER CODE: UPDATED" if and only if github_sha == deployed_sha == running_sha
  and all three are non-empty valid git commit hashes.
- "SERVER CODE: NOT UPDATED" on ANY mismatch, missing/empty SHA, git error,
  network timeout, or verification failure.
"""

from __future__ import annotations

import datetime
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("remote.server_code_status")

STATUS_UPDATED = "SERVER CODE: UPDATED"
STATUS_NOT_UPDATED = "SERVER CODE: NOT UPDATED"

DEFAULT_CACHE_TTL_S = 30.0
GIT_TIMEOUT_S = 5.0

_LOCK = threading.Lock()
_IN_MEMORY_RUNNING_SHA: str | None = None
_CACHED_STATUS: dict[str, Any] | None = None
_LAST_CHECKED_MONOTONIC: float = 0.0


def _is_valid_sha(sha: str | None) -> bool:
    """Validate 40-character hexadecimal git commit SHA."""
    if not isinstance(sha, str):
        return False
    s = sha.strip()
    return len(s) == 40 and all(c in "0123456789abcdefABCDEF" for c in s)


def _resolve_repo_dir(repo_dir: str | Path | None = None) -> Path:
    """Resolve repository directory root safely."""
    if repo_dir:
        p = Path(repo_dir).resolve()
        if (p / ".git").exists():
            return p
    # Try directory derived from this file (src/remote/server_code_status.py -> repo root)
    try:
        candidate = Path(__file__).resolve().parents[2]
        if (candidate / ".git").exists():
            return candidate
    except Exception:  # noqa: BLE001
        pass
    # Common EC2 path fallback
    ec2_candidate = Path("/home/ubuntu/vayren-core")
    if (ec2_candidate / ".git").exists():
        return ec2_candidate
    return Path.cwd().resolve()


def _read_head_commit_sha(repo_dir: Path) -> str:
    """Read the current HEAD commit hash from repo_dir."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            if _is_valid_sha(sha):
                return sha.lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read HEAD commit SHA from %s: %s", repo_dir, exc)
    return ""


def get_running_sha(repo_dir: str | Path | None = None) -> str:
    """Return the in-memory version SHA captured at process startup."""
    global _IN_MEMORY_RUNNING_SHA
    with _LOCK:
        if _IN_MEMORY_RUNNING_SHA and _is_valid_sha(_IN_MEMORY_RUNNING_SHA):
            return _IN_MEMORY_RUNNING_SHA
        env_sha = os.environ.get("VAYREN_RUNNING_SHA", "").strip().lower()
        if _is_valid_sha(env_sha):
            _IN_MEMORY_RUNNING_SHA = env_sha
            return _IN_MEMORY_RUNNING_SHA
        resolved = _resolve_repo_dir(repo_dir)
        _IN_MEMORY_RUNNING_SHA = _read_head_commit_sha(resolved)
        return _IN_MEMORY_RUNNING_SHA


def set_running_sha_for_testing(sha: str | None) -> None:
    """Override in-memory running SHA for tests."""
    global _IN_MEMORY_RUNNING_SHA
    with _LOCK:
        _IN_MEMORY_RUNNING_SHA = sha.strip().lower() if sha else None


def fetch_github_origin_sha(repo_dir: Path) -> str:
    """Fetch the latest commit SHA of origin/main from GitHub (read-only)."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "origin", "refs/heads/main"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
        if proc.returncode == 0:
            out = proc.stdout.strip()
            if out:
                sha = out.split()[0].strip().lower()
                if _is_valid_sha(sha):
                    return sha
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch origin/main SHA from GitHub: %s", exc)
    return ""


def evaluate_code_status(
    github_sha: str, deployed_sha: str, running_sha: str
) -> tuple[str, bool, str]:
    """Pure evaluation of tri-SHA equality.

    Returns (status_text, is_updated, detail_reason).
    Only returns STATUS_UPDATED when all three SHAs are non-empty valid hashes
    and are identical.
    """
    g = (github_sha or "").strip().lower()
    d = (deployed_sha or "").strip().lower()
    r = (running_sha or "").strip().lower()

    if not _is_valid_sha(g):
        return STATUS_NOT_UPDATED, False, "GitHub origin/main SHA unreachable or invalid"
    if not _is_valid_sha(d):
        return STATUS_NOT_UPDATED, False, "Deployed working tree SHA unavailable or invalid"
    if not _is_valid_sha(r):
        return STATUS_NOT_UPDATED, False, "In-memory running service SHA unavailable or invalid"

    if g != d:
        return STATUS_NOT_UPDATED, False, f"Deployed SHA ({d[:7]}) does not match GitHub ({g[:7]})"
    if r != d:
        return (
            STATUS_NOT_UPDATED,
            False,
            f"Running process SHA ({r[:7]}) does not match deployed ({d[:7]})",
        )
    if r != g:
        return (
            STATUS_NOT_UPDATED,
            False,
            f"Running process SHA ({r[:7]}) does not match GitHub ({g[:7]})",
        )

    return STATUS_UPDATED, True, f"All versions in sync: {g[:7]}"


def get_server_code_status(
    repo_dir: str | Path | None = None,
    force: bool = False,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
) -> dict[str, Any]:
    """Return the verified server code status dictionary.

    Uses cached result if within cache_ttl_s and force is False.
    """
    global _CACHED_STATUS, _LAST_CHECKED_MONOTONIC
    now_mono = time.monotonic()
    with _LOCK:
        if (
            not force
            and _CACHED_STATUS is not None
            and (now_mono - _LAST_CHECKED_MONOTONIC) < cache_ttl_s
        ):
            return dict(_CACHED_STATUS)

    resolved = _resolve_repo_dir(repo_dir)
    running_sha = get_running_sha(resolved)
    deployed_sha = _read_head_commit_sha(resolved)
    github_sha = fetch_github_origin_sha(resolved)

    status_text, is_updated, detail = evaluate_code_status(
        github_sha=github_sha,
        deployed_sha=deployed_sha,
        running_sha=running_sha,
    )

    result = {
        "status": status_text,
        "updated": is_updated,
        "github_sha": github_sha,
        "deployed_sha": deployed_sha,
        "running_sha": running_sha,
        "detail": detail,
        "last_checked": datetime.datetime.now(datetime.UTC).isoformat(),
    }

    with _LOCK:
        _CACHED_STATUS = dict(result)
        _LAST_CHECKED_MONOTONIC = time.monotonic()

    return result


def clear_cache_for_testing() -> None:
    """Clear cached state for deterministic unit testing."""
    global _CACHED_STATUS, _LAST_CHECKED_MONOTONIC, _IN_MEMORY_RUNNING_SHA
    with _LOCK:
        _CACHED_STATUS = None
        _LAST_CHECKED_MONOTONIC = 0.0
        _IN_MEMORY_RUNNING_SHA = None
