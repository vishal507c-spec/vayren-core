"""Trading-session and clock rules — deterministic, no network time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC


@dataclass(frozen=True)
class SessionRules:
    """Allowed trading window. ``None`` bounds disable the gate entirely."""

    start: str | None = None  # "HH:MM" inclusive
    end: str | None = None  # "HH:MM" inclusive


def _hhmm(timestamp: str) -> str | None:
    """Extract HH:MM from an ISO-like timestamp, else None (fail-closed input)."""
    try:
        return str(timestamp)[11:16]
    except Exception:
        return None


def within_session(timestamp: str, rules: SessionRules) -> bool:
    """True when the timestamp falls inside the allowed window."""
    if rules.start is None and rules.end is None:
        return True
    hhmm = _hhmm(timestamp)
    if hhmm is None:
        return False
    if rules.start is not None and hhmm < rules.start:
        return False
    return not (rules.end is not None and hhmm > rules.end)


def clock_sane(timestamp: str, now_epoch: float, max_future_skew_seconds: float = 300.0) -> bool:
    """Reject event timestamps impossibly far in the future (clock anomaly).

    ``now_epoch`` must be measured on the same wall-clock basis as the event
    timestamps (callers construct both, keeping the check deterministic and
    timezone-explicit). Unparseable timestamps fail closed.
    """
    try:
        from datetime import datetime

        text = str(timestamp).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        event_epoch = parsed.timestamp()
    except Exception:
        return False
    return event_epoch <= now_epoch + max_future_skew_seconds
