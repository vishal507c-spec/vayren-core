"""Rust-backed timeframe aggregation kernel (AI_ENTRY.md §1: Market/Data
processing / Numerical calculations / Performance-critical).

Bucket grouping and single-pass OHLCV accumulation live in Rust
(`rust/vayren-core`, `aggregate` module). Python keeps timestamp parsing
and bar formatting (domain/IO); this module marshals plain integer/float
arrays across the boundary and maps buckets back.
"""

from __future__ import annotations

import ctypes
import struct
from array import array
from collections.abc import Sequence
from typing import Any

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_REQUIRED_EXPORTS = (
    "vy_agg_anchor_seconds",
    "vy_agg_bucket_start",
    "vy_agg_closed_count",
    "vy_agg_fold_tick",
)

_missing_exports = [name for name in _REQUIRED_EXPORTS if not hasattr(_lib, name)]
if _missing_exports:
    raise NativeBridgeError(
        f"native library has no streaming aggregation kernel ({', '.join(_missing_exports)}). "
        "Rebuild: `python scripts/build_rust.py`"
    )

# AggBucket layout: i32 day, i32 index, 5 x f64 (48 bytes, no padding).
_BUCKET_FORMAT = "ii5d"
_BUCKET_SIZE = struct.calcsize(_BUCKET_FORMAT)


def _i32_view(values: Sequence[int]) -> tuple[ctypes.Array, array]:
    packed = array("i", values)
    return (ctypes.c_int32 * len(packed)).from_buffer(packed), packed


def _f64_view(values: Sequence[float]) -> tuple[ctypes.Array, array]:
    packed = array("d", values)
    return (ctypes.c_double * len(packed)).from_buffer(packed), packed


class _AggBucket(ctypes.Structure):
    _fields_ = [
        ("day", ctypes.c_int32),
        ("index", ctypes.c_int32),
        ("open", ctypes.c_double),
        ("high", ctypes.c_double),
        ("low", ctypes.c_double),
        ("close", ctypes.c_double),
        ("volume", ctypes.c_double),
    ]


_I32_P = ctypes.POINTER(ctypes.c_int32)
_I64_P = ctypes.POINTER(ctypes.c_int64)
_F64_P = ctypes.POINTER(ctypes.c_double)

# Explicit signatures (house rule: every FFI binding declares argtypes).
# Without them ctypes passes Python ints as 4-byte C ints while the kernel
# reads usize/i64 slots (8 bytes) — the upper half stays stack garbage and
# bucketing differs per platform/build. Seen live: one tf slot arrived huge
# on Linux (whole session folded into a single anchor-stamped bucket).
_lib.vy_aggregate.argtypes = [
    _I32_P,
    _I32_P,
    _F64_P,
    _F64_P,
    _F64_P,
    _F64_P,
    _F64_P,
    ctypes.c_size_t,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.POINTER(_AggBucket),
    ctypes.c_size_t,
]
_lib.vy_aggregate.restype = ctypes.c_size_t
_lib.vy_mode.argtypes = [_I64_P, ctypes.c_size_t, _I64_P]
_lib.vy_mode.restype = ctypes.c_int32
_lib.vy_agg_anchor_seconds.argtypes = [ctypes.c_char_p, ctypes.c_int64]
_lib.vy_agg_anchor_seconds.restype = ctypes.c_int64
_lib.vy_agg_bucket_start.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_size_t,
]
_lib.vy_agg_bucket_start.restype = ctypes.c_int32
_lib.vy_agg_closed_count.argtypes = [ctypes.c_int64, ctypes.c_int32]
_lib.vy_agg_closed_count.restype = ctypes.c_int64
_lib.vy_agg_fold_tick.argtypes = [
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int32,
    ctypes.c_double,
    _F64_P,
    ctypes.c_size_t,
]
_lib.vy_agg_fold_tick.restype = ctypes.c_int32


def aggregate(
    days: Sequence[int],
    secs: Sequence[int],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    timeframe_seconds: int,
    session_start: int,
) -> list[tuple[int, int, float, float, float, float, float]]:
    """Group ascending base rows into `(day, index, o, h, l, c, v)` buckets."""
    n = len(days)
    if n == 0:
        return []
    days_v, _k1 = _i32_view(days)
    secs_v, _k2 = _i32_view(secs)
    opens_v, _k3 = _f64_view(opens)
    highs_v, _k4 = _f64_view(highs)
    lows_v, _k5 = _f64_view(lows)
    closes_v, _k6 = _f64_view(closes)
    volumes_v, _k7 = _f64_view(volumes)
    out = (_AggBucket * n)()
    count = int(
        _lib.vy_aggregate(
            days_v,
            secs_v,
            opens_v,
            highs_v,
            lows_v,
            closes_v,
            volumes_v,
            n,
            int(timeframe_seconds),
            int(session_start),
            out,
            n,
        )
    )
    if count > n:
        raise RuntimeError(f"native aggregation overflow: {count} buckets from {n} rows")
    # Buckets come back in one C-speed unpack (no per-field Python loop).
    if ctypes.sizeof(_AggBucket) != _BUCKET_SIZE:
        raise RuntimeError("native AggBucket layout drift")
    return list(struct.iter_unpack(_BUCKET_FORMAT, bytes(out)[: count * _BUCKET_SIZE]))


def _i64_view(values: Sequence[int]) -> tuple[ctypes.Array, array]:
    packed = array("q", values)
    return (ctypes.c_int64 * len(packed)).from_buffer(packed), packed


def mode(values: Sequence[int]) -> int | None:
    """Most-frequent value, first-seen tie-break (Counter.most_common(1))."""
    if not values:
        return None
    view, _keepalive = _i64_view(values)
    out = ctypes.c_int64()
    defined = int(_lib.vy_mode(view, len(values), ctypes.byref(out)))
    return int(out.value) if defined else None


def _read(invoke: Any) -> str:
    """Probe for the length, then fill a buffer; the kernel's two-call form."""
    needed = int(invoke(None, 0))
    if needed < 0:
        raise NativeBridgeError(f"aggregation kernel rejected the call: {needed}")
    buf = ctypes.create_string_buffer(needed + 1)
    if int(invoke(buf, needed + 1)) != needed:
        raise NativeBridgeError("aggregation kernel length drift")
    return buf.raw[:needed].decode("utf-8")


def session_anchor_seconds(anchor: str) -> int:
    """Session anchor `"HH:MM"` as seconds of day (09:15 when unreadable)."""
    payload = anchor.encode("utf-8")
    code = int(_lib.vy_agg_anchor_seconds(payload, len(payload)))
    if code < 0:
        raise NativeBridgeError("aggregation kernel could not read the anchor")
    return code


def bucket_start(stamp: str, size_s: int, anchor_s: int) -> str:
    """Bucket start a `"YYYY-MM-DD HH:MM:SS"` quote stamp falls into."""
    payload = stamp.encode("utf-8")
    return _read(
        lambda b, c: _lib.vy_agg_bucket_start(
            payload, len(payload), int(size_s), int(anchor_s), b, c
        )
    )


def closed_count(fresh_rows: int, newest_is_forming: bool) -> int:
    """Rows already closed in a fresh ascending batch; the forming one waits."""
    return int(_lib.vy_agg_closed_count(int(fresh_rows), 1 if newest_is_forming else 0))


# One bucket: open, high, low, close, volume.
Bucket = tuple[float, float, float, float, float]

_FOLD_SLOTS = ctypes.c_double * 5


def fold_tick(
    bucket: Bucket | None,
    price: float,
    dayvol: int,
    last_dayvol: int | None,
) -> Bucket:
    """Fold one quote into its bucket: the kernel decides the five OHLCV fields.

    ``bucket=None`` opens the bucket at this quote; otherwise the extremes
    absorb it, the close becomes this price, and the volume grows by the
    day-volume delta.
    """
    held = bucket if bucket is not None else (0.0, 0.0, 0.0, 0.0, 0.0)
    out = _FOLD_SLOTS()
    code = int(
        _lib.vy_agg_fold_tick(
            1 if bucket is None else 0,
            held[0],
            held[1],
            held[2],
            held[4],
            float(price),
            float(dayvol),
            1 if last_dayvol is not None else 0,
            float(last_dayvol) if last_dayvol is not None else 0.0,
            out,
            5,
        )
    )
    if code != 5:
        raise NativeBridgeError(f"native bucket fold failed: {code}")
    return (out[0], out[1], out[2], out[3], out[4])


__all__ = [
    "Bucket",
    "aggregate",
    "bucket_start",
    "closed_count",
    "fold_tick",
    "mode",
    "session_anchor_seconds",
]
