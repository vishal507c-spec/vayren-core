"""Rust-backed timeframe aggregation kernel (AI_ENTRY.md §1: Market/Data
processing / Numerical calculations / Performance-critical).

Bucket grouping and single-pass OHLCV accumulation live in Rust
(`rust/vayren-core`, `aggregate` module). Python keeps timestamp parsing
and bar formatting (domain/IO); this module marshals plain integer/float
arrays across the boundary and maps buckets back.
"""

from __future__ import annotations

import ctypes
import math
import operator
import struct
from array import array
from collections.abc import Sequence
from typing import Any

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_REQUIRED_EXPORTS = (
    "vy_aggregate",
    "vy_mode",
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
# The struct format is pinned little-endian ("<") to match the kernel's
# #[repr(C)] layout on LE hosts; the array typecodes below ("i"/"q") only back
# short-lived ctypes buffer views (c_int32/c_int64), never the wire layout.
_BUCKET_FORMAT = "<ii5d"
_BUCKET_SIZE = struct.calcsize(_BUCKET_FORMAT)


def _i32_view(values: Sequence[int]) -> tuple[ctypes.Array, array]:
    # array("i") backs the ctypes c_int32 view; the layout check in
    # aggregate() pins the struct size, so an exotic LP64 "i" cannot slip
    # through silently.
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
    lens = {len(days), len(secs), len(opens), len(highs), len(lows), len(closes), len(volumes)}
    if len(lens) != 1:
        raise NativeBridgeError(f"aggregation inputs have mismatched lengths: {sorted(lens)}")
    if int(timeframe_seconds) <= 0:
        raise NativeBridgeError(f"invalid timeframe_seconds: {timeframe_seconds}")
    if ctypes.sizeof(_AggBucket) != _BUCKET_SIZE:
        raise RuntimeError("native AggBucket layout drift")
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
    return list(struct.iter_unpack(_BUCKET_FORMAT, bytes(out)[: count * _BUCKET_SIZE]))


def _i64_view(values: Sequence[int]) -> tuple[ctypes.Array, array]:
    # array("q") backs the ctypes c_int64 view (see the _i32_view note).
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
    try:
        return buf.raw[:needed].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NativeBridgeError(f"aggregation kernel returned non-UTF-8 text: {exc}") from exc


def session_anchor_seconds(anchor: str) -> int:
    """Session anchor `"HH:MM"` as seconds of day (09:15 when unreadable).

    Unreadable text falls back to the NSE open (09:15) inside the kernel — a
    mis-set anchor buckets from the session start rather than midnight. A
    non-string anchor is caller misuse and raises `TypeError`.
    """
    if not isinstance(anchor, str):
        raise TypeError(f"anchor must be str, not {type(anchor).__name__}")
    payload = anchor.encode("utf-8")
    code = int(_lib.vy_agg_anchor_seconds(payload, len(payload)))
    if code < 0:
        raise NativeBridgeError("aggregation kernel could not read the anchor")
    return code


def bucket_start(stamp: str, size_s: int, anchor_s: int) -> str:
    """Bucket start a `"YYYY-MM-DD HH:MM:SS"` quote stamp falls into."""
    if not isinstance(stamp, str):
        raise NativeBridgeError(f"invalid stamp type: {type(stamp).__name__}")
    if int(size_s) <= 0:
        raise NativeBridgeError(f"invalid size_s: {size_s}")
    payload = stamp.encode("utf-8")
    return _read(
        lambda b, c: _lib.vy_agg_bucket_start(
            payload, len(payload), int(size_s), int(anchor_s), b, c
        )
    )


def closed_count(fresh_rows: int, newest_is_forming: bool) -> int:
    """Rows already closed in a fresh ascending batch; the forming one waits.

    Negative ``fresh_rows`` clamps to 0 (the kernel answers 0 for any
    ``fresh <= 0``; the clamp here keeps the contract explicit on the Python
    side too). Non-integer row counts are caller misuse (`TypeError`).
    """
    try:
        fresh = operator.index(fresh_rows)
    except TypeError as exc:
        raise TypeError(f"fresh_rows must be an int, not {type(fresh_rows).__name__}") from exc
    if fresh < 0:
        fresh = 0
    return int(_lib.vy_agg_closed_count(fresh, 1 if newest_is_forming else 0))


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

    Kernel arg-order contract (``vy_agg_fold_tick``): ``(fresh, open, high,
    low, volume, price, dayvol, has_last_dayvol, last_dayvol, out, out_cap)``.
    Note the kernel takes the open bucket's ``volume`` — not its ``close`` —
    so this bridge passes ``held[4]`` in the volume slot and never ``held[3]``;
    keep this mapping exact when touching the call below.

    A NaN ``price`` would silently poison the bucket, so it fails closed with
    `NativeBridgeError`; inputs ``float()`` rejects are caller misuse
    (`TypeError`), while numeric strings/ints coerce like the kernel's f64.
    """
    if bucket is not None:
        if len(bucket) != 5:
            raise NativeBridgeError(f"bucket must hold 5 OHLCV fields, got {len(bucket)}")
        for field in bucket:
            try:
                value = float(field)
            except (TypeError, ValueError) as exc:
                raise TypeError(f"bucket fields must be numeric: {exc}") from exc
            if math.isnan(value):
                raise NativeBridgeError("bucket fields must not be NaN")
    try:
        quoted = float(price)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"price must be numeric, not {type(price).__name__}") from exc
    if math.isnan(quoted):
        raise NativeBridgeError(f"cannot fold a NaN price into the bucket: {price!r}")
    try:
        volume_now = float(dayvol)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"dayvol must be numeric, not {type(dayvol).__name__}") from exc
    if last_dayvol is not None:
        try:
            volume_last: float | None = float(last_dayvol)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"last_dayvol must be numeric or None, not {type(last_dayvol).__name__}"
            ) from exc
    else:
        volume_last = None
    held = bucket if bucket is not None else (0.0, 0.0, 0.0, 0.0, 0.0)
    out = _FOLD_SLOTS()
    code = int(
        _lib.vy_agg_fold_tick(
            1 if bucket is None else 0,
            held[0],
            held[1],
            held[2],
            held[4],
            quoted,
            volume_now,
            1 if volume_last is not None else 0,
            volume_last if volume_last is not None else 0.0,
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
