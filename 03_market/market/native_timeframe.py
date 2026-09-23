"""Rust-backed timeframe ladder bridge (AI_ENTRY.md §1: Market/Data).

The ladder, its label grammar, the aggregation-availability rule and the
higher-timeframe query plan (plain-vs-aggregate fallback, base-row over-fetch
budget, newest-bars tail) live in Rust (``rust/vayren-core``, ``market``
module) and reach Python only through these ``vy_timeframe_*`` exports. This
module holds no ladder and no query rule of its own — it marshals UTF-8
strings across the boundary and maps the kernel's answers back.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_REQUIRED_EXPORTS = (
    "vy_timeframe_ladder_count",
    "vy_timeframe_ladder_seconds",
    "vy_timeframe_ladder_label",
    "vy_timeframe_seconds",
    "vy_timeframe_label",
    "vy_timeframe_generate_label",
    "vy_timeframe_available",
    "vy_timeframe_fetch_plan",
)

for _name in _REQUIRED_EXPORTS:
    if not hasattr(_lib, _name):
        raise NativeBridgeError(
            f"native library has no timeframe kernel ({_name} missing). "
            "Rebuild: `python scripts/build_rust.py`"
        )

_lib.vy_timeframe_fetch_plan.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.POINTER(ctypes.c_int64),
]
_lib.vy_timeframe_fetch_plan.restype = ctypes.c_int32
# Same house rule as native_aggregate: untyped ints arrive as 4-byte C ints
# while the kernel reads i64/usize slots (upper half is stack garbage).
_lib.vy_timeframe_ladder_count.argtypes = []
_lib.vy_timeframe_ladder_count.restype = ctypes.c_int32
_lib.vy_timeframe_ladder_seconds.argtypes = [ctypes.c_int32]
_lib.vy_timeframe_ladder_seconds.restype = ctypes.c_int64
_lib.vy_timeframe_ladder_label.argtypes = [ctypes.c_int32, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_timeframe_ladder_label.restype = ctypes.c_int32
_lib.vy_timeframe_seconds.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_timeframe_seconds.restype = ctypes.c_int64
_lib.vy_timeframe_label.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_timeframe_label.restype = ctypes.c_int32
_lib.vy_timeframe_generate_label.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_timeframe_generate_label.restype = ctypes.c_int32
_lib.vy_timeframe_available.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_timeframe_available.restype = ctypes.c_int32


def _read(needed: int, call) -> str:
    """Drain a two-call buffer protocol: probe, allocate, refill.

    ``needed`` is the byte length the kernel reported (0 means "no answer").
    """
    if needed < 0:
        raise NativeBridgeError(f"timeframe kernel rejected the call: {needed}")
    if needed == 0:
        return ""
    buf = ctypes.create_string_buffer(needed + 1)
    if call(buf, needed + 1) != needed:
        raise NativeBridgeError("timeframe kernel length drift")
    # `buf.value` would stop at an embedded NUL; take the exact payload span.
    return buf.raw[:needed].decode("utf-8")


def _encode(name: str) -> tuple[bytes, int]:
    if not isinstance(name, str):
        raise TypeError(f"timeframe label must be str, not {type(name).__name__}")
    payload = name.encode("utf-8")
    return payload, len(payload)


def ladder() -> tuple[str, ...]:
    """Ladder labels in ascending granularity order, straight from the kernel."""
    count = int(_lib.vy_timeframe_ladder_count())
    labels = []
    for index in range(count):
        needed = int(_lib.vy_timeframe_ladder_label(index, None, 0))
        labels.append(
            _read(
                needed,
                lambda buf, cap, index=index: int(_lib.vy_timeframe_ladder_label(index, buf, cap)),
            )
        )
    return tuple(labels)


def ladder_seconds(index: int) -> int:
    """Granularity of one ladder entry; the kernel answers -1 out of range."""
    seconds = int(_lib.vy_timeframe_ladder_seconds(index))
    if seconds < 0:
        raise IndexError(f"timeframe ladder has no entry {index}")
    return seconds


def seconds_of(name: str) -> int | None:
    """Seconds for a timeframe label (ladder or generated), or None if invalid."""
    payload, length = _encode(name)
    seconds = int(_lib.vy_timeframe_seconds(payload, length))
    if seconds < 0:
        raise NativeBridgeError("timeframe kernel could not decode the label")
    return seconds or None


def name_of(seconds: int) -> str | None:
    """Ladder label for a granularity, or None if it is not in the ladder."""
    needed = int(_lib.vy_timeframe_label(int(seconds), None, 0))
    return (
        _read(needed, lambda buf, cap: int(_lib.vy_timeframe_label(int(seconds), buf, cap))) or None
    )


def generate_label(seconds: int) -> str:
    """Human label for a granularity outside the ladder, derived from seconds."""
    needed = int(_lib.vy_timeframe_generate_label(int(seconds), None, 0))
    return _read(
        needed,
        lambda buf, cap: int(_lib.vy_timeframe_generate_label(int(seconds), buf, cap)),
    )


def available_timeframes(base_seconds: int) -> tuple[str, ...]:
    """Timeframes the database can produce from its detected base bar duration."""
    needed = int(_lib.vy_timeframe_available(int(base_seconds), None, 0))
    text = _read(
        needed,
        lambda buf, cap: int(_lib.vy_timeframe_available(int(base_seconds), buf, cap)),
    )
    return tuple(text.split(",")) if text else ()


@dataclass(frozen=True)
class FetchPlan:
    """How one timeframe query reads: plain rows or aggregated buckets."""

    plain: bool
    seconds: int
    row_budget: int | None
    keep_last: int | None


def _word(value: int | None) -> int:
    return -1 if value is None else int(value)


def fetch_plan(label: str, base: int | None, limit: int | None) -> FetchPlan:
    """Plan a `[label, base, limit]` candle read; the kernel decides every rule.

    ``base`` is the detected base bar duration, ``limit`` the caller's bar
    count (``None`` = whole history).
    """
    payload, length = _encode(label)
    slots = (ctypes.c_int64 * 4)()
    code = int(_lib.vy_timeframe_fetch_plan(payload, length, _word(base), _word(limit), slots))
    if code != 1:
        raise NativeBridgeError(f"timeframe plan kernel rejected the call ({code})")
    plain, seconds, row_budget, keep_last = (int(slot) for slot in slots)
    return FetchPlan(
        plain=bool(plain),
        seconds=seconds,
        row_budget=None if row_budget < 0 else row_budget,
        keep_last=None if keep_last < 0 else keep_last,
    )


__all__ = [
    "FetchPlan",
    "available_timeframes",
    "fetch_plan",
    "generate_label",
    "ladder",
    "ladder_seconds",
    "name_of",
    "seconds_of",
]
