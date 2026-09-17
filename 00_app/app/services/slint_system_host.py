"""Slint System host — dumb viewport for the native System view (no UI here).

The native System UI (the broker status surface: identity, environment,
health, LIVE readiness, capabilities, blockers) lives 100% in Rust+Slint
(`rust/vayren-shell/ui/app.slint` `BrokerPanel` + `rust/vayren-system-view`).
This module only:

- loads the `vayren_system_view` cdylib (fail-closed, same handshake
  discipline as `core.native.loader`),
- blits its RGB frames into a plain QWidget (no System painting, no
  layout, no business logic),
- forwards input events (mouse/hover/wheel/keys/resize) 1:1 in logical
  units (Qt logical coordinates map directly onto Slint logical units),
- pushes backend snapshots produced by :func:`system_snapshot_dict`.

The hosted surface is read-only status (the migrated Slint design carries
no management actions), so there is no action-event channel: broker
management flows stay in `BrokerManager` (backend, untouched). When the
cdylib is unavailable the widget stays dark with one muted status line.

Bridge snapshot schema (owned by the Python backend; the Rust side parses
it defensively — missing/mistyped degrades to honest absence, see
`BrokerPanel::from_json`): ``broker_id``, ``display_name``,
``environment`` (paper/sandbox/live; anything else falls back to the
PAPER default), ``health`` (a `HealthState` label; unknown stays UNKNOWN),
``live_ready`` (backend verdict only), ``capabilities[{id,label,kind}]``
(kind 0 = supported, 1 = not supported, 2 = not configured) and
``blockers[...]`` (backend reason lines).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import platform
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

ABI_VERSION = 2
VIEW_LIB_ENV_OVERRIDE = "VAYREN_SYSTEM_VIEW_LIB"
PUMP_INTERVAL_MS = 33
PROVIDER_POLL_TICKS = 30
WHEEL_PX_PER_NOTCH = 50.0
EVENT_BUFFER = 2048


class NativeViewError(RuntimeError):
    """The native System view library is unavailable or incompatible."""


def _lib_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_system_view.dll",)
    if system == "darwin":
        return ("libvayren_system_view.dylib",)
    return ("libvayren_system_view.so",)


def find_view_library() -> Path:
    """Locate the view cdylib: env override, then repo release/debug targets."""
    override = os.environ.get(VIEW_LIB_ENV_OVERRIDE, "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise NativeViewError(f"{VIEW_LIB_ENV_OVERRIDE} points at a missing file: {override!r}")
    root = Path(__file__).resolve().parent.parent.parent.parent
    for profile in ("release", "debug"):
        for name in _lib_names():
            candidate = root / "rust" / "target" / profile / name
            if candidate.is_file():
                return candidate
    raise NativeViewError(
        "Native System view library not found. Build it first: "
        "`python scripts/build_rust.py` (requires a Rust toolchain)."
    )


def _bind(lib: Any) -> Any:
    """Declare the C ABI surface used below (names mirror the Rust exports)."""
    u32, i32, f32 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_float
    void_p = ctypes.c_void_p
    cstr = ctypes.c_char_p
    u8_p = ctypes.POINTER(ctypes.c_uint8)
    size = ctypes.c_size_t
    _declare(lib, {"vayren_system_abi_version": (u32, [])})
    if lib.vayren_system_abi_version() != ABI_VERSION:
        raise NativeViewError(f"native System view ABI mismatch (expected {ABI_VERSION})")
    spec = {
        "vayren_system_abi_version": (u32, []),
        "vayren_system_view_next_event": (i32, [void_p, u8_p, size]),
        "vayren_system_view_refresh_requested": (i32, [void_p]),
        "vayren_system_view_ack_refresh": (i32, [void_p]),
        "vayren_system_view_create": (void_p, [u32, u32, f32]),
        "vayren_system_view_destroy": (None, [void_p]),
        "vayren_system_view_resize": (i32, [void_p, u32, u32, f32]),
        "vayren_system_view_set_snapshot": (i32, [void_p, cstr]),
        "vayren_system_view_tick": (i32, [void_p]),
        "vayren_system_view_render": (i32, [void_p, u8_p, size]),
        "vayren_system_view_pointer_move": (i32, [void_p, f32, f32]),
        "vayren_system_view_pointer_press": (i32, [void_p, f32, f32, i32]),
        "vayren_system_view_pointer_release": (i32, [void_p, f32, f32, i32]),
        "vayren_system_view_pointer_leave": (i32, [void_p]),
        "vayren_system_view_scroll": (i32, [void_p, f32, f32, f32, f32]),
        "vayren_system_view_key": (i32, [void_p, cstr, i32]),
    }
    _declare(lib, spec)
    return lib


def _declare(lib: Any, spec: dict[str, tuple[Any, list[Any]]]) -> None:
    """Attach ctypes signatures; a missing export fails closed (AttributeError)."""
    for name, (restype, argtypes) in spec.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes


def load_view_library() -> Any:
    """Load the cdylib and verify the ABI handshake (fail-closed)."""
    path = find_view_library()
    try:
        lib = _bind(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeViewError(f"cannot load native System view {path}: {exc}") from exc
    return lib


def _check_rows(checks: Any) -> list[dict[str, Any]]:
    """Map manager check results onto capability rows (honest buckets).

    ``READY`` → supported (green); ``FAILED…`` → amber (attention: the Slint
    panel colors kind 0 green, kind 2 amber, anything else muted); anything
    else → muted with the raw value or NOT_CONFIGURED.
    """
    rows: list[dict[str, Any]] = []
    if not isinstance(checks, dict):
        return rows
    for check_id in sorted(checks):
        value = checks[check_id]
        text = "" if value is None else str(value)
        if text == "READY":
            rows.append({"id": str(check_id), "label": "SUPPORTED", "kind": 0})
        elif text.startswith("FAILED"):
            rows.append({"id": str(check_id), "label": text, "kind": 2})
        else:
            rows.append({"id": str(check_id), "label": text or "NOT_CONFIGURED", "kind": 1})
    return rows


def system_snapshot_dict(state: Any) -> dict[str, Any]:
    """Project the broker-manager selection onto the bridge snapshot schema.

    Pure structural projection (raw strings/bools/rows — all formatting and
    derivation happens in Rust). ``state`` is ``{"selection": {...} | None,
    "record": {...} | None}``; anything else yields the honest
    NOT CONFIGURED snapshot.
    """
    empty: dict[str, Any] = {
        "broker_id": "",
        "display_name": "NOT CONFIGURED",
        "environment": "paper",
        "health": "UNKNOWN",
        "live_ready": False,
        "capabilities": [],
        "blockers": ["no broker selected"],
    }
    if not isinstance(state, dict):
        return dict(empty)
    selection = state.get("selection")
    record = state.get("record")
    if not isinstance(selection, dict) or not isinstance(record, dict):
        return dict(empty)
    name = str(selection.get("name", "") or "")
    environment = str(selection.get("environment", "") or "")
    callback_url = str(state.get("callback_url", "") or "")
    status = str(record.get("status", "") or "")
    reason = str(record.get("reason", "") or "")
    blockers = [reason] if reason and status not in ("CONNECTED", "LIVE_READY") else []
    raw_funds = record.get("funds")
    funds = raw_funds if isinstance(raw_funds, dict) else {}
    return {
        "broker_id": name,
        "display_name": str(record.get("name", "") or name or "NOT CONFIGURED"),
        "environment": environment,
        "health": status,
        "status_raw": status,
        "live_ready": status == "LIVE_READY",
        "checks": record.get("checks") if isinstance(record.get("checks"), dict) else {},
        "capabilities": _check_rows(record.get("checks")),
        "account_id": str(record.get("account_id", "") or ""),
        "funds_available": funds.get("available"),
        "funds_used": funds.get("used"),
        "funds_total": funds.get("total"),
        "positions_open": record.get("positions_open"),
        "orders_open": record.get("orders_open"),
        "last_sync": str(record.get("last_sync", "") or ""),
        "api_key_masked": str(record.get("api_key_masked", "") or ""),
        "configured": bool(record.get("configured")),
        "can_login": bool(record.get("can_login")),
        "can_disconnect": bool(record.get("can_disconnect")),
        "can_refresh": bool(record.get("can_refresh")),
        "callback_url": callback_url,
        "blockers": blockers,
    }


class SlintSystemHost(QWidget):
    """Plain viewport blitting native Slint System frames.

    Zero System presentation lives here: pixels arrive from Rust, input
    events leave for Rust, backend facts arrive via ``state_provider`` (a
    ``{"selection", "record"}`` dict — see :func:`system_snapshot_dict`).
    When the cdylib is unavailable the widget stays dark with one muted
    status line (honest bridge health, not system content) and logs the
    cause.

    Action intents accepted in Rust drain through these signals — the
    SAME contract the retained Qt `BrokersWorkspace` carried (broker_id
    resolved to the current selection; `configure_requested` carries the
    one-time api_key/api_secret dict).
    """

    configure_requested = Signal(str, dict)
    login_requested = Signal(str)
    refresh_requested = Signal(str)
    disconnect_requested = Signal(str)
    remove_requested = Signal(str)
    copy_callback_requested = Signal(str)

    def __init__(
        self,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = state_provider
        self._lib: Any | None = None
        self._view: Any | None = None
        self._frame = bytearray()
        self._frame_w = 0
        self._frame_h = 0
        self._last_pushed = ""
        self._provider_ticks = 0
        self._pump: QTimer | None = None
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        try:
            self._lib = load_view_library()
        except NativeViewError as exc:
            logger.warning("slint system host unavailable: %s", exc)

    @property
    def is_native_available(self) -> bool:
        """Whether the native view library loaded (bridge health probe)."""
        return self._lib is not None

    # ── lifecycle ──

    def _dpr(self) -> float:
        try:
            return max(0.25, min(8.0, float(self.devicePixelRatio())))
        except (TypeError, ValueError):
            return 1.0

    def _ensure_view(self) -> bool:
        if self._lib is None or self._view is not None:
            return self._view is not None
        dpr = self._dpr()
        width = max(1, int(round(self.width() * dpr)))
        height = max(1, int(round(self.height() * dpr)))
        handle = self._lib.vayren_system_view_create(width, height, dpr)
        if not handle:
            logger.warning("slint system host: native view creation failed")
            return False
        self._view = handle
        self._alloc_frame(width, height)
        self._push_snapshot(force=True)
        return True

    def _alloc_frame(self, width: int, height: int) -> None:
        if width != self._frame_w or height != self._frame_h:
            self._frame_w, self._frame_h = width, height
            self._frame = bytearray(width * height * 3)

    def showEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if self._ensure_view() and self._lib is not None and self._view is not None:
            self._lib.vayren_system_view_resize(
                self._view, self._frame_w, self._frame_h, self._dpr()
            )
        if self._pump is None:
            pump = QTimer(self)
            pump.setInterval(PUMP_INTERVAL_MS)
            pump.timeout.connect(self._on_pump)
            pump.start()
            self._pump = pump

    def hideEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        if self._pump is not None:
            self._pump.stop()
            self._pump = None
        super().hideEvent(event)

    def destroy_view(self) -> None:
        """Release the native view (idempotent; used by tests/teardown)."""
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_destroy(self._view)
        self._view = None

    def refresh_now(self) -> None:
        """Push a fresh backend snapshot immediately (broker-state change)."""
        self._push_snapshot(force=True)

    # ── data feed ──

    def _push_snapshot(self, force: bool = False) -> None:
        if self._lib is None or self._view is None or self._provider is None:
            return
        try:
            state = self._provider()
        except Exception:  # noqa: BLE001 (provider contract: never raises; stay safe anyway)
            logger.debug("slint system host: state provider failed", exc_info=True)
            return
        snapshot = system_snapshot_dict(state)
        try:
            payload = json.dumps(snapshot, sort_keys=True, default=str)
        except (TypeError, ValueError):
            logger.debug("slint system host: snapshot not serializable", exc_info=True)
            return
        if not force and payload == self._last_pushed:
            return
        code = self._lib.vayren_system_view_set_snapshot(self._view, payload.encode("utf-8"))
        if code == 0:
            self._last_pushed = payload
        else:
            logger.debug("slint system host: snapshot rejected (code %s)", code)

    def _selected_broker_id(self) -> str:
        if self._provider is None:
            return ""
        try:
            state = self._provider()
        except Exception:  # noqa: BLE001
            return ""
        selection = state.get("selection") if isinstance(state, dict) else None
        return str(selection.get("name", "") or "") if isinstance(selection, dict) else ""

    def _drain_events(self) -> None:
        """Pop accepted UI intents from the native queue and signal outward."""
        if self._lib is None or self._view is None:
            return
        buf = (ctypes.c_uint8 * EVENT_BUFFER)()
        while True:
            try:
                length = self._lib.vayren_system_view_next_event(self._view, buf, len(buf))
            except Exception:  # noqa: BLE001
                logger.debug("slint system host: event drain failed", exc_info=True)
                return
            if length <= 0:
                return
            try:
                event = json.loads(bytes(buf[:length]).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                logger.debug("slint system host: unparsable action event", exc_info=True)
                return
            self._dispatch_action(event)

    def _dispatch_action(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        action = event.get("action")
        broker_id = self._selected_broker_id()
        try:
            if action == "login" and broker_id:
                self.login_requested.emit(broker_id)
            elif action == "refresh" and broker_id:
                self.refresh_requested.emit(broker_id)
            elif action == "disconnect" and broker_id:
                self.disconnect_requested.emit(broker_id)
            elif action == "remove" and broker_id:
                self.remove_requested.emit(broker_id)
            elif action == "configure" and broker_id:
                self.configure_requested.emit(
                    broker_id,
                    {
                        "api_key": str(event.get("api_key", "") or ""),
                        "api_secret": str(event.get("api_secret", "") or ""),
                    },
                )
            elif action == "copy_url":
                self.copy_callback_requested.emit(broker_id)
            else:
                logger.debug("slint system host: dropped action %r (no selection?)", action)
        except Exception:  # noqa: BLE001
            logger.debug("slint system host: action dispatch failed", exc_info=True)

    def _on_pump(self) -> None:
        if self._lib is None or self._view is None:
            return
        with contextlib.suppress(Exception):
            self._lib.vayren_system_view_tick(self._view)
            refreshed = bool(self._lib.vayren_system_view_refresh_requested(self._view))
            self._drain_events()
            if refreshed:
                # Dispatched actions mutate backend state synchronously in
                # the handlers; re-read once so the panel reflects the new
                # facts without waiting for the poll tick.
                self._push_snapshot(force=True)
                self._lib.vayren_system_view_ack_refresh(self._view)
            else:
                self._provider_ticks += 1
                if self._provider_ticks >= PROVIDER_POLL_TICKS:
                    self._provider_ticks = 0
                    self._push_snapshot()
            width = max(1, int(round(self.width() * self._dpr())))
            height = max(1, int(round(self.height() * self._dpr())))
            self._alloc_frame(width, height)
            painted = self._lib.vayren_system_view_render(
                self._view,
                (ctypes.c_uint8 * len(self._frame)).from_buffer(self._frame),
                len(self._frame),
            )
            if painted:
                self.update()

    # ── presentation: blit only ──

    def paintEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        painter = QPainter(self)
        if self._lib is None or self._view is None or not self._frame:
            painter.fillRect(self.rect(), self.palette().color(self.backgroundRole()))
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                "Native System view unavailable — build the Rust workspace.",
            )
            painter.end()
            return
        image = QImage(
            self._frame,
            self._frame_w,
            self._frame_h,
            self._frame_w * 3,
            QImage.Format.Format_RGB888,
        )
        image.setDevicePixelRatio(self._dpr())
        painter.drawImage(0, 0, image)
        painter.end()

    # ── input forwarding (1:1, logical units) ──

    @staticmethod
    def _button(button: Qt.MouseButton) -> int | None:
        if button == Qt.MouseButton.LeftButton:
            return 0
        if button == Qt.MouseButton.RightButton:
            return 1
        if button == Qt.MouseButton.MiddleButton:
            return 2
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mouseMoveEvent(event)
        if self._lib is not None and self._view is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_pointer_move(self._view, pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mousePressEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_pointer_press(self._view, pos.x(), pos.y(), button)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_pointer_release(self._view, pos.x(), pos.y(), button)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_pointer_leave(self._view)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt override)
        super().wheelEvent(event)
        if self._lib is not None and self._view is not None:
            delta = event.angleDelta()
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_scroll(
                    self._view,
                    pos.x(),
                    pos.y(),
                    delta.x() / 120.0 * WHEEL_PX_PER_NOTCH,
                    delta.y() / 120.0 * WHEEL_PX_PER_NOTCH,
                )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyPressEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_key(self._view, event.text().encode("utf-8"), 1)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyReleaseEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_key(self._view, event.text().encode("utf-8"), 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if self._lib is not None and self._view is not None:
            dpr = self._dpr()
            width = max(1, int(round(self.width() * dpr)))
            height = max(1, int(round(self.height() * dpr)))
            self._alloc_frame(width, height)
            with contextlib.suppress(Exception):
                self._lib.vayren_system_view_resize(self._view, width, height, dpr)
