"""ctypes wrapper for the Rust kill-switch kernel (handle-based bridge).

The latch table, level vocabulary, timestamps, the reload rule and the
serialized document shape live in `rust/vayren-core/src/kill_switch.rs`.
This module only marshals UTF-8 strings/buffers across the boundary and maps
kernel rejections onto the exception types Python callers already expect —
no kill-switch policy lives here (AI_ENTRY.md §1).
"""

from __future__ import annotations

import contextlib
import ctypes
from collections.abc import Callable

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

# `vy_ks_load` outcome codes.
_APPLIED = 0
_GARBAGE = 1

_REQUIRED_EXPORTS = (
    "vy_ks_new",
    "vy_ks_free",
    "vy_ks_level_count",
    "vy_ks_level_name",
    "vy_ks_engage",
    "vy_ks_disengage",
    "vy_ks_is_halted",
    "vy_ks_state_engaged",
    "vy_ks_state_reason",
    "vy_ks_state_engaged_at",
    "vy_ks_serialize",
    "vy_ks_load",
    "vy_ks_last_message",
)

_missing_exports = [name for name in _REQUIRED_EXPORTS if not hasattr(_lib, name)]
if _missing_exports:
    raise NativeBridgeError(
        f"native library has no kill-switch kernel ({', '.join(_missing_exports)}). "
        "Rebuild: `python tools/build_rust.py`"
    )

# Handle-based kill-switch ABI (loader `_configure_kill_switch` pattern):
# strings cross as UTF-8 buffer pairs; reads use the two-call probe protocol.
_char_out = ctypes.POINTER(ctypes.c_char)
_lib.vy_ks_new.argtypes = []
_lib.vy_ks_new.restype = ctypes.c_int64
_lib.vy_ks_free.argtypes = [ctypes.c_int64]
_lib.vy_ks_free.restype = ctypes.c_int32
_lib.vy_ks_level_count.argtypes = []
_lib.vy_ks_level_count.restype = ctypes.c_int32
_lib.vy_ks_level_name.argtypes = [ctypes.c_int32, _char_out, ctypes.c_size_t]
_lib.vy_ks_level_name.restype = ctypes.c_int32
_lib.vy_ks_engage.argtypes = [
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.c_char_p,
    ctypes.c_size_t,
]
_lib.vy_ks_engage.restype = ctypes.c_int32
_lib.vy_ks_disengage.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_ks_disengage.restype = ctypes.c_int32
_lib.vy_ks_is_halted.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_ks_is_halted.restype = ctypes.c_int32
_lib.vy_ks_state_engaged.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_ks_state_engaged.restype = ctypes.c_int32
_lib.vy_ks_state_reason.argtypes = [
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_size_t,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_ks_state_reason.restype = ctypes.c_int32
_lib.vy_ks_state_engaged_at.argtypes = _lib.vy_ks_state_reason.argtypes
_lib.vy_ks_state_engaged_at.restype = ctypes.c_int32
_lib.vy_ks_serialize.argtypes = [ctypes.c_int64, _char_out, ctypes.c_size_t]
_lib.vy_ks_serialize.restype = ctypes.c_int32
_lib.vy_ks_load.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
_lib.vy_ks_load.restype = ctypes.c_int32
_lib.vy_ks_last_message.argtypes = [ctypes.c_int64, _char_out, ctypes.c_size_t]
_lib.vy_ks_last_message.restype = ctypes.c_int32


def _encode(value: str, what: str) -> tuple[bytes, int]:
    """UTF-8 payload + length for an inbound string argument."""
    if not isinstance(value, str):
        raise TypeError(f"kill-switch {what} must be str, not {type(value).__name__}")
    payload = value.encode("utf-8")
    return payload, len(payload)


def _read_string(call: Callable[[ctypes.Array[ctypes.c_char] | None, int], int]) -> str:
    """Drain a length-returning buffer call: probe size, then fill exactly."""
    needed = call(None, 0)
    if needed < 0:
        raise NativeBridgeError(f"native kill-switch read rejected: {needed}")
    buf = ctypes.create_string_buffer(needed + 1)
    if call(buf, needed + 1) != needed:
        raise NativeBridgeError("native kill-switch string length drift")
    # `buf.value` would stop at an embedded NUL; take the exact payload span.
    return buf.raw[:needed].decode("utf-8")


def levels() -> tuple[str, ...]:
    """The kernel's level vocabulary — the one authority on level names."""
    names: list[str] = []
    for index in range(int(_lib.vy_ks_level_count())):
        names.append(
            _read_string(lambda b, c, i=index: int(_lib.vy_ks_level_name(i, b, c))),
        )
    return tuple(names)


class NativeKillSwitch:
    """One Rust-side latch table, addressed by an opaque handle."""

    def __init__(self) -> None:
        handle = int(_lib.vy_ks_new())
        if handle <= 0:
            raise NativeBridgeError(f"native kill-switch handle allocation failed: {handle}")
        self._handle = handle

    def __del__(self) -> None:
        with contextlib.suppress(Exception):  # interpreter teardown: nothing to report
            self.close()

    def close(self) -> None:
        """Release the Rust slot. Idempotent."""
        handle, self._handle = self._handle, 0
        if handle and int(_lib.vy_ks_free(handle)) != 0:
            raise NativeBridgeError(f"native kill-switch handle {handle} already released")

    def last_message(self) -> str:
        """Rejection text recorded by the most recent call on this handle."""
        return _read_string(
            lambda b, c: int(_lib.vy_ks_last_message(self._handle, b, c)),
        )

    def engage(self, reason: str, level: str) -> None:
        payload, length = _encode(reason, "reason")
        code = int(_lib.vy_ks_engage(self._handle, payload, length, *self._level(level)))
        self._require_ok(code)

    def disengage(self, level: str) -> None:
        self._require_ok(int(_lib.vy_ks_disengage(self._handle, *self._level(level))))

    def is_halted(self, level: str) -> bool:
        return self._require_bool(int(_lib.vy_ks_is_halted(self._handle, *self._level(level))))

    def state_engaged(self, level: str) -> bool:
        return self._require_bool(
            int(_lib.vy_ks_state_engaged(self._handle, *self._level(level))),
        )

    def reason(self, level: str) -> str:
        return self._field(_lib.vy_ks_state_reason, level)

    def engaged_at(self, level: str) -> str:
        return self._field(_lib.vy_ks_state_engaged_at, level)

    def serialize(self) -> str:
        return _read_string(lambda b, c: int(_lib.vy_ks_serialize(self._handle, b, c)))

    def load(self, text: str) -> None:
        """Restore latches from persisted text.

        Corrupt text (`_GARBAGE`) raises `NativeBridgeError` — the persisted
        state is untrusted input, so a silent return would leave the caller
        believing a halt was restored when nothing applied. A document that
        parses but cannot be walked raises `AttributeError` after the levels
        it reached applied.
        """
        payload, length = _encode(text, "saved state")
        code = int(_lib.vy_ks_load(self._handle, payload, length))
        if code == _APPLIED:
            return
        if code == _GARBAGE:
            raise NativeBridgeError(
                "native kill-switch state is corrupt: "
                f"{self.last_message() or 'unparseable document'}"
            )
        if code == -1:
            raise AttributeError(self.last_message())
        raise NativeBridgeError(f"native kill-switch handle error: {code}")

    def _field(self, read: Callable[..., int], level: str) -> str:
        payload, length = self._level(level)
        return _read_string(
            lambda b, c: int(read(self._handle, payload, length, b, c)),
        )

    @staticmethod
    def _level(level: str) -> tuple[bytes, int]:
        return _encode(level, "level")

    def _require_ok(self, code: int) -> None:
        if code == 0:
            return
        if code == -1:
            raise ValueError(self.last_message())
        raise NativeBridgeError(f"native kill-switch handle error: {code}")

    def _require_bool(self, code: int) -> bool:
        if code in (0, 1):
            return bool(code)
        if code == -1:
            raise ValueError(self.last_message())
        raise NativeBridgeError(f"native kill-switch handle error: {code}")


__all__ = ["NativeBridgeError", "NativeKillSwitch", "levels"]
