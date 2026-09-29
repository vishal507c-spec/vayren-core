"""Rust-backed candle metric bridge (AI_ENTRY.md §1: Market/Data).

The intraday-change rule lives in Rust (``rust/vayren-core``, ``market``
module); ``Bar::return_pct`` and the ``vy_bar_return_pct`` export share that
one function. This module holds no arithmetic of its own — it crosses the
boundary with plain doubles and returns the kernel's answer.
"""

from __future__ import annotations

import ctypes
import math

from core.native.loader import NativeBridgeError, load_vayren_core

_REQUIRED_EXPORTS = ("vy_bar_return_pct",)

_lib: ctypes.CDLL | None = None


def _lib_handle() -> ctypes.CDLL:
    """Load the native library on first use, not at import time.

    Import-time loading turns every ``import market`` into a native-library
    probe; lazy access keeps the payload modules importable and fails closed
    with `NativeBridgeError` only when the kernel is actually needed.
    """
    global _lib
    if _lib is None:
        loaded: ctypes.CDLL = load_vayren_core()
        for _name in _REQUIRED_EXPORTS:
            if not hasattr(loaded, _name):
                raise NativeBridgeError(
                    f"native library has no market bar kernel ({_name} missing). "
                    "Rebuild: `python scripts/build_rust.py`"
                )
        _lib = loaded
    assert _lib is not None  # narrowed for the type checker; set above.
    return _lib


def return_pct(open_px: float, close: float) -> float:
    """Intraday change of one candle, in percent; a zero open answers 0.0.

    Legs coerce via ``float()`` (numeric strings and ints are accepted);
    anything ``float()`` rejects is caller misuse (`ValueError` with a
    bridge-style message, never a bare `TypeError`). NaN or infinite legs
    would poison the answer, so they raise `ValueError` too.
    """
    try:
        open_value = float(open_px)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"native bar kernel received a non-numeric open: {open_px!r}") from exc
    try:
        close_value = float(close)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"native bar kernel received a non-numeric close: {close!r}") from exc
    if not math.isfinite(open_value):
        raise ValueError(f"native bar kernel received a non-finite open: {open_px!r}")
    if not math.isfinite(close_value):
        raise ValueError(f"native bar kernel received a non-finite close: {close!r}")
    return float(_lib_handle().vy_bar_return_pct(open_value, close_value))


__all__ = ["return_pct"]
