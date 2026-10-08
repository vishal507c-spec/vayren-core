"""ControlPlaneBridge — connects the VAYREN UI to the EC2 Control Plane.

Maintains bidirectional communication between the desktop UI and the EC2 execution runtime:
- Dispatches Phase 3 commands: START, STOP, ARM, HALT, SELECT_STRATEGY, SET_RISK,
  SELECT_SYMBOLS, REQUEST_RECONCILIATION, REQUEST_SNAPSHOT, RESYNC_EVENTS.
- Ingests sequenced events with monotonic sequence tracking and deduplication.
- Detects market data staleness (> 15.0s) and notifies UI views.
- In-memory or remote WSS transport support with zero fake data.
- Emits PySide6 Qt signals for UI updates.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import QObject, Signal

from execution.control_plane import (
    CommandRequest,
    CommandResponse,
    CommandStatus,
    ControlCommand,
    ControlPlaneController,
    ControlPlaneEvent,
    DeduplicatingEventConsumer,
)

logger = logging.getLogger(__name__)

STALE_MARKET_THRESHOLD_SECONDS = 15.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ControlPlaneBridge(QObject):
    """Client-side bridge connecting UI dashboards to the EC2 Control Plane."""

    snapshot_updated = Signal(dict)
    event_received = Signal(dict)
    connection_changed = Signal(bool)
    staleness_changed = Signal(bool)
    command_response_received = Signal(dict)

    def __init__(
        self,
        controller: ControlPlaneController | None = None,
        auth_token: str = "default_token",
        user_id: str = "default_user",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._auth_token = auth_token
        self._user_id = user_id
        self._connected: bool = False
        self._is_stale: bool = False
        self._last_snapshot_dict: dict[str, Any] = {}
        self._event_consumer = DeduplicatingEventConsumer()
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=500)

        # Wire client tracker if controller provided
        if self._controller is not None:
            self._connected = True
            self._controller.client_tracker.on_client_connect()
            self.refresh_snapshot()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_stale(self) -> bool:
        return self._is_stale

    @property
    def latest_snapshot(self) -> dict[str, Any]:
        return dict(self._last_snapshot_dict)

    @property
    def recent_events(self) -> list[dict[str, Any]]:
        return list(self._recent_events)

    def set_controller(self, controller: ControlPlaneController) -> None:
        self._controller = controller
        self._connected = True
        self._controller.client_tracker.on_client_connect()
        self.connection_changed.emit(True)
        self.refresh_snapshot()

    def disconnect(self) -> None:
        self._connected = False
        if self._controller is not None:
            self._controller.client_tracker.on_client_disconnect()
        self.connection_changed.emit(False)

    def reconnect(self) -> None:
        if self._controller is not None:
            self._connected = True
            self._controller.client_tracker.on_client_connect()
            self.connection_changed.emit(True)
            self.refresh_snapshot()

    # ── Snapshot & Event Processing ──────────────────────────────────────────

    def refresh_snapshot(self) -> dict[str, Any]:
        """Fetch fresh authoritative snapshot from EC2 runtime."""
        if self._controller is None:
            return {}
        snapshot = self._controller.build_runtime_snapshot()
        return self.apply_snapshot(snapshot.to_dict())

    def apply_snapshot(self, snapshot_dict: dict[str, Any]) -> dict[str, Any]:
        """Apply snapshot dictionary and update staleness status."""
        self._last_snapshot_dict = dict(snapshot_dict)
        self._evaluate_staleness(snapshot_dict)
        self.snapshot_updated.emit(self._last_snapshot_dict)
        return self._last_snapshot_dict

    def ingest_event(self, event_dict: dict[str, Any]) -> bool:
        """Ingest streaming event frame with deduplication."""
        try:
            seq = int(event_dict.get("seq", 0))
            event_type = str(event_dict.get("event_type", ""))
            payload = event_dict.get("payload") or {}
            timestamp = str(event_dict.get("timestamp") or _now_iso())
            cp_event = ControlPlaneEvent(
                seq=seq,
                event_type=event_type,
                payload=payload,
                timestamp=timestamp,
            )
            if not self._event_consumer.accept(cp_event):
                return False  # Duplicate or out-of-order dropped
            self._recent_events.append(event_dict)
            self.event_received.emit(event_dict)

            # Auto-refresh snapshot on state-changing events
            if event_type in (
                "STATE_CHANGED",
                "ARM_CHANGED",
                "EMERGENCY_HALT",
                "CONFIG_CHANGED",
                "RISK_CONFIG_CHANGED",
                "SYMBOLS_CHANGED",
                "RECONCILIATION_COMPLETED",
            ):
                self.refresh_snapshot()
            return True
        except Exception as exc:
            logger.debug("bridge: failed to ingest event: %s", exc)
            return False

    def _evaluate_staleness(self, snapshot_dict: dict[str, Any]) -> None:
        safety = snapshot_dict.get("safety") or {}
        market_ws = str(snapshot_dict.get("market_ws") or safety.get("market_ws") or "")
        stale_flag = market_ws == "STALE"
        if stale_flag != self._is_stale:
            self._is_stale = stale_flag
            self.staleness_changed.emit(self._is_stale)

    # ── Command Dispatch Methods ─────────────────────────────────────────────

    def _dispatch(
        self, command: ControlCommand, payload: dict[str, Any] | None = None
    ) -> CommandResponse:
        req_id = f"cmd-{uuid.uuid4().hex[:8]}"
        req = CommandRequest(
            command=command,
            request_id=req_id,
            auth_token=self._auth_token,
            user_id=self._user_id,
            payload=payload or {},
            timestamp=_now_iso(),
        )
        if self._controller is None:
            resp = CommandResponse(
                request_id=req_id,
                command=command.value,
                status=CommandStatus.ERROR,
                error_code="NO_CONNECTION",
                reason="control plane not connected",
            )
        else:
            resp = self._controller.handle_command(req)

        self.command_response_received.emit(resp.to_dict())
        return resp

    def send_start(self) -> CommandResponse:
        return self._dispatch(ControlCommand.START)

    def send_stop(self) -> CommandResponse:
        return self._dispatch(ControlCommand.STOP)

    def send_arm(self) -> CommandResponse:
        return self._dispatch(ControlCommand.ARM)

    def send_halt(self, reason: str = "Operator halt") -> CommandResponse:
        return self._dispatch(ControlCommand.HALT, {"reason": reason})

    def send_select_strategy(
        self, strategy_name: str, parameters: dict[str, Any] | None = None
    ) -> CommandResponse:
        return self._dispatch(
            ControlCommand.SELECT_STRATEGY,
            {"strategy_name": strategy_name, "parameters": parameters or {}},
        )

    def send_set_risk(
        self,
        max_risk_pct: float,
        max_capital: float,
        max_drawdown_pct: float = 0.05,
    ) -> CommandResponse:
        return self._dispatch(
            ControlCommand.SET_RISK,
            {
                "max_risk_pct": max_risk_pct,
                "max_capital": max_capital,
                "max_drawdown_pct": max_drawdown_pct,
            },
        )

    def send_select_symbols(self, symbols: list[str]) -> CommandResponse:
        return self._dispatch(ControlCommand.SELECT_SYMBOLS, {"symbols": symbols})

    def send_request_reconciliation(self) -> CommandResponse:
        return self._dispatch(ControlCommand.REQUEST_RECONCILIATION)

    def send_request_snapshot(self) -> CommandResponse:
        resp = self._dispatch(ControlCommand.REQUEST_SNAPSHOT)
        if resp.status == CommandStatus.SUCCESS and resp.data:
            self.apply_snapshot(resp.data)
        return resp

    def send_resync_events(self, from_seq: int) -> CommandResponse:
        resp = self._dispatch(ControlCommand.RESYNC_EVENTS, {"from_seq": from_seq})
        if resp.status == CommandStatus.SUCCESS:
            events = resp.data.get("events") or []
            for ev in events:
                if isinstance(ev, dict):
                    self.ingest_event(ev)
        elif resp.error_code == "RESYNC_FULL_SNAPSHOT_REQUIRED":
            self.send_request_snapshot()
        return resp
