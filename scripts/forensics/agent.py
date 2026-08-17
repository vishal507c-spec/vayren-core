"""Agent lifecycle events: read the coding agent's own timestamped log.

The opencode agent writes every turn boundary to
`~/.local/share/opencode/log/opencode.log`:

    timestamp=... message=process session.id=... messageID=...   -> turn start
    timestamp=... message="exiting loop" session.id=...          -> turn end
    timestamp=... message="stream error" ... session.id=...      -> abnormal end

A coding task == one agent turn: [first process after the previous exit,
exit]. These are REAL lifecycle timestamps (UTC, converted to local) —
no manual task-start/task-end commands are needed.

If the log is unavailable (different agent, different machine), the
forensics system falls back to evidence-based session boundaries.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

AGENT_LOG_ENV = "VAYREN_FORENSICS_AGENT_LOG"

_PROCESS_RE = re.compile(r"timestamp=([^ ]+) .*message=process session\.id=(\S+) messageID=(\S+)")
_EXIT_RE = re.compile(r'timestamp=([^ ]+) .*message="exiting loop" session\.id=(\S+)')
_ERROR_RE = re.compile(r"timestamp=([^ ]+) .*message=\"stream error\".*session\.id=(\S+)")


@dataclass
class Turn:
    """One agent turn = one coding task. start = first process of the turn."""

    session_id: str
    msg_id: str
    start: datetime
    end: datetime | None = None


def agent_log_path() -> Path | None:
    """The agent's lifecycle log, or None when this environment has none."""
    override = os.environ.get(AGENT_LOG_ENV)
    if override:
        path = Path(override)
        return path if path.exists() else None
    default = Path.home() / ".local" / "share" / "opencode" / "log" / "opencode.log"
    return default if default.exists() else None


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def parse_turns(path: Path) -> list[Turn]:
    """All turns in the log, ordered by start time (all sessions).

    A turn opens on the first `process` event of a session and closes on the
    next `exiting loop` (or `stream error`) of that same session. Additional
    `process` events inside an open turn belong to the same turn.
    """
    open_turn: dict[str, Turn] = {}
    turns: list[Turn] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                match = _PROCESS_RE.search(line)
                if match:
                    ts = _parse_ts(match.group(1))
                    session = match.group(2)
                    msg_id = match.group(3)
                    if session not in open_turn:
                        open_turn[session] = Turn(session, msg_id, ts)
                        turns.append(open_turn[session])
                    continue
                match = _EXIT_RE.search(line) or _ERROR_RE.search(line)
                if match:
                    ts = _parse_ts(match.group(1))
                    session = match.group(2)
                    turn = open_turn.pop(session, None)
                    if turn is not None and turn.end is None:
                        turn.end = ts
    except OSError:
        return []
    return turns


def current_turn(path: Path | None) -> Turn | None:
    """The latest turn in the log (the one in progress, or the last one)."""
    if path is None:
        return None
    turns = parse_turns(path)
    return turns[-1] if turns else None
