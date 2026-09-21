"""Rust-backed candle metric bridge (AI_ENTRY.md §1: Market/Data).

The intraday-change rule lives in Rust (``rust/vayren-core``, ``market``
module); ``Bar::return_pct`` and the ``vy_bar_return_pct`` export share that
one function. This module holds no arithmetic of its own — it crosses the
boundary with plain doubles and returns the kernel's answer.
"""

from __future__ import annotations

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_REQUIRED_EXPORTS = ("vy_bar_return_pct",)

for _name in _REQUIRED_EXPORTS:
    if not hasattr(_lib, _name):
        raise NativeBridgeError(
            f"native library has no market bar kernel ({_name} missing). "
            "Rebuild: `python scripts/build_rust.py`"
        )


def return_pct(open: float, close: float) -> float:
    """Intraday change of one candle, in percent; a zero open answers 0.0."""
    return float(_lib.vy_bar_return_pct(float(open), float(close)))


__all__ = ["return_pct"]
