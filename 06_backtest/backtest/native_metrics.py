"""Rust-backed backtest numeric kernels (constitution §1: Backtesting /
Numerical calculations / Performance-critical).

The math lives in Rust (`rust/vayren-core`, `metrics` and `backtest_engine`
modules); this module marshals plain float sequences across the boundary and
maps results back to domain conventions (`None` for undefined). No independent
Python math or aggregation here.

Marshalling is zero-copy: values move through `array.array` (C-speed pack)
into ctypes views, so the FFI boundary never dominates the kernel.
"""

from __future__ import annotations

import ctypes
import math
import struct
from array import array
from collections.abc import Sequence
from dataclasses import dataclass

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

if not hasattr(_lib, "vy_bt_report"):
    raise NativeBridgeError(
        "native library has no backtest report kernel (vy_bt_report). "
        "Rebuild: `python scripts/build_rust.py`"
    )


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


class _ReportOut(ctypes.Structure):
    """Mirror of `backtest_engine::ReportOut` — thirteen doubles, one int64, two ints."""

    _fields_ = [
        ("values", ctypes.c_double * 13),
        ("total_trades", ctypes.c_int64),
        ("defined", ctypes.c_int32),
        ("pad", ctypes.c_int32),
    ]


assert ctypes.sizeof(_ReportOut) == 120, (
    f"ReportOut ABI drift: ctypes sees {ctypes.sizeof(_ReportOut)} bytes, "
    "rust/vayren-core/src/backtest_engine.rs declares 120"
)

_NET_PROFIT, _NET_PROFIT_PCT, _WIN_RATE, _PROFIT_FACTOR = 0, 1, 2, 3
_MAX_DD_PCT, _MAX_DD_ABS, _AVG_TRADE, _EXPECTANCY = 4, 5, 6, 7
_SHARPE, _GROSS_PROFIT, _GROSS_LOSS = 8, 9, 10
_STARTING_CAPITAL, _ENDING_CAPITAL = 11, 12

_BIT_WIN_RATE = 1 << 0
_BIT_PROFIT_FACTOR = 1 << 1
_BIT_AVG_TRADE = 1 << 2
_BIT_EXPECTANCY = 1 << 3
_BIT_SHARPE = 1 << 4

_lib.vy_bt_report.argtypes = [
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_double,
    ctypes.c_void_p,
]
_lib.vy_bt_report.restype = ctypes.c_int32


@dataclass(frozen=True)
class NativeReport:
    """The kernel's metric aggregate; `None` fields were flagged undefined."""

    net_profit: float
    net_profit_pct: float
    total_trades: int
    win_rate: float | None
    profit_factor: float | None
    max_drawdown_pct: float
    max_drawdown_abs: float
    avg_trade: float | None
    expectancy: float | None
    sharpe_ratio: float | None
    gross_profit: float
    gross_loss: float
    starting_capital: float
    ending_capital: float


def report(
    pnls: Sequence[float],
    bars_held: Sequence[float],
    equities: Sequence[float],
    initial_capital: float,
) -> NativeReport:
    """Aggregate the displayed metrics; the curve's last point is the ending equity."""
    if len(bars_held) != len(pnls):
        raise ValueError("pnls and bars_held must share a length")
    pnl_view, _keep_pnls = _f64_view(pnls)
    bars_view, _keep_bars = _f64_view(bars_held)
    equity_view, _keep_equities = _f64_view(equities)
    out = _ReportOut()
    code = int(
        _lib.vy_bt_report(
            pnl_view,
            len(pnls),
            bars_view,
            len(bars_held),
            equity_view,
            len(equities),
            float(initial_capital),
            ctypes.byref(out),
        )
    )
    if code != 1:
        raise NativeBridgeError(f"native report kernel rejected the call: {code}")
    defined = int(out.defined)

    def optional(slot: int, bit: int) -> float | None:
        return float(out.values[slot]) if defined & bit else None

    return NativeReport(
        net_profit=float(out.values[_NET_PROFIT]),
        net_profit_pct=float(out.values[_NET_PROFIT_PCT]),
        total_trades=int(out.total_trades),
        win_rate=optional(_WIN_RATE, _BIT_WIN_RATE),
        profit_factor=optional(_PROFIT_FACTOR, _BIT_PROFIT_FACTOR),
        max_drawdown_pct=float(out.values[_MAX_DD_PCT]),
        max_drawdown_abs=float(out.values[_MAX_DD_ABS]),
        avg_trade=optional(_AVG_TRADE, _BIT_AVG_TRADE),
        expectancy=optional(_EXPECTANCY, _BIT_EXPECTANCY),
        sharpe_ratio=optional(_SHARPE, _BIT_SHARPE),
        gross_profit=float(out.values[_GROSS_PROFIT]),
        gross_loss=float(out.values[_GROSS_LOSS]),
        starting_capital=float(out.values[_STARTING_CAPITAL]),
        ending_capital=float(out.values[_ENDING_CAPITAL]),
    )


__all__ = ["NativeReport", "max_drawdown", "equity_curve_points", "sharpe", "report"]
