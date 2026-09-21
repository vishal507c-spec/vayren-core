"""Rust-backed backtest replay window (constitution §1: Backtesting).

Which bars a `[start, end]` date range keeps is decided by
`rust/vayren-core/src/backtest_engine.rs::slice_indices`. This module only
marshals timestamps across and returns the surviving indices — the bars
themselves never cross, and no window policy lives here.
"""

from __future__ import annotations

import ctypes
from collections.abc import Sequence

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

if not hasattr(_lib, "vy_bt_slice"):
    raise NativeBridgeError(
        "native library has no backtest replay window (vy_bt_slice). "
        "Rebuild: `python scripts/build_rust.py`"
    )

_lib.vy_bt_slice.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_size_t,
]
_lib.vy_bt_slice.restype = ctypes.c_int32


def _bound(value: str | None) -> tuple[bytes | None, int]:
    """`(payload, length)` for one bound; length `-1` is Python's `None`."""
    if value is None:
        return None, -1
    payload = value.encode("utf-8")
    return payload, len(payload)


def window(
    timestamps: Sequence[str],
    start_date: str | None,
    end_date: str | None,
) -> tuple[int, ...]:
    """Indices of the bars whose ISO date falls inside `[start_date, end_date]`."""
    blob = "\n".join(timestamps).encode("utf-8")
    count = len(timestamps)
    slots = (ctypes.c_uint32 * count)()
    start_payload, start_length = _bound(start_date)
    end_payload, end_length = _bound(end_date)
    wrote = int(
        _lib.vy_bt_slice(
            blob,
            len(blob),
            start_payload,
            start_length,
            end_payload,
            end_length,
            slots,
            count,
        )
    )
    if wrote < 0:
        raise NativeBridgeError(f"native replay window rejected the request (code {wrote})")
    return tuple(int(index) for index in slots[:wrote])


__all__ = ["window"]
