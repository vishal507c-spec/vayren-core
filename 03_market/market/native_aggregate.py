"""Rust-backed timeframe aggregation kernel (constitution §1: Market/Data
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

from core.native.loader import load_vayren_core

_lib = load_vayren_core()

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


__all__ = ["aggregate", "mode"]
