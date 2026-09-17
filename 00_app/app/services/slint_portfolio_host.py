"""Slint Portfolio host — dumb viewport for the native view (no UI here).

The native Portfolio UI (layout, components, styling, state, interaction)
lives 100% in Rust+Slint (`rust/vayren-shell/ui/portfolio.slint` +
`rust/vayren-portfolio-view`). This module only:

- loads the `vayren_portfolio_view` cdylib (fail-closed, same handshake
  discipline as `core.native.loader`),
- blits its RGB frames into a plain QWidget (no Portfolio painting, no
  layout, no business logic),
- forwards input events (mouse/hover/wheel/keys/resize) 1:1 in logical
  units (Qt logical coordinates map directly onto Slint logical units),
- pushes backend snapshots produced by :func:`portfolio_snapshot_dict`.

Bridge snapshot schema (owned by the Python backend; the Rust side parses
it defensively — missing/mistyped degrades to honest absence, see
`PortfolioSnapshot::from_json`):
``broker{name,environment,connected,status}``, ``funds{equity,available,
used}`` (omitted when empty — legacy configured-parity), ``position`` /
``positions`` (raw records; ``flat`` flag honoured), ``orders`` /
``fills`` (raw records), ``pnl{total,unrealized,realized,today,wins,
losses}``, ``risk{status}``, ``reconciliation{status}``, ``kill{halted}``,
``lifecycle``, ``mode``, ``risk_metrics{drawdown,volatility}``.
``updated_label`` carries the bridge refresh stamp (display-only).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import platform
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
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

ABI_VERSION = 1
VIEW_LIB_ENV_OVERRIDE = "VAYREN_PORTFOLIO_VIEW_LIB"
PUMP_INTERVAL_MS = 33
PROVIDER_POLL_TICKS = 30
WHEEL_PX_PER_NOTCH = 50.0


class NativeViewError(RuntimeError):
    """The native Portfolio view library is unavailable or incompatible."""


def _lib_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_portfolio_view.dll",)
    if system == "darwin":
        return ("libvayren_portfolio_view.dylib",)
    return ("libvayren_portfolio_view.so",)


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
        "Native Portfolio view library not found. Build it first: "
        "`python scripts/build_rust.py` (requires a Rust toolchain)."
    )


def _bind(lib: Any) -> Any:
    """Declare the C ABI surface used below (names mirror the Rust exports)."""
    u32, i32, f32 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_float
    void_p = ctypes.c_void_p
    cstr = ctypes.c_char_p
    u8_p = ctypes.POINTER(ctypes.c_uint8)
    size = ctypes.c_size_t
    spec = {
        "vayren_portfolio_abi_version": (u32, []),
        "vayren_portfolio_view_create": (void_p, [u32, u32, f32]),
        "vayren_portfolio_view_destroy": (None, [void_p]),
        "vayren_portfolio_view_resize": (i32, [void_p, u32, u32, f32]),
        "vayren_portfolio_view_set_snapshot": (i32, [void_p, cstr]),
        "vayren_portfolio_view_tick": (i32, [void_p]),
        "vayren_portfolio_view_render": (i32, [void_p, u8_p, size]),
        "vayren_portfolio_view_pointer_move": (i32, [void_p, f32, f32]),
        "vayren_portfolio_view_pointer_press": (i32, [void_p, f32, f32, i32]),
        "vayren_portfolio_view_pointer_release": (i32, [void_p, f32, f32, i32]),
        "vayren_portfolio_view_pointer_leave": (i32, [void_p]),
        "vayren_portfolio_view_scroll": (i32, [void_p, f32, f32, f32, f32]),
        "vayren_portfolio_view_key": (i32, [void_p, cstr, i32]),
        "vayren_portfolio_view_refresh_requested": (i32, [void_p]),
        "vayren_portfolio_view_ack_refresh": (i32, [void_p]),
    }
    for name, (restype, argtypes) in spec.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    if lib.vayren_portfolio_abi_version() != ABI_VERSION:
        raise NativeViewError(f"native Portfolio view ABI mismatch (expected {ABI_VERSION})")
    return lib


def load_view_library() -> Any:
    """Load the cdylib and verify the ABI handshake (fail-closed)."""
    path = find_view_library()
    try:
        lib = _bind(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeViewError(f"cannot load native Portfolio view {path}: {exc}") from exc
    return lib


def _sub(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _sublist(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = state.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def portfolio_snapshot_dict(state: Any) -> dict[str, Any]:
    """Project a provider state dict onto the bridge snapshot schema.

    Pure structural pass-through (raw numbers/bools/strings/None — all
    formatting and derivation happens in Rust). Empty ``funds`` mappings are
    omitted so Rust ``is_configured`` keeps legacy parity (an empty dict is
    not a configured book). Non-dict input yields the honest empty snapshot.
    """
    if not isinstance(state, dict):
        return {}
    broker = _sub(state, "broker")
    funds = _sub(state, "funds")
    pnl = _sub(state, "pnl")
    snapshot: dict[str, Any] = {
        "broker": {
            "name": broker.get("name"),
            "environment": broker.get("environment"),
            "connected": broker.get("connected"),
            "status": broker.get("status"),
        },
        "pnl": {
            "total": pnl.get("total"),
            "unrealized": pnl.get("unrealized"),
            "realized": pnl.get("realized"),
            "today": pnl.get("today"),
            "wins": pnl.get("wins"),
            "losses": pnl.get("losses"),
        },
        "positions": _sublist(state, "positions"),
        "orders": _sublist(state, "orders"),
        "fills": _sublist(state, "fills"),
        "risk": _sub(state, "risk"),
        "reconciliation": _sub(state, "reconciliation"),
        "kill": _sub(state, "kill"),
        "lifecycle": state.get("lifecycle"),
        "mode": state.get("mode"),
        "risk_metrics": _sub(state, "risk_metrics"),
    }
    if funds:
        snapshot["funds"] = {
            "equity": funds.get("equity"),
            "available": funds.get("available"),
            "used": funds.get("used"),
        }
    position = state.get("position")
    if isinstance(position, dict):
        snapshot["position"] = position
    return snapshot


class SlintPortfolioHost(QWidget):
    """Plain viewport blitting native Slint Portfolio frames.

    Zero Portfolio presentation lives here: pixels arrive from Rust, input
    events leave for Rust, backend facts arrive via ``state_provider`` (the
    same callable shape ``LiveWorkspace`` consumes). When the cdylib is
    unavailable the widget stays dark with one muted status line (honest
    bridge health, not portfolio content) and logs the cause.
    """

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
            logger.warning("slint portfolio host unavailable: %s", exc)

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
        handle = self._lib.vayren_portfolio_view_create(width, height, dpr)
        if not handle:
            logger.warning("slint portfolio host: native view creation failed")
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
            self._lib.vayren_portfolio_view_resize(
                self._view, self._frame_w, self._frame_h, self._dpr()
            )
        if self._pump is None:
            pump = QTimer(self)
            pump.setInterval(PUMP_INTERVAL_MS)
            pump.timeout.connect(self._on_pump)
            pump.start()
            self._pump = pump

    def destroy_view(self) -> None:
        """Release the native view (idempotent; used by tests/teardown)."""
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_destroy(self._view)
        self._view = None

    # ── data feed ──

    def _push_snapshot(self, force: bool = False) -> None:
        if self._lib is None or self._view is None or self._provider is None:
            return
        try:
            state = self._provider()
        except Exception:  # noqa: BLE001 (provider contract: never raises; stay safe anyway)
            logger.debug("slint portfolio host: state provider failed", exc_info=True)
            return
        snapshot = portfolio_snapshot_dict(state)
        snapshot["updated_label"] = datetime.now().strftime("Updated %H:%M:%S")
        try:
            payload = json.dumps(snapshot, sort_keys=True, default=str)
        except (TypeError, ValueError):
            logger.debug("slint portfolio host: snapshot not serializable", exc_info=True)
            return
        if not force and payload == self._last_pushed:
            return
        code = self._lib.vayren_portfolio_view_set_snapshot(self._view, payload.encode("utf-8"))
        if code == 0:
            self._last_pushed = payload
        else:
            logger.debug("slint portfolio host: snapshot rejected (code %s)", code)

    def _on_pump(self) -> None:
        if self._lib is None or self._view is None:
            return
        with contextlib.suppress(Exception):
            self._lib.vayren_portfolio_view_tick(self._view)
            if self._lib.vayren_portfolio_view_refresh_requested(self._view):
                self._push_snapshot(force=True)
                self._lib.vayren_portfolio_view_ack_refresh(self._view)
            else:
                self._provider_ticks += 1
                if self._provider_ticks >= PROVIDER_POLL_TICKS:
                    self._provider_ticks = 0
                    self._push_snapshot()
            width = max(1, int(round(self.width() * self._dpr())))
            height = max(1, int(round(self.height() * self._dpr())))
            self._alloc_frame(width, height)
            painted = self._lib.vayren_portfolio_view_render(
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
                "Native Portfolio view unavailable — build the Rust workspace.",
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
                self._lib.vayren_portfolio_view_pointer_move(self._view, pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mousePressEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_pointer_press(self._view, pos.x(), pos.y(), button)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_pointer_release(
                    self._view, pos.x(), pos.y(), button
                )

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_pointer_leave(self._view)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt override)
        super().wheelEvent(event)
        if self._lib is not None and self._view is not None:
            delta = event.angleDelta()
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_scroll(
                    self._view,
                    pos.x(),
                    pos.y(),
                    delta.x() / 120.0 * WHEEL_PX_PER_NOTCH,
                    delta.y() / 120.0 * WHEEL_PX_PER_NOTCH,
                )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyPressEvent(event)
        # Held-key auto-repeat is intentionally not forwarded: the screen has
        # no text inputs, so repeats would only double-fire actions.
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_key(self._view, event.text().encode("utf-8"), 1)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyReleaseEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_key(self._view, event.text().encode("utf-8"), 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if self._lib is not None and self._view is not None:
            dpr = self._dpr()
            width = max(1, int(round(self.width() * dpr)))
            height = max(1, int(round(self.height() * dpr)))
            self._alloc_frame(width, height)
            with contextlib.suppress(Exception):
                self._lib.vayren_portfolio_view_resize(self._view, width, height, dpr)
