"""Contract tests for backtest.native_replay (window slicing)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from backtest import native_replay as nr  # noqa: E402


def test_window_date_slicing() -> None:
    stamps = [
        "2026-01-01T09:15:00",
        "2026-01-02T09:15:00",
        "2026-01-03T09:15:00",
        "2026-01-04T09:15:00",
    ]
    # Slicing from 2026-01-02 to 2026-01-03 inclusive
    indices = nr.window(stamps, start_date="2026-01-02", end_date="2026-01-03")
    assert indices == (1, 2)


def test_window_unbounded() -> None:
    stamps = [
        "2026-01-01T09:15:00",
        "2026-01-02T09:15:00",
    ]
    # None bounds keep everything
    indices = nr.window(stamps, start_date=None, end_date=None)
    assert indices == (0, 1)


def test_window_no_match() -> None:
    stamps = [
        "2026-01-01T09:15:00",
        "2026-01-02T09:15:00",
    ]
    indices = nr.window(stamps, start_date="2026-02-01", end_date="2026-02-10")
    assert indices == ()
