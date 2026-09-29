"""Bar payload validation pins (fail-closed construction)."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in ("01_core", "03_market"):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pytest  # noqa: E402

from market.models.bar import Bar  # noqa: E402


def _bar(**overrides) -> Bar:
    fields: dict = {
        "symbol": "RELIANCE",
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 103.0,
        "volume": 1000,
        "timestamp": "2026-01-05 09:15:00",
    }
    fields.update(overrides)
    return Bar(**fields)


def test_valid_bar_and_timeframe_spellings() -> None:
    assert _bar().bar_size == "1d"
    assert _bar(bar_size="5m").bar_size == "5m"
    assert _bar(bar_size="1D").bar_size == "1D"
    assert _bar(bar_size="90m").bar_size == "90m"
    assert _bar(volume=0, trades=0, vwap=101.5, source="nse").volume == 0


def test_bar_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _bar().close = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbol": ""},
        {"symbol": "   "},
        {"symbol": 123},
        {"open": float("nan")},
        {"close": float("inf")},
        {"high": -1.0},
        {"low": -0.5},
        {"open": "100"},
        {"volume": True},
        {"high": 98.0},  # high < low
        {"open": 98.0},  # open below low
        {"open": 106.0},  # open above high
        {"close": 50.0},  # close below low
        {"volume": -1},
        {"volume": 1.5},
        {"trades": -2},
        {"vwap": float("nan")},
        {"timestamp": "not-a-time"},
        {"timestamp": 20260105},
        {"bar_size": "bogus"},
        {"bar_size": ""},
        {"bar_size": 300},
        {"source": None},
    ],
)
def test_bar_violations_raise_value_error(overrides: dict) -> None:
    with pytest.raises(ValueError):
        _bar(**overrides)
