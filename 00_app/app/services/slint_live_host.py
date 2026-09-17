"""Slint Live host — dumb viewport for the native Live view (no UI here).

The native Live UI (layout, components, styling, state, interaction)
lives 100% in Rust+Slint (`rust/vayren-shell/ui/live.slint` +
`rust/vayren-live-view`). This module only:

- loads the `vayren_live_view` cdylib (fail-closed, same handshake
  discipline as `core.native.loader`),
- blits its RGB frames into a plain QWidget (no Live painting, no
  layout, no business logic),
- forwards input events (mouse/hover/wheel/keys/resize) 1:1 in logical
  units (Qt logical coordinates map directly onto Slint logical units),
- pushes backend snapshots produced by :func:`live_snapshot_dict`,
- drains UI action events (start/stop/halt/arm/mode/setup) as Qt signals
  wired to the real `LiveTradingService` (same contract `LiveWorkspace`
  signals carry: start/stop/halt/arm/mode/setup/configure_broker).

Bridge snapshot schema (owned by the Python backend; the Rust side parses
it defensively — missing/mistyped degrades to honest absence, see
`live::LiveState::apply_snapshot`): everything the
`_live_state_provider` dict carries, projected into JSON-safe atoms —
``mode``, ``broker{name,status,connected,reason,environment,latency_ms,
last_heartbeat,capabilities}``, ``gates[{name,status,reason}]``,
``session_status``, ``status_reason``, ``kill{halted}``,
``can_halt``/``can_arm``/``start_blockers``/``arm_blockers``,
``positions``/``orders``/``fills`` (raw records), ``strategy``/``position``
(blocks), ``pnl{realized,unrealized,exposure,orders,fills,wins,losses}``,
``risk{status,limits,decisions}``, ``reconciliation{status,positions,
orders,last_check,mismatches,blocks_live}``, ``events``,
``available_strategies``/``available_symbols``/``selected_symbols``/
``available_timeframes``/``selected_timeframe``/``quantity``,
``market_symbol``/``market_timeframe``/``market_bars[{o,h,l,c}]``.

Action event schema (Rust → Python, one JSON object per accepted UI
intent, drained each pump): ``{"action":"start","confirmed":bool}``,
``{"action":"stop"}``, ``{"action":"halt"}``, ``{"action":"arm"}``,
``{"action":"mode","mode":"PAPER"|"SANDBOX"|"LIVE"}``,
``{"action":"setup","strategy_name":str,"symbols":[...],"timeframe":str,
"quantity":float}``, ``{"action":"configure_broker"}``.
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

ABI_VERSION = 1
VIEW_LIB_ENV_OVERRIDE = "VAYREN_LIVE_VIEW_LIB"
PUMP_INTERVAL_MS = 33
PROVIDER_POLL_TICKS = 30
WHEEL_PX_PER_NOTCH = 50.0
MAX_BARS = 500
_EVENT_BUFFER = 1024


class NativeViewError(RuntimeError):
    """The native Live view library is unavailable or incompatible."""


def _lib_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_live_view.dll",)
    if system == "darwin":
        return ("libvayren_live_view.dylib",)
    return ("libvayren_live_view.so",)


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
        "Native Live view library not found. Build it first: "
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
        "vayren_live_abi_version": (u32, []),
        "vayren_live_view_create": (void_p, [u32, u32, f32]),
        "vayren_live_view_destroy": (None, [void_p]),
        "vayren_live_view_resize": (i32, [void_p, u32, u32, f32]),
        "vayren_live_view_set_snapshot": (i32, [void_p, cstr]),
        "vayren_live_view_next_event": (i32, [void_p, u8_p, size]),
        "vayren_live_view_tick": (i32, [void_p]),
        "vayren_live_view_render": (i32, [void_p, u8_p, size]),
        "vayren_live_view_pointer_move": (i32, [void_p, f32, f32]),
        "vayren_live_view_pointer_press": (i32, [void_p, f32, f32, i32]),
        "vayren_live_view_pointer_release": (i32, [void_p, f32, f32, i32]),
        "vayren_live_view_pointer_leave": (i32, [void_p]),
        "vayren_live_view_scroll": (i32, [void_p, f32, f32, f32, f32]),
        "vayren_live_view_key": (i32, [void_p, cstr, i32]),
        "vayren_live_view_refresh_requested": (i32, [void_p]),
        "vayren_live_view_ack_refresh": (i32, [void_p]),
    }
    for name, (restype, argtypes) in spec.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    if lib.vayren_live_abi_version() != ABI_VERSION:
        raise NativeViewError(f"native Live view ABI mismatch (expected {ABI_VERSION})")
    return lib


def load_view_library() -> Any:
    """Load the cdylib and verify the ABI handshake (fail-closed)."""
    path = find_view_library()
    try:
        lib = _bind(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeViewError(f"cannot load native Live view {path}: {exc}") from exc
    return lib


def _sub(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _sublist(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = state.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _bar_records(bars: Any) -> list[dict[str, float]]:
    """Convert the provider's ``market_bars`` (Bar tuples) to JSON OHLC."""
    records: list[dict[str, float]] = []
    if bars is None:
        return records
    rows = bars if isinstance(bars, (list, tuple)) else []
    for bar in rows[-MAX_BARS:]:
        values = getattr(bar, "__dict__", None) or (bar if isinstance(bar, dict) else None)
        try:
            if isinstance(values, dict):
                data: dict[str, Any] = dict(values)
            elif isinstance(bar, (list, tuple)) and len(bar) >= 4:
                data = {"o": bar[0], "h": bar[1], "l": bar[2], "c": bar[3]}
            else:
                data = {
                    "o": getattr(bar, "open", None),
                    "h": getattr(bar, "high", None),
                    "l": getattr(bar, "low", None),
                    "c": getattr(bar, "close", None),
                }
            open_ = _num_at(data, "o", "open")
            high = _num_at(data, "h", "high")
            low = _num_at(data, "l", "low")
            close = _num_at(data, "c", "close")
        except (TypeError, ValueError, KeyError):
            continue
        records.append({"o": open_, "h": high, "l": low, "c": close})
    return records


def _num_at(data: dict[str, Any], *names: str) -> float:
    """First present numeric value under any of `names` (KeyError if none)."""
    for name in names:
        value = data.get(name)
        if value is not None:
            return float(value)
    raise KeyError(names[0])


def live_snapshot_dict(state: Any) -> dict[str, Any]:
    """Project the live-state provider dict onto the bridge snapshot schema.

    Pure structural projection (raw numbers/bools/strings/None/lists —
    all formatting and derivation happens in Rust). Non-dict input yields
    the honest empty snapshot. Bar objects become ``[{o,h,l,c}]``.
    """
    if not isinstance(state, dict):
        return {}
    broker = _sub(state, "broker")
    kill = _sub(state, "kill")
    pnl = _sub(state, "pnl")
    risk = _sub(state, "risk")
    recon = _sub(state, "reconciliation")
    strategy = _sub(state, "strategy")
    position = _sub(state, "position")
    gates: list[dict[str, Any]] = [
        {
            "name": str(gate.get("name", "")),
            "status": str(gate.get("status", "")),
            "reason": str(gate.get("reason", "")),
        }
        for gate in _sublist(state, "gates")
        if isinstance(gate, dict)
    ]
    snapshot: dict[str, Any] = {
        "mode": state.get("mode"),
        "broker": {
            "name": broker.get("name"),
            "status": broker.get("status"),
            "connected": broker.get("connected"),
            "reason": broker.get("reason"),
            "environment": broker.get("environment"),
            "latency_ms": broker.get("latency_ms"),
            "last_heartbeat": broker.get("last_heartbeat"),
            "capabilities": [c for c in broker.get("capabilities", []) if c is not None]
            if isinstance(broker.get("capabilities"), list)
            else [],
        },
        "gates": gates,
        "session_status": state.get("session_status"),
        "status_reason": str(state.get("status_reason", "") or ""),
        "kill": {"halted": bool(kill.get("halted"))},
        "can_halt": bool(state.get("can_halt")),
        "can_arm": bool(state.get("can_arm")),
        "arm_blockers": [str(b) for b in state.get("arm_blockers", []) or []],
        "start_blockers": [str(b) for b in state.get("start_blockers", []) or []],
        "positions": _sublist(state, "positions"),
        "position": position,
        "orders": _sublist(state, "orders"),
        "fills": _sublist(state, "fills"),
        "pnl": {
            "realized": pnl.get("realized"),
            "unrealized": pnl.get("unrealized"),
            "exposure": pnl.get("exposure"),
            "orders": pnl.get("orders"),
            "fills": pnl.get("fills"),
            "wins": pnl.get("wins"),
            "losses": pnl.get("losses"),
        },
        "strategy": strategy,
        "risk": {
            "status": risk.get("status"),
            "limits": [
                list(entry) if isinstance(entry, (list, tuple)) else str(entry)
                for entry in risk.get("limits", []) or []
            ],
            "decisions": [
                list(entry) if isinstance(entry, (list, tuple)) else str(entry)
                for entry in risk.get("decisions", []) or []
            ],
        },
        "reconciliation": {
            "status": recon.get("status"),
            "positions": recon.get("positions"),
            "orders": recon.get("orders"),
            "last_check": recon.get("last_check"),
            "mismatches": recon.get("mismatches", []),
            "blocks_live": bool(recon.get("blocks_live", True)),
        },
        "events": _sublist(state, "events"),
        "available_strategies": [str(s) for s in state.get("available_strategies", []) or []],
        "available_symbols": [str(s) for s in state.get("available_symbols", []) or []],
        "selected_symbols": [str(s) for s in state.get("selected_symbols", []) or []],
        "available_timeframes": [str(t) for t in state.get("available_timeframes", []) or []],
        "selected_timeframe": str(state.get("selected_timeframe", "") or ""),
        "quantity": state.get("quantity"),
        "market_symbol": str(state.get("market_symbol", "") or ""),
        "market_timeframe": str(state.get("market_timeframe", "") or ""),
        "market_bars": _bar_records(state.get("market_bars")),
    }
    return snapshot


class SlintLiveHost(QWidget):
    """Plain viewport blitting native Slint Live frames.

    Zero Live presentation lives here: pixels arrive from Rust, input events
    leave for Rust, backend facts arrive via ``state_provider`` (the same
    callable shape ``LiveWorkspace`` consumes), and UI action intents arrive
    back as Qt signals wired to the real `LiveTradingService` (the same
    contract `LiveWorkspace` signals carry). When the cdylib is unavailable
    the widget stays dark with one muted status line (honest bridge health,
    not live content) and logs the cause.
    """

    mode_requested = Signal(str)
    arm_requested = Signal()
    halt_requested = Signal()
    setup_changed = Signal(dict)
    start_requested = Signal(bool)
    stop_requested = Signal()
    configure_broker_requested = Signal()

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
            logger.warning("slint live host unavailable: %s", exc)

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
        handle = self._lib.vayren_live_view_create(width, height, dpr)
        if not handle:
            logger.warning("slint live host: native view creation failed")
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
            self._lib.vayren_live_view_resize(self._view, self._frame_w, self._frame_h, self._dpr())
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
                self._lib.vayren_live_view_destroy(self._view)
        self._view = None

    # ── data feed: snapshots out, actions in ──

    def refresh_now(self) -> None:
        """Push a fresh backend snapshot immediately (broker-state change)."""
        self._push_snapshot(force=True)

    def _push_snapshot(self, force: bool = False) -> None:
        if self._lib is None or self._view is None or self._provider is None:
            return
        try:
            state = self._provider()
        except Exception:  # noqa: BLE001 (provider contract: never raises; stay safe anyway)
            logger.debug("slint live host: state provider failed", exc_info=True)
            return
        snapshot = live_snapshot_dict(state)
        try:
            payload = json.dumps(snapshot, sort_keys=True, default=str)
        except (TypeError, ValueError):
            logger.debug("slint live host: snapshot not serializable", exc_info=True)
            return
        if not force and payload == self._last_pushed:
            return
        code = self._lib.vayren_live_view_set_snapshot(self._view, payload.encode("utf-8"))
        if code == 0:
            self._last_pushed = payload
        else:
            logger.debug("slint live host: snapshot rejected (code %s)", code)

    def _drain_events(self) -> None:
        """Pop accepted UI intents from the native queue and signal outward."""
        if self._lib is None or self._view is None:
            return
        buf = (ctypes.c_uint8 * _EVENT_BUFFER)()
        while True:
            try:
                length = self._lib.vayren_live_view_next_event(self._view, buf, len(buf))
            except Exception:  # noqa: BLE001
                logger.debug("slint live host: event drain failed", exc_info=True)
                return
            if length <= 0:
                return
            try:
                event = json.loads(bytes(buf[:length]).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                logger.debug("slint live host: unparsable action event", exc_info=True)
                return
            self._dispatch_action(event)

    def _dispatch_action(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        action = event.get("action")
        try:
            if action == "start":
                self.start_requested.emit(bool(event.get("confirmed", False)))
            elif action == "stop":
                self.stop_requested.emit()
            elif action == "halt":
                self.halt_requested.emit()
            elif action == "arm":
                self.arm_requested.emit()
            elif action == "mode":
                mode = str(event.get("mode", "") or "")
                if mode:
                    self.mode_requested.emit(mode)
            elif action == "setup":
                setup = {
                    "strategy_name": str(event.get("strategy_name", "") or ""),
                    "symbols": [str(s) for s in event.get("symbols", []) or ()],
                    "timeframe": str(event.get("timeframe", "") or ""),
                    "quantity": event.get("quantity"),
                }
                self.setup_changed.emit(setup)
            elif action == "configure_broker":
                self.configure_broker_requested.emit()
            else:
                logger.debug("slint live host: unknown action %r", action)
        except Exception:  # noqa: BLE001
            logger.debug("slint live host: action dispatch failed", exc_info=True)

    def _on_pump(self) -> None:
        if self._lib is None or self._view is None:
            return
        with contextlib.suppress(Exception):
            self._lib.vayren_live_view_tick(self._view)
            refreshed = bool(self._lib.vayren_live_view_refresh_requested(self._view))
            self._drain_events()
            if refreshed:
                self._push_snapshot(force=True)
                self._lib.vayren_live_view_ack_refresh(self._view)
            else:
                self._provider_ticks += 1
                if self._provider_ticks >= PROVIDER_POLL_TICKS:
                    self._provider_ticks = 0
                    self._push_snapshot()
            width = max(1, int(round(self.width() * self._dpr())))
            height = max(1, int(round(self.height() * self._dpr())))
            self._alloc_frame(width, height)
            painted = self._lib.vayren_live_view_render(
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
                "Native Live view unavailable — build the Rust workspace.",
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
                self._lib.vayren_live_view_pointer_move(self._view, pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mousePressEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_live_view_pointer_press(self._view, pos.x(), pos.y(), button)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_live_view_pointer_release(self._view, pos.x(), pos.y(), button)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_live_view_pointer_leave(self._view)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt override)
        super().wheelEvent(event)
        if self._lib is not None and self._view is not None:
            delta = event.angleDelta()
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_live_view_scroll(
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
                self._lib.vayren_live_view_key(self._view, event.text().encode("utf-8"), 1)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        super().keyReleaseEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_live_view_key(self._view, event.text().encode("utf-8"), 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if self._lib is not None and self._view is not None:
            dpr = self._dpr()
            width = max(1, int(round(self.width() * dpr)))
            height = max(1, int(round(self.height() * dpr)))
            self._alloc_frame(width, height)
            with contextlib.suppress(Exception):
                self._lib.vayren_live_view_resize(self._view, width, height, dpr)
