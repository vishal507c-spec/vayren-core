"""Slint Research host — dumb viewport for the native Research view.

The Research UI (layout, components, styling, state, interaction) lives
100% in Rust+Slint (`rust/vayren-shell/ui/research.slint` +
`rust/vayren-research-view`). This module only:

- loads the `vayren_research_view` cdylib (same fail-closed handshake
  discipline as the Portfolio/Live hosts),
- blits RGB frames into a plain QWidget (no Research presentation here),
- forwards input events 1:1 in logical units,
- pushes snapshots built from the REAL Python ResearchService
  (`strategy.research` engine untouched), and
- drains queued UI intents and dispatches them to the service:
  create → ``create_research_experiment``, run → :class:`ResearchWorker`
  (same background thread + progress/cancel contract the engine already
  exposes), cancel → cooperative stop, select → bundle re-pull.

Snapshot schema (owned here; Rust `apply_host_snapshot` parses it
defensively): ``strategies[{name,description,version}]``,
``experiments[{id,strategy,status}]``, ``selected_id``, ``status``,
``running``, ``stale``, ``log[str]``, ``defaults{universe,symbols,start,
end,capital,slippage_pct,commission_pct,parameters,timeframe,side,
hypothesis,research_question,expected_effect}``,
``bundle{experiment,signals,trades}|null``.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import platform
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
VIEW_LIB_ENV_OVERRIDE = "VAYREN_RESEARCH_VIEW_LIB"
PUMP_INTERVAL_MS = 33
PROVIDER_POLL_TICKS = 30
LOG_CAP = 200
WHEEL_PX_PER_NOTCH = 50.0


class NativeViewError(RuntimeError):
    """The native Research view library is unavailable or incompatible."""


def _lib_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_research_view.dll",)
    if system == "darwin":
        return ("libvayren_research_view.dylib",)
    return ("libvayren_research_view.so",)


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
        "Native Research view library not found. Build it first: "
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
        "vayren_research_abi_version": (u32, []),
        "vayren_research_view_create": (void_p, [u32, u32, f32]),
        "vayren_research_view_destroy": (None, [void_p]),
        "vayren_research_view_resize": (i32, [void_p, u32, u32, f32]),
        "vayren_research_view_set_snapshot": (i32, [void_p, cstr]),
        "vayren_research_view_next_event": (i32, [void_p, u8_p, size]),
        "vayren_research_view_tick": (i32, [void_p]),
        "vayren_research_view_render": (i32, [void_p, u8_p, size]),
        "vayren_research_view_pointer_move": (i32, [void_p, f32, f32]),
        "vayren_research_view_pointer_press": (i32, [void_p, f32, f32, i32]),
        "vayren_research_view_pointer_release": (i32, [void_p, f32, f32, i32]),
        "vayren_research_view_pointer_leave": (i32, [void_p]),
        "vayren_research_view_scroll": (i32, [void_p, f32, f32, f32, f32]),
        "vayren_research_view_key": (i32, [void_p, cstr, i32]),
        "vayren_research_view_refresh_requested": (i32, [void_p]),
        "vayren_research_view_ack_refresh": (i32, [void_p]),
    }
    for name, (restype, argtypes) in spec.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    if lib.vayren_research_abi_version() != ABI_VERSION:
        raise NativeViewError(f"native Research view ABI mismatch (expected {ABI_VERSION})")
    return lib


def load_view_library() -> Any:
    """Load the cdylib and verify the ABI handshake (fail-closed)."""
    path = find_view_library()
    try:
        lib = _bind(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeViewError(f"cannot load native Research view {path}: {exc}") from exc
    return lib


def parse_params(text: Any) -> dict[str, float]:
    """`key=value, key=value` -> float dict (bad parts skipped; pure fn)."""
    params: dict[str, float] = {}
    for chunk in str(text or "").replace(";", ",").split(","):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        key, _, raw = piece.partition("=")
        try:
            params[key.strip()] = float(raw.strip())
        except (TypeError, ValueError):
            continue
    return params


def service_config_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Host config payload -> ResearchService config schema (pure)."""
    config: dict[str, Any] = {
        "strategy_name": str(payload.get("strategy_name", "") or ""),
        "universe": str(payload.get("universe", "") or ""),
        "symbols": [
            s.strip().upper()
            for s in str(payload.get("symbols", "") or "").replace(";", ",").split(",")
            if s.strip()
        ],
        "timeframe": str(payload.get("timeframe", "") or ""),
        "start_date": str(payload.get("start_date", "") or ""),
        "end_date": str(payload.get("end_date", "") or ""),
        "side": str(payload.get("side", "BOTH") or "BOTH").upper(),
        "initial_capital": _as_float(payload.get("initial_capital"), 0.0),
        "slippage_pct": _as_float(payload.get("slippage_pct"), 0.0),
        "commission_pct": _as_float(payload.get("commission_pct"), 0.0),
        "parameters": parse_params(payload.get("parameters")),
        "hypothesis": str(payload.get("hypothesis", "") or ""),
        "research_question": str(payload.get("research_question", "") or ""),
        "expected_effect": str(payload.get("expected_effect", "") or ""),
    }
    return config


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return str(int(number)) if number.is_integer() else repr(number)


def _params_text(params: Any) -> str:
    if not isinstance(params, dict):
        return ""
    return ", ".join(f"{k}={v}" for k, v in sorted(params.items()))


def research_snapshot_dict(service: Any, selected_id: str, log_lines: list[str]) -> dict[str, Any]:
    """Build one bridge snapshot from the REAL service (pure read path).

    Every value comes from persisted engine facts — never invented here.
    """
    strategies: list[dict[str, str]] = []
    try:
        names = [str(n) for n in (service.available_strategies() or [])]
    except Exception:
        logger.debug("research host: strategy list failed", exc_info=True)
        names = []
    for name in names:
        version = ""
        with contextlib.suppress(Exception):
            described = service.describe_strategy(name)
            if isinstance(described, dict):
                version = str(described.get("version", "") or "")
        strategies.append({"name": name, "description": "", "version": version})

    experiments: list[dict[str, str]] = []
    stored: dict[str, dict[str, Any]] = {}
    with contextlib.suppress(Exception):
        for exp in service.experiments():
            if isinstance(exp, dict) and exp.get("experiment_id"):
                stored[str(exp["experiment_id"])] = exp
                experiments.append(
                    {
                        "id": str(exp["experiment_id"]),
                        "strategy": str(exp.get("strategy_id", "") or ""),
                        "status": str(exp.get("status", "DRAFT") or "DRAFT"),
                    }
                )
    if selected_id not in stored:
        selected_id = experiments[-1]["id"] if experiments else ""

    snapshot: dict[str, Any] = {
        "strategies": strategies,
        "experiments": experiments,
        "selected_id": selected_id,
        "status": (stored.get(selected_id, {}) or {}).get("status", ""),
        "running": False,
        "stale": False,
        "log": list(log_lines[-LOG_CAP:]),
        "defaults": {},
        "bundle": None,
    }
    exp = stored.get(selected_id)
    if isinstance(exp, dict):
        parameters = exp.get("parameters", {})
        snapshot["defaults"] = {
            "universe": str(exp.get("universe", "") or ""),
            "symbols": ", ".join(str(s) for s in (exp.get("symbols", ()) or ())),
            "timeframe": str(exp.get("timeframe", "") or ""),
            "start": str(exp.get("start_date", "") or ""),
            "end": str(exp.get("end_date", "") or ""),
            "side": str(exp.get("side", "") or ""),
            "capital": _fmt_number(exp.get("initial_capital", "")),
            "slippage_pct": _fmt_number(exp.get("slippage_pct", "")),
            "commission_pct": _fmt_number(exp.get("commission_pct", "")),
            "parameters": _params_text(parameters),
            "hypothesis": str(
                (exp.get("hypothesis") or {}).get("text", "")
                if isinstance(exp.get("hypothesis"), dict)
                else exp.get("hypothesis") or ""
            ),
            "research_question": str(
                (exp.get("configuration") or {}).get("research_question", "")
                if isinstance(exp.get("configuration"), dict)
                else ""
            ),
            "expected_effect": str(
                (exp.get("configuration") or {}).get("expected_effect", "")
                if isinstance(exp.get("configuration"), dict)
                else ""
            ),
        }
        if exp.get("status") == "COMPLETED":
            with contextlib.suppress(Exception):
                bundle = service.experiment_bundle(selected_id)
                if isinstance(bundle, dict):
                    snapshot["bundle"] = {
                        "experiment": bundle.get("experiment", {}),
                        "signals": list(bundle.get("signals", ()) or ()),
                        "trades": list(bundle.get("trades", ()) or ()),
                    }
    return snapshot


class SlintResearchHost(QWidget):
    """Plain viewport blitting native Slint Research frames.

    Zero Research presentation lives here: pixels arrive from Rust, input
    events leave for Rust, engine facts arrive from the injected
    ``ResearchService``, and accepted UI intents return to that same
    service (create / run via the existing ``ResearchWorker`` / cancel /
    select). When the cdylib is unavailable the widget stays dark with one
    muted status line and logs the cause.
    """

    def __init__(
        self,
        service: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._lib: Any | None = None
        self._view: Any | None = None
        self._frame = bytearray()
        self._frame_w = 0
        self._frame_h = 0
        self._last_pushed = ""
        self._provider_ticks = 0
        self._selected_id = ""
        self._log_lines: list[str] = []
        self._running_id = ""
        self._worker: Any | None = None
        self._pump: QTimer | None = None
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        try:
            self._lib = load_view_library()
        except NativeViewError as exc:
            logger.warning("slint research host unavailable: %s", exc)

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
        handle = self._lib.vayren_research_view_create(width, height, dpr)
        if not handle:
            logger.warning("slint research host: native view creation failed")
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
            self._lib.vayren_research_view_resize(
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
        worker = self._worker
        if worker is not None:
            with contextlib.suppress(Exception):
                worker.request_cancel()
                worker.wait(2000)
            self._worker = None
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_destroy(self._view)
        self._view = None

    # ── data feed ──

    def _snapshot(self) -> dict[str, Any]:
        if self._service is None:
            return {"running": bool(self._running_id), "log": list(self._log_lines[-LOG_CAP:])}
        snapshot = research_snapshot_dict(self._service, self._selected_id, self._log_lines)
        snapshot["running"] = bool(self._running_id)
        if self._running_id:
            snapshot["selected_id"] = self._running_id
            snapshot["status"] = "RUNNING"
        return snapshot

    def _push_snapshot(self, force: bool = False) -> None:
        if self._lib is None or self._view is None:
            return
        try:
            payload = json.dumps(self._snapshot(), sort_keys=True, default=str)
        except (TypeError, ValueError):
            logger.debug("slint research host: snapshot not serializable", exc_info=True)
            return
        if not force and payload == self._last_pushed:
            return
        code = self._lib.vayren_research_view_set_snapshot(self._view, payload.encode("utf-8"))
        if code == 0:
            self._last_pushed = payload
        else:
            logger.debug("slint research host: snapshot rejected (code %s)", code)

    # ── intent dispatch (the ONLY place service calls happen) ──

    def _drain_actions(self) -> None:
        if self._lib is None or self._view is None:
            return
        buffer = (ctypes.c_uint8 * 65536)()
        for _ in range(32):  # bounded drain per pump tick
            try:
                size = self._lib.vayren_research_view_next_event(self._view, buffer, len(buffer))
            except Exception:  # noqa: BLE001 (bridge must never break the pump)
                logger.debug("slint research host: event drain failed", exc_info=True)
                return
            if size <= 0:
                break
            try:
                action = json.loads(bytes(buffer[:size]).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            with contextlib.suppress(Exception):
                self._dispatch(action)

    def _dispatch(self, action: dict[str, Any]) -> None:
        kind = str(action.get("action", "") or "")
        service = self._service
        if service is None:
            return
        config = service_config_from_payload(action.get("config", {}) or {})
        if kind == "create":
            if not config["hypothesis"]:
                return
            created = service.create_research_experiment(config)
            self._selected_id = str(created.get("experiment_id", "") or "")
            self._note(f"Experiment {self._selected_id} created ({created.get('status', '')}).")
        elif kind == "run":
            target = self._runnable_experiment(service, config)
            if target:
                self._start_worker(service, target)
        elif kind == "cancel":
            worker = self._worker
            if worker is not None:
                with contextlib.suppress(Exception):
                    worker.request_cancel()
                self._note("Cancellation requested — stopping at a safe checkpoint.")
        elif kind == "select":
            self._selected_id = str(action.get("id", "") or "")
        self._push_snapshot(force=True)

    def _runnable_experiment(self, service: Any, config: dict[str, Any]) -> str:
        """Selected DRAFT experiment, or fork one from the current form."""
        current = self._selected_id
        if current:
            stored = service.get_experiment(current)
            if (
                isinstance(stored, dict)
                and stored.get("status") == "DRAFT"
                and not stored.get("result_fingerprint")
            ):
                return current
        if not config["hypothesis"]:
            self._note("Write a hypothesis before running research.")
            return ""
        created = service.create_research_experiment(config)
        self._selected_id = str(created.get("experiment_id", "") or "")
        return "" if created.get("status") == "INVALID" else self._selected_id

    def _start_worker(self, service: Any, experiment_id: str) -> None:
        if self._worker is not None:
            self._note("Research already running — cancel it first.")
            return
        from app.services.research_service import ResearchWorker

        worker = ResearchWorker(service, experiment_id)
        worker.progressed.connect(self._on_worker_progress)
        worker.log_line.connect(self._on_worker_log)
        worker.finished_ok.connect(self._on_worker_finished)
        worker.finished_fail.connect(self._on_worker_failed)
        self._worker = worker
        self._running_id = experiment_id
        self._note(f"Research running: {experiment_id}.")
        worker.start()

    def _on_worker_progress(self, exp_id: str, stage: str, done: int, total: int) -> None:  # noqa: ARG002
        self._note(f"{stage} ({done}/{total}).")
        with contextlib.suppress(Exception):
            if self._lib is not None and self._view is not None:
                self._push_snapshot(force=True)

    def _on_worker_log(self, exp_id: str, message: str) -> None:  # noqa: ARG002
        self._note(str(message))

    def _on_worker_finished(self, exp_id: str) -> None:
        self._worker = None
        self._running_id = ""
        self._selected_id = str(exp_id)
        self._note(f"Research finished: {exp_id}.")
        self._push_snapshot(force=True)

    def _on_worker_failed(self, exp_id: str, message: str) -> None:  # noqa: ARG002
        self._worker = None
        self._running_id = ""
        self._note(f"Research failed: {message}")
        self._push_snapshot(force=True)

    def _note(self, message: str) -> None:
        self._log_lines.append(message)
        if len(self._log_lines) > LOG_CAP * 2:
            del self._log_lines[: len(self._log_lines) - LOG_CAP * 2]

    # ── pump ──

    def _on_pump(self) -> None:
        if self._lib is None or self._view is None:
            return
        with contextlib.suppress(Exception):
            self._lib.vayren_research_view_tick(self._view)
            self._drain_actions()
            if self._lib.vayren_research_view_refresh_requested(self._view):
                self._push_snapshot(force=True)
                self._lib.vayren_research_view_ack_refresh(self._view)
            else:
                self._provider_ticks += 1
                if self._provider_ticks >= PROVIDER_POLL_TICKS:
                    self._provider_ticks = 0
                    self._push_snapshot()
            width = max(1, int(round(self.width() * self._dpr())))
            height = max(1, int(round(self.height() * self._dpr())))
            self._alloc_frame(width, height)
            painted = self._lib.vayren_research_view_render(
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
                "Native Research view unavailable — build the Rust workspace.",
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

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        if self._lib is not None and self._view is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_pointer_move(self._view, pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mousePressEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_pointer_press(self._view, pos.x(), pos.y(), button)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        button = self._button(event.button())
        if self._lib is not None and self._view is not None and button is not None:
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_pointer_release(self._view, pos.x(), pos.y(), button)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        super().leaveEvent(event)
        if self._lib is not None and self._view is not None:
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_pointer_leave(self._view)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        super().wheelEvent(event)
        if self._lib is not None and self._view is not None:
            delta = event.angleDelta()
            pos = event.position()
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_scroll(
                    self._view,
                    pos.x(),
                    pos.y(),
                    delta.x() / 120.0 * WHEEL_PX_PER_NOTCH,
                    delta.y() / 120.0 * WHEEL_PX_PER_NOTCH,
                )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        super().keyPressEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_key(self._view, event.text().encode("utf-8"), 1)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        super().keyReleaseEvent(event)
        if self._lib is not None and self._view is not None and not event.isAutoRepeat():
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_key(self._view, event.text().encode("utf-8"), 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._lib is not None and self._view is not None:
            dpr = self._dpr()
            width = max(1, int(round(self.width() * dpr)))
            height = max(1, int(round(self.height() * dpr)))
            self._alloc_frame(width, height)
            with contextlib.suppress(Exception):
                self._lib.vayren_research_view_resize(self._view, width, height, dpr)
