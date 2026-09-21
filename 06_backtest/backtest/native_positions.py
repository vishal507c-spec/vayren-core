"""Rust-backed backtest position kernel (constitution §1: Backtesting /
Performance-critical).

The entry fill (slippage direction, affordability rejection, fractional
quantity, commission), the exit rule (signal slippage, SL/TP bar crossing,
long/short mirroring) and the trade maths (commission, gross, pnl, pnl%, risk,
R multiple) live in `rust/vayren-core/src/backtest.rs`. This module only
marshals plain floats across the boundary and maps the packed results back onto
domain conventions (`None` for an absent exit and for an undefined R multiple) —
no position policy lives here.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_SIDES = {"LONG": 0, "SHORT": 1}
_REASONS = {"SIGNAL": 1, "SL": 2, "TP": 3, "END": 4}
_REASON_NAMES = {code: name for name, code in _REASONS.items()}

_CLOSE_RECORDED = 1
_NO_EXIT = 0

_R_MULTIPLE_NONE = 1

_REQUIRED_EXPORTS = (
    "vy_bt_try_close",
    "vy_bt_close_trade",
    "vy_bt_fill",
    "vy_bt_signal_exit",
    "vy_bt_exit_price",
)

_missing_exports = [name for name in _REQUIRED_EXPORTS if not hasattr(_lib, name)]
if _missing_exports:
    raise NativeBridgeError(
        f"native library has no backtest position kernel ({', '.join(_missing_exports)}). "
        "Rebuild: `python scripts/build_rust.py`"
    )


class _FillOut(ctypes.Structure):
    """Mirror of `backtest::FillOut` — three doubles then two ints."""

    _fields_ = [
        ("fill_price", ctypes.c_double),
        ("quantity", ctypes.c_double),
        ("commission", ctypes.c_double),
        ("side", ctypes.c_int32),
        ("pad", ctypes.c_int32),
    ]


assert ctypes.sizeof(_FillOut) == 32, (
    f"FillOut ABI drift: ctypes sees {ctypes.sizeof(_FillOut)} bytes, "
    "rust/vayren-core/src/backtest.rs declares 32"
)

_FILL_SIDES = {1: "LONG", 2: "SHORT"}

_lib.vy_bt_fill.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_void_p,
]
_lib.vy_bt_fill.restype = ctypes.c_int32
_lib.vy_bt_signal_exit.argtypes = [
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_void_p,
]
_lib.vy_bt_signal_exit.restype = ctypes.c_int32
_lib.vy_bt_exit_price.argtypes = [ctypes.c_int32, ctypes.c_double, ctypes.c_double, ctypes.c_void_p]
_lib.vy_bt_exit_price.restype = ctypes.c_int32


@dataclass(frozen=True)
class NativeFill:
    """One entry the kernel priced: direction, price, quantity, fee."""

    side: str
    fill_price: float
    quantity: float
    commission: float


class _TradeOut(ctypes.Structure):
    """Mirror of `backtest::TradeOut` — five doubles then two ints."""

    _fields_ = [
        ("exit_price", ctypes.c_double),
        ("commission", ctypes.c_double),
        ("pnl", ctypes.c_double),
        ("pnl_pct", ctypes.c_double),
        ("r_multiple", ctypes.c_double),
        ("reason", ctypes.c_int32),
        ("flags", ctypes.c_int32),
    ]


assert ctypes.sizeof(_TradeOut) == 48, (
    f"TradeOut ABI drift: ctypes sees {ctypes.sizeof(_TradeOut)} bytes, "
    "rust/vayren-core/src/backtest.rs declares 48"
)


@dataclass(frozen=True)
class ClosedTrade:
    """Economics of a close the kernel recorded (identity stays in Python)."""

    exit_price: float
    commission: float
    pnl: float
    pnl_pct: float
    r_multiple: float | None
    exit_reason: str


def _side_code(side: str) -> int:
    try:
        return _SIDES[side]
    except KeyError:
        raise ValueError(f"side must be LONG or SHORT, not {side!r}") from None


def _level(value: float | None) -> tuple[int, float]:
    """Presence flag + payload; `0.0` is a real level the kernel must honour."""
    if value is None:
        return 0, 0.0
    return 1, float(value)


def _check(code: int) -> None:
    if code == -2:
        raise NativeBridgeError("native position kernel rejected a null result slot")
    if code == -1:
        raise ValueError("native position kernel rejected the trade vocabulary")


def _translate(out: _TradeOut) -> ClosedTrade:
    reason = _REASON_NAMES.get(out.reason)
    if reason is None:
        raise NativeBridgeError(f"native position kernel returned reason code {out.reason}")
    r_multiple = None if out.flags & _R_MULTIPLE_NONE else float(out.r_multiple)
    return ClosedTrade(
        exit_price=float(out.exit_price),
        commission=float(out.commission),
        pnl=float(out.pnl),
        pnl_pct=float(out.pnl_pct),
        r_multiple=r_multiple,
        exit_reason=reason,
    )


def fill(
    signal_side: str,
    bar_close: float,
    available_equity: float,
    slippage_pct: float,
    commission_pct: float,
) -> NativeFill | None:
    """Price one entry, or None when the kernel finds it unaffordable.

    Percentages cross raw: clamping them is `backtest::ExecutionSimulator`'s
    rule, not this boundary's.
    """
    payload = signal_side.encode("utf-8")
    out = _FillOut()
    code = int(
        _lib.vy_bt_fill(
            payload,
            len(payload),
            float(bar_close),
            float(available_equity),
            float(slippage_pct),
            float(commission_pct),
            ctypes.byref(out),
        )
    )
    if code == -2:
        raise NativeBridgeError("native fill kernel rejected a null result slot")
    if code == -1:
        raise NativeBridgeError("native fill kernel rejected an undecodable side")
    if code == 0:
        return None
    side = _FILL_SIDES.get(out.side)
    if side is None:
        raise NativeBridgeError(f"native fill kernel returned side code {out.side}")
    return NativeFill(
        side=side,
        fill_price=float(out.fill_price),
        quantity=float(out.quantity),
        commission=float(out.commission),
    )


def signal_exit(
    side: str,
    is_buy: bool,
    bar_close: float,
    slippage_pct: float,
) -> float | None:
    """Does this signal close the open leg? None means "the leg holds"."""
    out = ctypes.c_double()
    code = int(
        _lib.vy_bt_signal_exit(
            _side_code(side),
            1 if is_buy else 0,
            float(bar_close),
            float(slippage_pct),
            ctypes.byref(out),
        )
    )
    if code == _NO_EXIT:
        return None
    _check(code)
    return float(out.value)


def exit_price(side: str, bar_close: float, slippage_pct: float) -> float:
    """Price the close the caller already decided on (end of the window)."""
    out = ctypes.c_double()
    code = int(
        _lib.vy_bt_exit_price(
            _side_code(side),
            float(bar_close),
            float(slippage_pct),
            ctypes.byref(out),
        )
    )
    _check(code)
    return float(out.value)


def try_close(
    side: str,
    sl_price: float | None,
    tp_price: float | None,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    entry_price: float,
    quantity: float,
    commission_entry: float,
    commission_pct: float,
    exit_signal: bool,
) -> ClosedTrade | None:
    """Does this bar close the open leg? None means "still holding"."""
    sl_defined, sl = _level(sl_price)
    tp_defined, tp = _level(tp_price)
    out = _TradeOut()
    code = int(
        _lib.vy_bt_try_close(
            _side_code(side),
            sl_defined,
            sl,
            tp_defined,
            tp,
            float(bar_high),
            float(bar_low),
            float(bar_close),
            float(entry_price),
            float(quantity),
            float(commission_entry),
            float(commission_pct),
            1 if exit_signal else 0,
            ctypes.byref(out),
        )
    )
    if code == _NO_EXIT:
        return None
    _check(code)
    return _translate(out)


def close_trade(
    side: str,
    exit_reason: str,
    entry_price: float,
    quantity: float,
    commission_entry: float,
    exit_price: float,
    commission_pct: float,
    sl_price: float | None,
) -> ClosedTrade:
    """Close at a price Python already decided (signal exit, end of window)."""
    sl_defined, sl = _level(sl_price)
    out = _TradeOut()
    code = int(
        _lib.vy_bt_close_trade(
            _side_code(side),
            _REASONS[exit_reason],
            float(entry_price),
            float(quantity),
            float(commission_entry),
            float(exit_price),
            float(commission_pct),
            sl_defined,
            sl,
            ctypes.byref(out),
        )
    )
    _check(code)
    return _translate(out)


__all__ = [
    "ClosedTrade",
    "NativeFill",
    "close_trade",
    "exit_price",
    "fill",
    "signal_exit",
    "try_close",
]
