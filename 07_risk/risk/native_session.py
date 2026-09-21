"""Rust-backed risk session and clock gates (AI_ENTRY.md §1: Risk /
Performance-critical decision kernels).

The `HH:MM` window comparison and the ISO-timestamp/clock-skew rule live in
`rust/vayren-core/src/risk_engine.rs`; this module marshals UTF-8 timestamps
across the boundary and turns the kernel's verdict code into a bool. No risk
policy lives here — the gate answers, the engine decides what to do with it.
"""

from __future__ import annotations

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_PASS = 1
_FAIL = 0
# A bound length of -1 is how Python's `None` crosses; `None`+len 0 would be
# the empty-string bound, which Python treats as a real (always-failing) value.
_ABSENT = -1

_REQUIRED_EXPORTS = ("vy_risk_within_session", "vy_risk_clock_sane")

_missing_exports = [name for name in _REQUIRED_EXPORTS if not hasattr(_lib, name)]
if _missing_exports:
    raise NativeBridgeError(
        f"native library has no risk session kernel ({', '.join(_missing_exports)}). "
        "Rebuild: `python scripts/build_rust.py`"
    )


def _encode(value: str, what: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError(f"risk {what} must be str, not {type(value).__name__}")
    return value.encode("utf-8")


def _verdict(code: int) -> bool:
    if code == _PASS:
        return True
    if code == _FAIL:
        return False
    if code == -1:
        raise NativeBridgeError("native risk gate received undecodable UTF-8 text")
    raise NativeBridgeError(f"native risk gate call was malformed: {code}")


def within_session(timestamp: str, start: str | None, end: str | None) -> bool:
    """True when the timestamp falls inside the inclusive `HH:MM` window."""
    stamp = _encode(timestamp, "timestamp")
    low = None if start is None else _encode(start, "session start")
    high = None if end is None else _encode(end, "session end")
    code = int(
        _lib.vy_risk_within_session(
            stamp,
            len(stamp),
            low,
            _ABSENT if low is None else len(low),
            high,
            _ABSENT if high is None else len(high),
        )
    )
    return _verdict(code)


def clock_sane(timestamp: str, now_epoch: float, max_future_skew_seconds: float) -> bool:
    """True when the event timestamp is not impossibly far in the future."""
    stamp = _encode(timestamp, "timestamp")
    code = int(
        _lib.vy_risk_clock_sane(
            stamp,
            len(stamp),
            float(now_epoch),
            float(max_future_skew_seconds),
        )
    )
    return _verdict(code)


__all__ = ["clock_sane", "within_session"]
