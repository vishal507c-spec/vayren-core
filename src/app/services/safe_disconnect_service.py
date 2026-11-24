"""Safe, idempotent broker disconnect orchestration (composition only).

This module owns NO trading rule. It sequences existing, already-approved
operations and refuses to disconnect while the broker still has exposure.
It never cancels orders or liquidates positions by itself: a human must
review and explicitly acknowledge remaining exposure.

State machine (server-authoritative, persisted for crash recovery)::

    CONNECTED → STOP_REQUESTED → STOPPING → RECONCILING → SAFE_TO_DISCONNECT
              → DISCONNECTING → DISCONNECTED

Failure/recovery exits: ``BLOCKED`` (exposure unresolved — broker stays
connected), ``FAILED`` (a step could not be proven), and restart recovery
(a non-terminal state found at startup is failed closed, never reported as
DISCONNECTED).
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SafeDisconnectState(StrEnum):
    IDLE = "IDLE"
    CONNECTED = "CONNECTED"
    STOP_REQUESTED = "STOP_REQUESTED"
    STOPPING = "STOPPING"
    RECONCILING = "RECONCILING"
    SAFE_TO_DISCONNECT = "SAFE_TO_DISCONNECT"
    DISCONNECTING = "DISCONNECTING"
    DISCONNECTED = "DISCONNECTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


_TERMINAL = frozenset(
    {
        SafeDisconnectState.DISCONNECTED,
        SafeDisconnectState.BLOCKED,
        SafeDisconnectState.FAILED,
    }
)


@dataclass
class SafeDisconnectResult:
    """One authoritative outcome; safe to project into a snapshot."""

    state: SafeDisconnectState
    request_id: str = ""
    broker_id: str = ""
    reason: str = ""
    engine_status: str = ""
    exposure: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, str]] = field(default_factory=list)
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.state is SafeDisconnectState.DISCONNECTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "ok": self.ok,
            "request_id": self.request_id,
            "broker_id": self.broker_id,
            "reason": self.reason,
            "engine_status": self.engine_status,
            "exposure": dict(self.exposure),
            "steps": list(self.steps),
            "cached": self.cached,
        }


class SafeDisconnectService:
    """Sequence STOP → reconcile → DISCONNECT with fail-closed exposure checks.

    Dependencies are injected (existing live service + broker manager). The
    class holds only orchestration state; it does not own an engine, a broker
    socket, or any strategy rules.
    """

    _STATE_FILE = "safe_disconnect_state.json"

    def __init__(
        self,
        data_dir: str,
        service: Any,
        manager: Any | None = None,
        *,
        read_timeout_s: float = 5.0,
    ) -> None:
        self._path = Path(data_dir) / self._STATE_FILE
        self._service = service
        self._manager = manager
        self._read_timeout_s = max(0.5, float(read_timeout_s))
        self._lock = threading.Lock()
        self._running = False
        self._requests: dict[str, SafeDisconnectResult] = {}
        self._result = SafeDisconnectResult(state=SafeDisconnectState.CONNECTED)
        self._recover()

    # ── authoritative state ───────────────────────────────────────

    def _recover(self) -> None:
        """A crash mid-sequence must never be reported as DISCONNECTED."""
        raw: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        state = str(raw.get("state", "") or "").strip().upper()
        try:
            resumed = SafeDisconnectState(state)
        except ValueError:
            resumed = SafeDisconnectState.CONNECTED
        if resumed in _TERMINAL:
            self._result = SafeDisconnectResult(
                state=resumed,
                request_id=str(raw.get("request_id", "") or ""),
                broker_id=str(raw.get("broker_id", "") or ""),
                reason=str(raw.get("reason", "") or ""),
                exposure=dict(raw.get("exposure") or {}),
            )
            if resumed is SafeDisconnectState.FAILED:
                self._clear_stale_disconnect_failure()
            return
        if resumed is not SafeDisconnectState.CONNECTED:
            self._result = SafeDisconnectResult(
                state=SafeDisconnectState.FAILED,
                request_id=str(raw.get("request_id", "") or ""),
                broker_id=str(raw.get("broker_id", "") or ""),
                reason=(
                    f"gateway restarted while disconnect was {resumed.value}; "
                    "engine and broker exposure must be re-verified — re-issue DISCONNECT"
                ),
            )
            self._persist()
        else:
            self._result = SafeDisconnectResult(state=SafeDisconnectState.CONNECTED)

    def _persist(self) -> None:
        payload = {
            "state": self._result.state.value,
            "request_id": self._result.request_id,
            "broker_id": self._result.broker_id,
            "reason": self._result.reason,
            "exposure": self._result.exposure,
            "updated_at": time.time(),
        }
        with contextlib.suppress(Exception):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)

    def snapshot(self) -> dict[str, Any]:
        # A failed disconnect keeps the broker connected by design. Once the
        # operator reconnects (or the gateway restarts and re-establishes the
        # session), the stale "sockets may still be alive" verdict must not
        # outlive the link it described.
        with self._lock:
            self._clear_failed_disconnect_locked(self._result.broker_id)
            return self._result.to_dict()

    def rebind(self, service: Any, manager: Any | None) -> None:
        """Refresh injected references if the composition root recreated them."""
        self._service = service
        self._manager = manager

    def note_broker_connected(self, broker_id: str) -> None:
        """Clear a stale failed-disconnect verdict after a verified reconnect.

        Only failures whose reason is about the broker disconnect are cleared;
        an engine-stop failure or a BLOCKED exposure verdict stays visible until
        the operator resolves it. The reconnect itself is the proof that the old
        "broker sockets may still be alive" warning no longer describes the
        current session.
        """
        with self._lock:
            self._clear_failed_disconnect_locked(broker_id)

    def _clear_stale_disconnect_failure(self) -> None:
        with self._lock:
            self._clear_failed_disconnect_locked(self._result.broker_id)

    def _clear_failed_disconnect_locked(self, broker_id: str) -> None:
        current = self._result
        if current.state is not SafeDisconnectState.FAILED:
            return
        # Only the broker's own disconnect failure is superseded by a
        # reconnect. A crash-recovery verdict, an engine STOP failure, or an
        # unresolved-exposure BLOCKED still needs the operator.
        if not current.reason.lower().startswith("broker did not confirm disconnect"):
            return
        broker_id = str(broker_id or current.broker_id or "").strip().lower()
        if not broker_id or self._manager is None:
            return
        try:
            status = str(self._manager.state(broker_id).get("status", "")).upper()
        except Exception:  # noqa: BLE001 - an unreadable manager is not proof
            return
        if status not in ("CONNECTED", "LIVE_READY", "DEGRADED"):
            return
        self._result = SafeDisconnectResult(
            state=SafeDisconnectState.CONNECTED,
            broker_id=broker_id,
            reason="broker session re-established after a failed disconnect",
        )
        self._persist()

    # ── orchestration ─────────────────────────────────────────────

    def run(
        self,
        request_id: str,
        broker_id: str,
        *,
        acknowledge_exposure: bool = False,
        progress_cb: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        """Execute the safe-disconnect sequence. Never raises; always reports."""
        request_id = str(request_id or "").strip()
        broker_id = str(broker_id or "").strip().lower()
        with self._lock:
            cached = self._requests.get(request_id)
            if cached is not None:
                answer = SafeDisconnectResult(**{**cached.__dict__, "cached": True})
                return answer.to_dict()
            if self._running:
                return SafeDisconnectResult(
                    state=self._result.state,
                    request_id=request_id,
                    broker_id=broker_id,
                    reason="a disconnect sequence is already in progress — duplicate ignored",
                    engine_status=self._result.engine_status,
                ).to_dict()
            self._running = True
        try:
            return self._execute(request_id, broker_id, acknowledge_exposure, progress_cb)
        except Exception as exc:  # noqa: BLE001 - an unexpected fault fails closed
            logger.exception("safe_disconnect: unexpected failure request=%s", request_id)
            self._finish(
                SafeDisconnectState.FAILED,
                f"safe disconnect failed: {type(exc).__name__}: {exc}",
            )
            return self._result.to_dict()
        finally:
            with self._lock:
                self._running = False

    def _execute(
        self,
        request_id: str,
        broker_id: str,
        acknowledge_exposure: bool,
        progress_cb: Callable[[str, str], None] | None,
    ) -> dict[str, Any]:
        self._result = SafeDisconnectResult(
            state=SafeDisconnectState.STOP_REQUESTED,
            request_id=request_id,
            broker_id=broker_id,
            reason="STOP requested — new entries blocked first",
        )
        self._persist()

        def _progress(stage: str, detail: str) -> None:
            if progress_cb is None:
                return
            with contextlib.suppress(Exception):
                progress_cb(stage, detail)

        def _step(state: SafeDisconnectState, detail: str) -> None:
            self._result.state = state
            self._result.reason = detail
            self._result.steps.append({"state": state.value, "detail": detail})
            logger.info(
                "safe_disconnect step request=%s broker=%s state=%s detail=%s",
                request_id,
                broker_id,
                state.value,
                detail,
            )
            _progress(state.value.lower(), detail)
            self._persist()

        # 1/4 Block new entries immediately, before any teardown. Disarm is
        # idempotent and is deliberately repeated by stop(); this guarantees
        # the ordering even on an older service build.
        self._block_new_entries()
        _step(SafeDisconnectState.STOPPING, "new entries blocked — stopping engine")

        # 2/4 Controlled STOP, with explicit acknowledgement verification.
        status = self._engine_status()
        if status in ("RUNNING", "STARTING", "STOPPING"):
            stop = getattr(self._service, "stop", None)
            if not callable(stop):
                self._finish(SafeDisconnectState.FAILED, "live service has no stop()")
                return self._result.to_dict()
            with contextlib.suppress(Exception):
                stop(progress_cb=lambda stage, detail, *_: _progress(str(stage), str(detail)))
        status = self._engine_status()
        if status in ("RUNNING", "STARTING"):
            self._finish(
                SafeDisconnectState.FAILED,
                f"engine did not acknowledge STOP (status={status}) — broker kept connected",
            )
            return self._result.to_dict()
        self._result.engine_status = status
        _step(SafeDisconnectState.RECONCILING, "engine stopped — reconciling broker exposure")

        # 3/4 Authoritative broker exposure. Any read failure is UNKNOWN and
        # therefore BLOCKED: an unknown exposure is never treated as flat.
        exposure = self._broker_exposure(broker_id)
        self._result.exposure = exposure
        unknown = bool(exposure.get("unknown"))
        positions = int(exposure.get("positions", 0) or 0)
        orders = int(exposure.get("orders", 0) or 0)
        if unknown or positions or orders:
            reason = (
                f"exposure unresolved (positions={positions}, working_orders={orders}, "
                f"unknown={unknown}) — broker access kept for operator review; "
                "close/verify manually then re-run with acknowledgement"
            )
            if acknowledge_exposure and not unknown:
                reason = (
                    f"operator acknowledged exposure (positions={positions}, "
                    f"working_orders={orders}) — disconnecting with broker access retained "
                    "only until the venue link is released; nothing was liquidated"
                )
            else:
                self._finish(SafeDisconnectState.BLOCKED, reason)
                return self._result.to_dict()
        _step(
            SafeDisconnectState.SAFE_TO_DISCONNECT,
            "exposure resolved or explicitly acknowledged",
        )

        # 4/4 Disconnect and verify. No false DISCONNECTED: the manager's own
        # status is authoritative and already idempotent by request_id.
        _step(SafeDisconnectState.DISCONNECTING, "releasing broker session")
        if self._manager is None:
            self._finish(SafeDisconnectState.FAILED, "broker backend unavailable")
            return self._result.to_dict()
        ok, message = self._manager.disconnect_broker(broker_id, request_id=request_id)
        with contextlib.suppress(Exception):
            state = self._manager.state(broker_id)
            if str(state.get("status", "")).upper() == "DISCONNECTED":
                ok = True
        if not ok:
            self._finish(
                SafeDisconnectState.FAILED,
                f"broker did not confirm disconnect: {message} — verify in SYSTEM",
            )
            return self._result.to_dict()
        _step(SafeDisconnectState.DISCONNECTED, message or "disconnected by operator")
        with self._lock:
            if len(self._requests) >= 128:
                self._requests.pop(next(iter(self._requests)))
            self._requests[request_id] = SafeDisconnectResult(**self._result.__dict__)
        return self._result.to_dict()

    def _finish(self, state: SafeDisconnectState, reason: str) -> None:
        self._result.state = state
        self._result.reason = reason
        self._result.steps.append({"state": state.value, "detail": reason})
        logger.warning(
            "safe_disconnect finish request=%s broker=%s state=%s reason=%s",
            self._result.request_id,
            self._result.broker_id,
            state.value,
            reason,
        )
        self._persist()

    # ── injected dependencies (thin, read-only probes) ────────────

    def _engine_status(self) -> str:
        try:
            return str(getattr(self._service, "status", "") or "").upper()
        except Exception:  # noqa: BLE001
            return "UNKNOWN"

    def _block_new_entries(self) -> None:
        disarm = getattr(self._service, "disarm_live_execution", None)
        if callable(disarm):
            with contextlib.suppress(Exception):
                disarm("safe disconnect — new entries blocked")
            return
        with contextlib.suppress(Exception):
            sessions = getattr(self._service, "_sessions", {}) or {}
            for session in list(sessions.values()):
                with contextlib.suppress(Exception):
                    session.disarm("safe disconnect — new entries blocked")

    def _broker_exposure(self, broker_id: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "positions": 0,
            "orders": 0,
            "unknown": True,
            "source": "UNAVAILABLE",
        }
        if self._manager is None:
            out["reason"] = "broker manager unavailable"
            return out
        try:
            state = self._manager.state(broker_id)
            adapter = state.get("adapter")
        except Exception as exc:  # noqa: BLE001
            out["reason"] = f"broker state unreadable: {type(exc).__name__}"
            return out
        if adapter is None:
            out.update(unknown=False, source="NO_SESSION", reason="broker session not connected")
            return out
        if not (hasattr(adapter, "positions") and hasattr(adapter, "open_orders")):
            out["reason"] = "adapter has no exposure API — cannot prove flat"
            return out

        def _read() -> tuple[list[Any], list[Any]]:
            positions = adapter.positions() or []
            orders = adapter.open_orders() or []
            return list(positions), list(orders)

        # Bounded: a wedged venue must keep the broker connected (BLOCKED),
        # never hang the command worker or produce a false SAFE verdict.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="vayren-exposure") as pool:
            try:
                positions, orders = pool.submit(_read).result(timeout=self._read_timeout_s)
            except FutureTimeout:
                out["reason"] = "broker exposure read timed out"
                return out
            except Exception as exc:  # noqa: BLE001
                out["reason"] = f"broker exposure read failed: {type(exc).__name__}: {exc}"
                return out

        def _net_qty(row: Any) -> float:
            if isinstance(row, dict):
                for key in ("qty", "quantity", "netqty", "netQty"):
                    value = row.get(key)
                    if value is not None:
                        with contextlib.suppress(TypeError, ValueError):
                            return float(value)
            return 1.0

        net_positions = [row for row in positions if _net_qty(row) != 0]
        out.update(
            positions=len(net_positions),
            orders=len(orders),
            unknown=False,
            source="BROKER_AUTHORITATIVE",
        )
        return out
