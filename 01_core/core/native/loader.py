"""Loader for the Rust `vayren_core` cdylib (constitution §8: Rust owns the
numeric/lifecycle kernels; this module is the typed interop boundary).

Fail-closed: a missing library, ABI mismatch, or handshake failure raises
`NativeBridgeError` with an actionable message. There is deliberately NO
silent Python fallback — the Rust implementation is the single authority
for the kernels it owns (migration §12).
"""

from __future__ import annotations

import ctypes
import os
import platform
from pathlib import Path

ABI_VERSION = 1
ORDER_STATE_COUNT = 13


class NativeBridgeError(RuntimeError):
    """The Rust native library is unavailable or incompatible."""


def _candidate_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_core.dll",)
    if system == "darwin":
        return ("libvayren_core.dylib", "vayren_core.dylib")
    return ("libvayren_core.so", "vayren_core.so")


def find_library() -> Path:
    """Locate the built cdylib. Env `VAYREN_NATIVE_LIB` wins; otherwise the
    release (then debug) target dir of the repo `rust/` workspace."""
    override = os.environ.get("VAYREN_NATIVE_LIB", "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise NativeBridgeError(f"VAYREN_NATIVE_LIB points at a missing file: {override!r}")
    root = Path(__file__).resolve().parent.parent.parent.parent
    for profile in ("release", "debug"):
        for name in _candidate_names():
            candidate = root / "rust" / "target" / profile / name
            if candidate.is_file():
                return candidate
    raise NativeBridgeError(
        "Rust native library not found. Build it first: "
        "`python scripts/build_rust.py` (requires a Rust toolchain)."
    )


def _configure(lib: ctypes.CDLL) -> ctypes.CDLL:
    lib.vy_abi_version.restype = ctypes.c_uint32
    lib.vy_abi_version.argtypes = []
    lib.vy_order_state_count.restype = ctypes.c_int32
    lib.vy_order_state_count.argtypes = []
    lib.vy_order_transition_allowed.restype = ctypes.c_int32
    lib.vy_order_transition_allowed.argtypes = [ctypes.c_int32, ctypes.c_int32]
    lib.vy_order_is_terminal.restype = ctypes.c_int32
    lib.vy_order_is_terminal.argtypes = [ctypes.c_int32]
    lib.vy_order_transitions.restype = ctypes.c_uint32
    lib.vy_order_transitions.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
    lib.vy_order_terminal_states.restype = ctypes.c_uint32
    lib.vy_order_terminal_states.argtypes = [
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_uint32,
    ]
    lib.vy_max_drawdown.restype = None
    lib.vy_max_drawdown.argtypes = [
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.vy_equity_curve.restype = ctypes.c_size_t
    lib.vy_equity_curve.argtypes = [
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.vy_sharpe.restype = ctypes.c_int32
    lib.vy_sharpe.argtypes = [
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.vy_mode.restype = ctypes.c_int32
    lib.vy_mode.argtypes = [
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_int64),
    ]
    if hasattr(lib, "vy_exec_arm_transition"):
        lib.vy_exec_arm_transition.restype = ctypes.c_int32
        lib.vy_exec_arm_transition.argtypes = [ctypes.c_int32, ctypes.c_int32]
    if hasattr(lib, "vy_exec_lifecycle_transition_allowed"):
        lib.vy_exec_lifecycle_transition_allowed.restype = ctypes.c_int32
        lib.vy_exec_lifecycle_transition_allowed.argtypes = [ctypes.c_int32, ctypes.c_int32]
    if hasattr(lib, "vy_exec_planner_plan"):
        lib.vy_exec_planner_plan.restype = ctypes.c_int32
        lib.vy_exec_planner_plan.argtypes = [
            ctypes.c_double,
            ctypes.c_int32,
            ctypes.c_double,
            ctypes.c_int32,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_double),
        ]
    if hasattr(lib, "vy_exec_ledger_apply_fill"):
        lib.vy_exec_ledger_apply_fill.restype = ctypes.c_int32
        lib.vy_exec_ledger_apply_fill.argtypes = [
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int32,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
        ]
    return lib


def load_vayren_core() -> ctypes.CDLL:
    """Load the cdylib and verify the ABI handshake (version + state count)."""
    path = find_library()
    try:
        lib = _configure(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeBridgeError(f"cannot load Rust native library {path}: {exc}") from exc
    if lib.vy_abi_version() != ABI_VERSION:
        raise NativeBridgeError(
            f"native ABI mismatch at {path}: library={lib.vy_abi_version()} "
            f"expected={ABI_VERSION} (rebuild: `python scripts/build_rust.py`)"
        )
    if lib.vy_order_state_count() != ORDER_STATE_COUNT:
        raise NativeBridgeError(
            f"native order-state vocabulary drift at {path}: "
            f"library={lib.vy_order_state_count()} expected={ORDER_STATE_COUNT}"
        )
    return lib
