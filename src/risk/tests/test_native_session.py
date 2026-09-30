"""Contract tests for risk.native_session (trading window, clock sanity)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from risk import native_session as ns  # noqa: E402


def test_within_session() -> None:
    # 10:00 is within 09:15 .. 15:30
    assert ns.within_session("2026-01-05T10:00:00", start="09:15", end="15:30") is True

    # 08:30 is before 09:15
    assert ns.within_session("2026-01-05T08:30:00", start="09:15", end="15:30") is False

    # 16:00 is after 15:30
    assert ns.within_session("2026-01-05T16:00:00", start="09:15", end="15:30") is False

    # Unbounded session
    assert ns.within_session("2026-01-05T10:00:00", start=None, end=None) is True


def test_clock_sane() -> None:
    # 2026-01-05T10:00:00Z -> epoch 1767607200
    now = 1767607200.0
    stamp = "2026-01-05T10:00:00"

    # Within 5s skew
    assert ns.clock_sane(stamp, now_epoch=now, max_future_skew_seconds=5.0) is True

    # More than 100s future skew
    future_stamp = "2026-01-05T10:10:00"
    assert ns.clock_sane(future_stamp, now_epoch=now, max_future_skew_seconds=5.0) is False
