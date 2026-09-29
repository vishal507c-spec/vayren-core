"""native_timeframe bridge pins (invalid->None, misuse->raise, caps)."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in ("01_core", "03_market"):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pytest  # noqa: E402

from market import native_timeframe as nt  # noqa: E402

# Validator boundary: tests never import core.native directly; the bridge
# carries the exact same error class object.
NativeBridgeError = nt.NativeBridgeError


def test_seconds_of_contract() -> None:
    assert nt.seconds_of("5m") == 300
    assert nt.seconds_of("1d") == 86400  # ladder match is case-insensitive
    assert nt.seconds_of("no-such-timeframe") is None  # invalid -> None, no raise
    with pytest.raises(TypeError):
        nt.seconds_of(300)  # type: ignore[arg-type]


def test_name_and_generate_guards() -> None:
    assert nt.name_of(60) == "1m"
    assert nt.name_of(12345) is None
    with pytest.raises(NativeBridgeError):
        nt.name_of(0)
    with pytest.raises(NativeBridgeError):
        nt.name_of(-60)
    assert nt.generate_label(120) == "2m"
    with pytest.raises(NativeBridgeError):
        nt.generate_label(0)


def test_fetch_plan_unknown_label_fails_closed() -> None:
    with pytest.raises(NativeBridgeError, match="unknown timeframe"):
        nt.fetch_plan("no-such-timeframe", 60, 10)
    plan = nt.fetch_plan("5m", 60, 10)
    assert plan.seconds == 300


def test_ladder_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(nt.ladder()) > 0
    monkeypatch.setattr(nt._lib, "vy_timeframe_ladder_count", lambda: 10001)
    with pytest.raises(NativeBridgeError, match="cap"):
        nt.ladder()


def test_decode_failure_is_bridge_error() -> None:
    needed = 2

    def _bad(buf, _cap: int) -> int:
        ctypes.memset(buf, 0xFF, needed)
        return needed

    with pytest.raises(NativeBridgeError, match="non-UTF-8"):
        nt._read(needed, _bad)
