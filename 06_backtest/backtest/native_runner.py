"""Rust-backed backtest runner kernels bridge (constitution §1: Backtesting).

The aggregation-window margin rule and the bounded worker-count rule live in
``rust/vayren-core/src/backtest_runner.rs`` and reach Python only through
these ``vy_bt_*`` exports. This module marshals; it holds no date arithmetic
and no clamp of its own. The machine probe (`os.cpu_count()`) and the process
pool stay in `runner.py` — they are host IO/process control, not policy.
"""

from __future__ import annotations

import ctypes

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_REQUIRED_EXPORTS = ("vy_bt_window_bounds", "vy_bt_default_workers")

for _name in _REQUIRED_EXPORTS:
    if not hasattr(_lib, _name):
        raise NativeBridgeError(
            f"native library has no backtest runner kernel ({_name} missing). "
            "Rebuild: `python scripts/build_rust.py`"
        )

_char_out = ctypes.POINTER(ctypes.c_char)

_lib.vy_bt_window_bounds.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_bt_window_bounds.restype = ctypes.c_int32
_lib.vy_bt_default_workers.argtypes = [ctypes.c_int64]
_lib.vy_bt_default_workers.restype = ctypes.c_int64


def _text(value: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError(f"date must be str, not {type(value).__name__}")
    return value.encode("utf-8")


def window_bounds(start_date: str, end_date: str) -> tuple[str, str]:
    """Aggregation-window `(lower, upper)` stamps for a date range.

    The kernel rejects a date that is not a real `YYYY-MM-DD` day; that
    surfaces here as ``ValueError``, exactly like the standard-library
    ISO parser did.
    """
    start = _text(start_date)
    end = _text(end_date)
    needed = int(_lib.vy_bt_window_bounds(start, len(start), end, len(end), None, 0))
    if needed < 0:
        raise ValueError(f"invalid backtest date range: {start_date!r} .. {end_date!r}")
    buf = ctypes.create_string_buffer(needed + 1)
    wrote = int(_lib.vy_bt_window_bounds(start, len(start), end, len(end), buf, needed + 1))
    if wrote != needed:
        raise NativeBridgeError("backtest runner kernel length drift")
    lower, _, upper = buf.raw[:needed].decode("utf-8").partition("\n")
    return lower, upper


def default_workers(cpu_count: int) -> int:
    """Bounded batch worker count for a machine with ``cpu_count`` cpus.

    ``0`` means "unknown" and is resolved by the kernel, not here.
    """
    return int(_lib.vy_bt_default_workers(int(cpu_count)))


__all__ = ["default_workers", "window_bounds"]
