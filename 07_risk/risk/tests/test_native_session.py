"""Parity: Rust risk session/clock gates vs frozen Python references.

The window oracle is a verbatim copy of the pre-migration Python rule; the
clock oracle uses the same stdlib parser the old code called. Full-form
timestamps must agree exactly; the documented divergences (partial ISO forms,
non-string input) are pinned so they stay visible.
"""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime
from typing import Any

import pytest

from risk import native_session

NOW = 1_767_700_000.0


def _ref_within(timestamp: object, start: str | None, end: str | None) -> bool:
    if start is None and end is None:
        return True
    hhmm = str(timestamp)[11:16]
    if start is not None and hhmm < start:
        return False
    return not (end is not None and hhmm > end)


def _ref_clock(timestamp: object, now_epoch: float, skew: float) -> bool:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        event_epoch = parsed.timestamp()
    except Exception:
        return False
    return event_epoch <= now_epoch + skew


def test_window_rule_matches_the_frozen_gate() -> None:
    stamps = (
        "2026-01-06T09:15:00+00:00",
        "2026-01-06T15:30:00+00:00",
        "2026-01-06 18:00:00",
        "2026-01-06T04:00:00",
        "2026-01-06",
        "x",
        "",
        "2026-01-06é09:15:00",
    )
    bounds = (None, "", "00:00", "09:15", "15:30", "23:59", "é")
    for stamp, (start, end) in itertools.product(stamps, itertools.product(bounds, bounds)):
        expected = _ref_within(stamp, start, end)
        assert native_session.within_session(stamp, start, end) == expected, (stamp, start, end)


def test_empty_bounds_are_real_bounds_not_absent_ones() -> None:
    # `SessionRules(start="")` used to compare against an empty string, which
    # every HH:MM slice sorts above, and `end=""` below: the kernel agrees.
    assert native_session.within_session("2026-01-06T09:30:00+00:00", "", None) is True
    assert native_session.within_session("2026-01-06T09:30:00+00:00", None, "") is False
    assert native_session.within_session("2026-01-06T09:30:00+00:00", None, None) is True


def test_clock_rule_matches_stdlib_on_full_timestamps() -> None:
    stamps = (
        "2026-01-06T09:30:00",
        "2026-01-06 09:30:00",
        "2026-01-06t09:30:00",
        "2026-01-06T09:30:00Z",
        "2026-01-06T09:30:00+00:00",
        "2026-01-06T09:30:00-00:00",
        "2026-01-06T09:30:00+05:30",
        "2026-01-06T09:30:00+0530",
        "2026-01-06T09:30:00+05",
        "2026-01-06T09:30:00.123456",
        "2026-01-06T09:30:00.5+00:00",
        "2024-02-29T09:30:00",
        "2026-01-05 09:15:00",
        "2026-01-06T13:06:40+00:00",
        "2026-01-06T13:06:41+00:00",
        "2026-01-06T13:11:39+00:00",
        "2026-01-06T13:11:41+00:00",
        "2026-02-30T09:30:00",
        "2026-02-29T09:30:00",
        "2026-04-31T09:30:00",
        "2026-13-01T09:30:00",
        "2026-01-06T25:30:00",
        "2026-01-06T09:61:00",
        "2026-01-06T09:30:60",
        "2026-01-06T09:30:59.999999",
        "0000-01-01T00:00:00",
        "2999-01-01T00:00:00+00:00",
        "not-a-time",
        " 2026-01-06T09:30:00",
        "2026-1-6T9:30:00",
        "2026-01-06T09:30:00+99:00",
        "2026-01-06T09:30:00.",
    )
    for stamp, (now, skew) in itertools.product(stamps, ((NOW, 300.0), (NOW, 0.0), (NOW, 86400.0))):
        expected = _ref_clock(stamp, now, skew)
        assert native_session.clock_sane(stamp, now, skew) == expected, (stamp, now, skew)


def test_clock_rule_fuzzes_against_stdlib() -> None:
    rng = random.Random(20260920)
    for _ in range(500):
        year = rng.choice((2020, 2024, 2026, 2027))
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        stamp = (
            f"{year:04d}-{month:02d}-{day:02d}T{rng.randint(0, 23):02d}:"
            f"{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
        )
        if rng.random() < 0.5:
            stamp += f".{rng.randint(0, 999999):06d}"
        stamp += rng.choice(("", "Z", "+00:00", "+05:30", "-08:00", "+0530", "+05"))
        now = NOW + rng.uniform(-86_400.0, 86_400.0)
        skew = rng.choice((0.0, 300.0, 3600.0))
        expected = _ref_clock(stamp, now, skew)
        assert native_session.clock_sane(stamp, now, skew) == expected, (stamp, now, skew)


def test_partial_iso_forms_now_fail_closed() -> None:
    """Documented divergence: the kernel parses fewer shapes than `fromisoformat`.

    Both directions matter only as deny-vs-allow, and the kernel denies — the
    safe side of a risk gate. Real bar/event stamps are full ISO datetimes
    (`candle_db` writes `%Y-%m-%d %H:%M:00`, `datetime.now(UTC).isoformat()`).
    """
    partial = (
        "2026-01-06",
        "2026-01-06T09",
        "2026-01-06T09:30",
        "20260106",
        "20260106T093000",
        "2026-01-06T09:30:00,500",
        "2026-01-06é09:30:00",
    )
    for stamp in partial:
        assert _ref_clock(stamp, NOW, 300.0) is True, stamp
        assert native_session.clock_sane(stamp, NOW, 300.0) is False, stamp


def test_gates_require_text_not_stringified_junk() -> None:
    """Documented divergence: the old code called ``str()`` on the timestamp."""
    bad_stamps: tuple[Any, ...] = (1_767_700_000, None, 9.5)
    bad_bounds: tuple[Any, ...] = (915, 9.15)
    for stamp in bad_stamps:
        with pytest.raises(TypeError, match="must be str"):
            native_session.within_session(stamp, None, None)
        with pytest.raises(TypeError, match="must be str"):
            native_session.clock_sane(stamp, NOW, 300.0)
    for bound in bad_bounds:
        with pytest.raises(TypeError, match="session start must be str"):
            native_session.within_session("2026-01-06T09:30:00", bound, None)
        with pytest.raises(TypeError, match="session end must be str"):
            native_session.within_session("2026-01-06T09:30:00", None, bound)
