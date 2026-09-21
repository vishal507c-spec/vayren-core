"""ctypes wrapper for the Rust stream-normalizer kernel (handle-based bridge).

The watermark, the gap/duplicate accounting, the reorder-buffer overflow rule,
delivery order, staleness thresholds and every counter live in
``rust/vayren-core/src/normalizer.rs``. This module only marshals a symbol, a
sequence number and an opaque token across and re-reads the kernel's verdict
document; market event objects never cross the boundary and no stream policy
is evaluated here (AI_ENTRY.md §1).
"""

from __future__ import annotations

import contextlib
import ctypes
from collections.abc import Callable
from dataclasses import dataclass

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_REQUIRED_EXPORTS = (
    "vy_norm_new",
    "vy_norm_free",
    "vy_norm_observe",
    "vy_norm_last_document",
    "vy_norm_last_health",
    "vy_norm_check_health",
    "vy_norm_is_stale",
    "vy_norm_expected_seq",
    "vy_norm_stats",
    "vy_norm_stale_floor",
    "vy_norm_gap_stale",
    "vy_norm_watermark",
    "vy_norm_gap_reason",
)

_missing_exports = [name for name in _REQUIRED_EXPORTS if not hasattr(_lib, name)]
if _missing_exports:
    raise NativeBridgeError(
        f"native library has no stream normalizer kernel ({', '.join(_missing_exports)}). "
        "Rebuild: `python scripts/build_rust.py`"
    )

_CHAR_OUT = ctypes.POINTER(ctypes.c_char)


@dataclass(frozen=True)
class NativeStreamStats:
    """The kernel's counters for one gate, as reported on every verdict."""

    accepted: int
    duplicates: int
    gaps: int
    evicted: int
    heartbeats: int
    stale_flags: int


def _encode(value: str) -> tuple[bytes, int]:
    payload = value.encode("utf-8")
    return payload, len(payload)


def _read_string(call: Callable[[ctypes.Array[ctypes.c_char] | None, int], int]) -> str:
    """Drain a length-returning buffer call: probe size, then fill exactly."""
    needed = call(None, 0)
    if needed < 0:
        raise NativeBridgeError(f"native stream read rejected: {needed}")
    buf = ctypes.create_string_buffer(needed + 1)
    if call(buf, needed + 1) != needed:
        raise NativeBridgeError("native stream string length drift")
    return buf.raw[:needed].decode("utf-8")


def _stats_line(text: str) -> NativeStreamStats:
    fields = text.split(",")
    if len(fields) != 6:
        raise NativeBridgeError(f"native stream stats are malformed: {text!r}")
    return NativeStreamStats(*(int(field) for field in fields))


class NativeStreamGate:
    """One Rust-side stream gate, addressed by an opaque handle."""

    def __init__(
        self,
        max_reorder_buffer: int,
        stale_after_seconds: float,
        heartbeat_timeout_seconds: float,
    ) -> None:
        handle = int(
            _lib.vy_norm_new(
                int(max_reorder_buffer),
                float(stale_after_seconds),
                float(heartbeat_timeout_seconds),
            )
        )
        if handle < 1:
            raise NativeBridgeError(
                "native stream gate rejected the configuration: "
                f"buffer={max_reorder_buffer} stale={stale_after_seconds} "
                f"heartbeat={heartbeat_timeout_seconds}"
            )
        self._handle = handle

    def __del__(self) -> None:
        with contextlib.suppress(Exception):  # interpreter teardown: nothing to report
            self.close()

    def close(self) -> None:
        """Release the Rust slot. Idempotent."""
        handle, self._handle = self._handle, 0
        if handle and int(_lib.vy_norm_free(handle)) != 0:
            raise NativeBridgeError(f"native stream handle {handle} already released")

    def observe_event(
        self, symbol: str, seq: int, token: int, now_epoch: float
    ) -> tuple[tuple[int, ...], tuple[int, ...], NativeStreamStats]:
        """Apply one sequenced arrival; return (delivered, dropped, stats)."""
        return self._observe(symbol, seq, token, now_epoch, heartbeat=False)

    def observe_heartbeat(
        self, symbol: str, now_epoch: float
    ) -> tuple[tuple[int, ...], tuple[int, ...], NativeStreamStats]:
        """Apply one heartbeat; nothing is ever delivered by it."""
        return self._observe(symbol, 0, 0, now_epoch, heartbeat=True)

    def check_health(self, symbol: str, now_epoch: float) -> tuple[bool, str, NativeStreamStats]:
        """Kernel verdict: healthy flag plus the kernel's own reason wording."""
        payload, length = _encode(symbol)
        status = int(_lib.vy_norm_check_health(self._handle, payload, length, float(now_epoch)))
        if status != 0:
            raise NativeBridgeError(f"native stream gate rejected the health check: {status}")
        document = _read_string(
            lambda b, c: int(_lib.vy_norm_last_health(self._handle, b, c)),
        )
        flag, _, reason = document.partition("|")
        if flag not in ("0", "1"):
            raise NativeBridgeError(f"native stream health verdict is malformed: {document!r}")
        return flag == "1", reason, self.stats()

    def is_stale(self, symbol: str) -> bool:
        payload, length = _encode(symbol)
        return self._require_bool(int(_lib.vy_norm_is_stale(self._handle, payload, length)))

    def expected_seq(self, symbol: str) -> int:
        payload, length = _encode(symbol)
        value = int(_lib.vy_norm_expected_seq(self._handle, payload, length))
        if value < 0:
            raise NativeBridgeError(f"native stream gate rejected the symbol read: {value}")
        return value

    def stats(self) -> NativeStreamStats:
        return _stats_line(
            _read_string(lambda b, c: int(_lib.vy_norm_stats(self._handle, b, c))),
        )

    def _observe(
        self,
        symbol: str,
        seq: int,
        token: int,
        now_epoch: float,
        *,
        heartbeat: bool,
    ) -> tuple[tuple[int, ...], tuple[int, ...], NativeStreamStats]:
        payload, length = _encode(symbol)
        code = int(
            _lib.vy_norm_observe(
                self._handle,
                payload,
                length,
                int(seq),
                int(token),
                float(now_epoch),
                1 if heartbeat else 0,
            )
        )
        if code != 0:
            raise NativeBridgeError(f"native stream gate rejected the arrival: {code}")
        return self._last_document()

    def _last_document(self) -> tuple[tuple[int, ...], tuple[int, ...], NativeStreamStats]:
        """Read the verdict the last observation applied (stateless read)."""
        text = _read_string(
            lambda b, c: int(_lib.vy_norm_last_document(self._handle, b, c)),
        )
        lines = text.split("\n")
        if len(lines) != 4 or lines[3] != "":
            raise NativeBridgeError(f"native stream verdict document is malformed: {text!r}")
        return (
            _tokens(lines[1]),
            _tokens(lines[2]),
            _stats_line(lines[0]),
        )

    def _require_bool(self, code: int) -> bool:
        if code in (0, 1):
            return bool(code)
        raise NativeBridgeError(f"native stream gate handle error: {code}")


def stale_floor(seconds: float) -> float:
    """The usable floor for a staleness window; the kernel sets it."""
    return float(_lib.vy_norm_stale_floor(float(seconds)))


def gap_stale(now_epoch: float, last_seen: float, stale_after: float) -> bool:
    """True when the stream has been quiet longer than the window."""
    code = int(_lib.vy_norm_gap_stale(float(now_epoch), float(last_seen), float(stale_after)))
    return bool(code)


def gap_reason() -> str:
    """Why a stalled stream is unhealthy, in the health gate's own words."""
    return _read_string(lambda b, c: int(_lib.vy_norm_gap_reason(b, c)))


def watermark(last_seq: int, incoming_seq: int) -> int:
    """Sequence watermark after an arriving event's sequence."""
    return int(_lib.vy_norm_watermark(int(last_seq), int(incoming_seq)))


def _tokens(line: str) -> tuple[int, ...]:
    if not line:
        return ()
    return tuple(int(value) for value in line.split(","))


__all__ = [
    "NativeBridgeError",
    "NativeStreamGate",
    "NativeStreamStats",
    "gap_reason",
    "gap_stale",
    "stale_floor",
    "watermark",
]
