"""Contract tests for backtest.native_runner (bounds, workers)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from backtest import native_runner as nr  # noqa: E402


def test_window_bounds_valid() -> None:
    lower, upper = nr.window_bounds("2026-01-01", "2026-01-10")
    # Kernel prepends margin before start_date and appends margin after end_date
    assert lower < "2026-01-01"
    assert upper > "2026-01-10"


def test_window_bounds_invalid_date_raises() -> None:
    with pytest.raises(ValueError, match="invalid backtest date range"):
        nr.window_bounds("invalid-date", "2026-01-10")


def test_default_workers_positive() -> None:
    workers = nr.default_workers(4)
    assert workers >= 1

    default = nr.default_workers(0)
    assert default >= 1
