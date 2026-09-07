"""Rust-backed backtest numeric kernels (constitution §1: Backtesting /
Numerical calculations / Performance-critical).

The math lives in Rust (`rust/vayren-core`, `metrics` module); this module
marshals plain float sequences across the boundary and maps results back to
domain conventions (`None` for undefined). No independent Python math here.

Marshalling is zero-copy: values move through `array.array` (C-speed pack)
into ctypes views, so the FFI boundary never dominates the kernel.
"""

from __future__ import annotations

import ctypes
import math
import struct
from array import array
from collections.abc import Sequence

from core.native.loader import load_vayren_core

_lib = load_vayren_core()


def _f64_view(values: Sequence[float]) -> tuple[ctypes.Array, array]:
    """Pack floats in C and return a zero-copy ctypes view (buffer kept alive)."""
    packed = array("d", values)
    return (ctypes.c_double * len(packed)).from_buffer(packed), packed


def max_drawdown(equities: Sequence[float]) -> tuple[float, float]:
    """Peak-to-trough drawdown of an equity curve (pct, abs)."""
    if not equities:
        return 0.0, 0.0
    view, _keepalive = _f64_view(equities)
    out_pct, out_abs = ctypes.c_double(), ctypes.c_double()
    _lib.vy_max_drawdown(view, len(equities), ctypes.byref(out_pct), ctypes.byref(out_abs))
    return float(out_pct.value), float(out_abs.value)


def equity_curve_points(initial: float, pnls: Sequence[float]) -> list[tuple[float, float]]:
    """Running `(equity, drawdown_pct)` pairs after each trade PnL, in order."""
    n = len(pnls)
    if n == 0:
        return []
    pnl_view, _keepalive = _f64_view(pnls)
    # Interleaved (equity, dd) pairs read back in one C-speed pass.
    raw = (ctypes.c_double * (2 * n))()
    wrote = int(_lib.vy_equity_curve(float(initial), pnl_view, n, raw))
    assert wrote == n, f"native equity curve short write: {wrote}/{n}"
    return list(struct.iter_unpack("dd", bytes(raw)))


def sharpe(pnls: Sequence[float], bars_held: Sequence[float], initial: float) -> float | None:
    """Annualized Sharpe of per-trade returns; None when undefined."""
    if len(pnls) < 2:
        return None
    if len(bars_held) != len(pnls):
        raise ValueError("pnls and bars_held must share a length")
    pnl_view, _keep_pnls = _f64_view(pnls)
    bars_view, _keep_bars = _f64_view(bars_held)
    out = ctypes.c_double()
    defined = int(
        _lib.vy_sharpe(
            pnl_view,
            bars_view,
            len(pnls),
            float(initial),
            ctypes.byref(out),
        )
    )
    if not defined or math.isnan(out.value):
        return None
    return float(out.value)


__all__ = ["max_drawdown", "equity_curve_points", "sharpe"]
