"""Trading-session and clock rules — deterministic, no network time.

The window comparison and the timestamp/clock-skew rule are Rust authority
(`rust/vayren-core/src/risk_engine.rs`, reached through
`risk.native_session`); this module keeps the public shape its callers import
and holds no rule of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from risk.native_session import clock_sane as _kernel_clock_sane
from risk.native_session import within_session as _kernel_within_session


@dataclass(frozen=True)
class SessionRules:
    """Allowed trading window. ``None`` bounds disable the gate entirely."""

    start: str | None = None  # "HH:MM" inclusive
    end: str | None = None  # "HH:MM" inclusive


def within_session(timestamp: str, rules: SessionRules) -> bool:
    """True when the timestamp falls inside the allowed window."""
    return _kernel_within_session(timestamp, rules.start, rules.end)


def clock_sane(timestamp: str, now_epoch: float, max_future_skew_seconds: float = 300.0) -> bool:
    """Reject event timestamps impossibly far in the future (clock anomaly).

    ``now_epoch`` must be measured on the same wall-clock basis as the event
    timestamps (callers construct both, keeping the check deterministic and
    timezone-explicit). Unparseable timestamps fail closed.
    """
    return _kernel_clock_sane(timestamp, now_epoch, max_future_skew_seconds)
