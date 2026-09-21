"""Timeframe ladder — canonical candidate granularities and their seconds.

The ladder is the universe of candidate timeframes. Whether a candidate
actually exists for a symbol is always decided from the SQLite database
(see ``available_timeframes``) — never hardcoded as a fixed available list.

The table, the label grammar and the availability rule are owned by Rust
(``rust/vayren-core``, ``market`` module); this module is the domain facade
callers import, so no Python file carries a second copy of the ladder.
"""

from __future__ import annotations

from market.native_timeframe import available_timeframes as _available
from market.native_timeframe import generate_label as _generate_label
from market.native_timeframe import ladder as _ladder
from market.native_timeframe import name_of as _name_of
from market.native_timeframe import seconds_of as _seconds_of

TIMEFRAME_LADDER: tuple[str, ...] = _ladder()


def timeframe_seconds(name: str) -> int | None:
    """Seconds for a timeframe label (ladder or generated), or None if invalid."""
    return _seconds_of(name)


def timeframe_name(seconds: int) -> str | None:
    """Ladder label for a granularity, or None if it is not in the ladder."""
    return _name_of(seconds)


def generate_label(seconds: int) -> str:
    """Human label for a granularity outside the ladder, derived from seconds."""
    return _generate_label(seconds)


def available_timeframes(base_seconds: int) -> tuple[str, ...]:
    """Timeframes the database can produce from its detected base bar duration.

    The base itself is always included, under its ladder label or a generated
    one. Never cached — callers re-run detection against SQLite.
    """
    return _available(base_seconds)
