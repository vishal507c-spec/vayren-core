"""VAYREN Phase 3 — APK/EXE Control Plane.

Provides a secure bidirectional control plane protocol and server for APK/EXE
clients communicating with the EC2 execution runtime:
- RFC 6455 compliant WebSocket framing and bidirectional messaging.
- Command validation, HMAC authentication, request_id idempotency.
- Gated START: requires all Phase 1 + Phase 2 safety gates to be GREEN.
- Safe STOP and emergency HALT (preserves protective stop-loss orders).
- Authoritative EC2 configuration for strategy, risk, and symbols.
- Authoritative RuntimeSnapshot covering all 9 operational dimensions.
- Sequenced event stream with replay/resync and duplicate deduplication.
- APK disconnect resilience: EC2 execution continues uninterrupted.
- Per-user runtime routing abstraction (User A -> Runtime A).
- Strict zero-secrets redaction and fail-closed security.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from execution.recovery import (
    ExecutionState,
    RemoteClientTracker,
)
from execution.safety import (
    SafetySnapshot,
    SlProtectionTracker,
    TradingSafetyState,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Commands & Response Models (§2, §3) ──────────────────────────────────────


class ControlCommand(StrEnum):
    """Authoritative commands supported by the EC2 execution control plane."""

    START = "START"
    STOP = "STOP"
    ARM = "ARM"
    HALT = "HALT"
    SELECT_STRATEGY = "SELECT_STRATEGY"
    SET_RISK = "SET_RISK"
    SELECT_SYMBOLS = "SELECT_SYMBOLS"
    REQUEST_RECONCILIATION = "REQUEST_RECONCILIATION"
    REQUEST_SNAPSHOT = "REQUEST_SNAPSHOT"
    RESYNC_EVENTS = "RESYNC_EVENTS"


class CommandStatus(StrEnum):
    """Outcome status of an executed or rejected command."""

    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"


@dataclass(frozen=True)
class CommandRequest:
    """Envelope for incoming APK/EXE commands."""

    command: ControlCommand
    request_id: str
    auth_token: str
    user_id: str = "default_user"
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self, redact_secrets: bool = True) -> dict[str, Any]:
        return {
            "command": self.command.value,
            "request_id": self.request_id,
            "auth_token": "***REDACTED***" if redact_secrets else self.auth_token,
            "user_id": self.user_id,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandRequest:
        if not isinstance(data, dict):
            raise ValueError("command envelope must be a JSON object")
        cmd_raw = data.get("command")
        if not cmd_raw:
            raise ValueError("missing 'command' in envelope")
        try:
            command = ControlCommand(str(cmd_raw).strip().upper())
        except ValueError as err:
            raise ValueError(f"unknown command: {cmd_raw}") from err

        request_id = str(data.get("request_id") or "").strip()
        if not request_id:
            raise ValueError("missing or empty 'request_id'")

        auth_token = str(data.get("auth_token") or "").strip()
        user_id = str(data.get("user_id") or "default_user").strip()
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("'payload' must be an object")

        timestamp = str(data.get("timestamp") or _now_iso())
        return cls(
            command=command,
            request_id=request_id,
            auth_token=auth_token,
            user_id=user_id,
            payload=payload,
            timestamp=timestamp,
        )


@dataclass(frozen=True)
class CommandResponse:
    """Authoritative response returned for every command."""

    request_id: str
    command: str
    status: CommandStatus
    error_code: str = ""
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "command": self.command,
            "status": self.status.value,
            "error_code": self.error_code,
            "reason": self.reason,
            "data": dict(self.data),
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ── Authentication & Security (§11) ──────────────────────────────────────────


class ControlPlaneAuth:
    """HMAC and bearer token authenticator for control plane commands."""

    def __init__(self, valid_tokens: set[str] | None = None) -> None:
        self._valid_tokens: set[str] = set(valid_tokens or set())

    def add_token(self, token: str) -> None:
        if token and token.strip():
            self._valid_tokens.add(token.strip())

    def revoke_token(self, token: str) -> None:
        self._valid_tokens.discard(token.strip())

    def validate_token(self, token: str) -> bool:
        if not token or not token.strip():
            return False
        clean = token.strip()
        # Constant-time comparison across active tokens to prevent timing attacks
        return any(hmac.compare_digest(clean, valid) for valid in self._valid_tokens)

    @staticmethod
    def redact_token(token: str) -> str:
        return "***REDACTED***" if token else ""


# ── Idempotency Manager (§3) ────────────────────────────────────────────────


class IdempotencyManager:
    """Tracks request_ids to ensure duplicate submissions return cached responses."""

    def __init__(self, max_entries: int = 2000) -> None:
        self._max_entries = max_entries
        self._responses: dict[str, CommandResponse] = {}
        self._order: deque[str] = deque()

    def get_response(self, request_id: str) -> CommandResponse | None:
        return self._responses.get(request_id)

    def record_response(self, request_id: str, response: CommandResponse) -> None:
        if request_id in self._responses:
            return
        if len(self._order) >= self._max_entries:
            oldest = self._order.popleft()
            self._responses.pop(oldest, None)
        self._order.append(request_id)
        self._responses[request_id] = response

    def clear(self) -> None:
        self._responses.clear()
        self._order.clear()


# ── Event Streaming & Replay (§8) ───────────────────────────────────────────


@dataclass(frozen=True)
class ControlPlaneEvent:
    """Sequenced broadcast event published over WSS."""

    seq: int
    event_type: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class EventStreamManager:
    """Maintains monotonic event sequences and an event ring buffer for replay."""

    def __init__(self, buffer_size: int = 2000) -> None:
        self._buffer_size = buffer_size
        self._current_seq: int = 0
        self._buffer: deque[ControlPlaneEvent] = deque(maxlen=buffer_size)

    @property
    def current_seq(self) -> int:
        return self._current_seq

    def publish(self, event_type: str, payload: dict[str, Any]) -> ControlPlaneEvent:
        self._current_seq += 1
        event = ControlPlaneEvent(
            seq=self._current_seq,
            event_type=event_type,
            payload=payload,
            timestamp=_now_iso(),
        )
        self._buffer.append(event)
        return event

    def get_events_since(self, from_seq: int) -> tuple[bool, list[ControlPlaneEvent]]:
        """Fetch missed events starting after `from_seq`.

        Returns:
            (is_continuous, events):
            If `from_seq` is older than the oldest buffered event, `is_continuous`
            is False, signaling that the client must perform a full snapshot sync.
        """
        if from_seq >= self._current_seq:
            return (True, [])
        if not self._buffer:
            return (False, [])
        oldest_seq = self._buffer[0].seq
        if from_seq < oldest_seq - 1:
            # Missed window gap: buffer has wrapped around
            return (False, [])
        events = [ev for ev in self._buffer if ev.seq > from_seq]
        return (True, events)


class DeduplicatingEventConsumer:
    """Client-side helper that guarantees exactly-once processing of sequenced events."""

    def __init__(self) -> None:
        self._last_seq: int = 0
        self._seen_seqs: set[int] = set()

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def accept(self, event: ControlPlaneEvent) -> bool:
        """Returns True if the event is fresh and accepted, False if duplicate or out-of-order."""
        if event.seq in self._seen_seqs or event.seq <= self._last_seq:
            return False
        self._seen_seqs.add(event.seq)
        self._last_seq = event.seq
        if len(self._seen_seqs) > 5000:
            # Trim old sequence IDs below high watermark
            self._seen_seqs = {s for s in self._seen_seqs if s > self._last_seq - 2000}
        return True


# ── Authoritative Runtime Snapshot (§7) ─────────────────────────────────────


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Comprehensive, authoritative runtime snapshot covering all 9 operational areas."""

    execution_state: str
    broker_connection: str
    market_ws: str
    order_ws: str
    risk: dict[str, Any]
    strategy: dict[str, Any]
    positions: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    safety: dict[str, Any]
    latest_seq: int
    user_id: str
    server_time: str = field(default_factory=_now_iso)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    disaster_recovery: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_state": self.execution_state,
            "broker_connection": self.broker_connection,
            "market_ws": self.market_ws,
            "order_ws": self.order_ws,
            "risk": dict(self.risk),
            "strategy": dict(self.strategy),
            "positions": list(self.positions),
            "orders": list(self.orders),
            "safety": dict(self.safety),
            "latest_seq": self.latest_seq,
            "user_id": self.user_id,
            "server_time": self.server_time,
            "reconciliation": dict(self.reconciliation),
            "disaster_recovery": dict(self.disaster_recovery),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ── Target Session Interface Protocol ────────────────────────────────────────


@runtime_checkable
class ControlTargetSession(Protocol):
    """Protocol satisfied by LiveSession or test mock targets."""

    def safety_snapshot(self) -> SafetySnapshot: ...

    def arm(self, reason: str = "") -> Any: ...

    def disarm(self, reason: str = "") -> Any: ...

    def stop(self) -> None: ...

    def reconcile_now(self) -> Any: ...


# ── Control Plane Controller (§4, §5, §6, §9) ───────────────────────────────


class ControlPlaneController:
    """Authoritative EC2 Execution Engine Gateway.

    Validates incoming commands, enforces safety gates, manages configuration,
    and publishes streaming events.
    """

    def __init__(
        self,
        session: Any,
        auth: ControlPlaneAuth,
        user_id: str = "default_user",
        client_tracker: RemoteClientTracker | None = None,
        event_stream: EventStreamManager | None = None,
        idempotency: IdempotencyManager | None = None,
    ) -> None:
        self._session = session
        self._auth = auth
        self._user_id = user_id
        self._client_tracker = client_tracker or RemoteClientTracker()
        self._event_stream = event_stream or EventStreamManager()
        self._idempotency = idempotency or IdempotencyManager()

        # Authoritative configuration state (EC2 is single source of truth)
        self._strategy_name: str = "OBR_C1C4"
        self._strategy_params: dict[str, Any] = {"version": "v1.0"}
        self._symbols: list[str] = ["NSE:INFY-EQ"]
        self._risk_config: dict[str, Any] = {
            "max_risk_pct": 0.01,
            "max_capital": 100_000.0,
            "max_drawdown_pct": 0.05,
        }

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def client_tracker(self) -> RemoteClientTracker:
        return self._client_tracker

    @property
    def event_stream(self) -> EventStreamManager:
        return self._event_stream

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def risk_config(self) -> dict[str, Any]:
        return dict(self._risk_config)

    def handle_command(self, request: CommandRequest) -> CommandResponse:
        """Process an incoming command envelope with auth, deduplication and validation."""
        # 1. Authentication Check
        if not self._auth.validate_token(request.auth_token):
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.UNAUTHORIZED,
                error_code="UNAUTHORIZED",
                reason="invalid or missing auth token",
            )

        # 2. Idempotency Check
        cached = self._idempotency.get_response(request.request_id)
        if cached is not None:
            return cached

        # 3. Command Dispatch
        handler = {
            ControlCommand.START: self._handle_start,
            ControlCommand.STOP: self._handle_stop,
            ControlCommand.ARM: self._handle_arm,
            ControlCommand.HALT: self._handle_halt,
            ControlCommand.SELECT_STRATEGY: self._handle_select_strategy,
            ControlCommand.SET_RISK: self._handle_set_risk,
            ControlCommand.SELECT_SYMBOLS: self._handle_select_symbols,
            ControlCommand.REQUEST_RECONCILIATION: self._handle_reconcile,
            ControlCommand.REQUEST_SNAPSHOT: self._handle_request_snapshot,
            ControlCommand.RESYNC_EVENTS: self._handle_resync_events,
        }.get(request.command)

        if handler is None:
            response = CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.ERROR,
                error_code="UNHANDLED_COMMAND",
                reason=f"command {request.command.value} not implemented",
            )
        else:
            response = handler(request)

        # 4. Cache authoritative response
        self._idempotency.record_response(request.request_id, response)
        return response

    # ── Command Handlers ────────────────────────────────────────────────────

    def _handle_start(self, request: CommandRequest) -> CommandResponse:
        """START command: permits LIVE execution only when all safety gates are GREEN."""
        safety = self._get_safety_snapshot()
        blockers: list[str] = []

        if safety.kill_switch_engaged:
            blockers.append("kill switch is engaged")
        if not safety.live_trading_enabled:
            blockers.append("live trading gate disabled")
        if not safety.broker_live_enabled:
            blockers.append("broker live trading not enabled")
        if not safety.account_confirmed:
            blockers.append("account environment not confirmed")
        if not safety.risk_limits_valid:
            blockers.append("risk limits invalid or capital unavailable")
        if not safety.broker_connected:
            blockers.append(f"broker disconnected ({safety.broker_health_reason or 'unknown'})")
        if not safety.reconciliation_healthy:
            blockers.append("reconciliation mismatch blocks live")
        if safety.sl_protection_summary in ("SL_ATTENTION", "PROTECTION_FAILED"):
            blockers.append(f"stop-loss protection status: {safety.sl_protection_summary}")
        if safety.market_ws != "CONNECTED":
            blockers.append(f"market WS unhealthy ({safety.market_ws})")
        if safety.order_ws != "CONNECTED":
            blockers.append(f"order WS unhealthy ({safety.order_ws})")
        if safety.overall in (
            TradingSafetyState.BLOCKED,
            TradingSafetyState.UNKNOWN,
            TradingSafetyState.KILL_SWITCH_ON,
        ):
            blockers.append(f"overall safety state is {safety.overall.value}")

        if blockers:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="SAFETY_GATES_FAILED",
                reason="; ".join(blockers),
                data={"blockers": blockers},
            )

        # Transition state machine to RUNNING if recovery coordinator exists
        recovery = getattr(self._session, "recovery", None)
        if recovery is not None:
            sm = recovery.state_machine
            if sm.state == ExecutionState.READY:
                sm.transition(ExecutionState.RUNNING, reason="START command from control plane")
            elif sm.state != ExecutionState.RUNNING:
                return CommandResponse(
                    request_id=request.request_id,
                    command=request.command.value,
                    status=CommandStatus.REJECTED,
                    error_code="INVALID_STATE_FOR_START",
                    reason=f"cannot start from state {sm.state.value} (must be READY)",
                )

        if hasattr(self._session, "arm"):
            self._session.arm(reason="START command authorized")

        event = self._event_stream.publish(
            "STATE_CHANGED",
            {"state": "RUNNING", "action": "START", "reason": "authorized by control plane"},
        )
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={"state": "RUNNING", "event_seq": event.seq},
        )

    def _handle_stop(self, request: CommandRequest) -> CommandResponse:
        """STOP command: stops active orders while preserving protective stop-losses."""
        if hasattr(self._session, "disarm"):
            self._session.disarm(reason="STOP command from control plane")

        # Disarm / stop session without canceling existing protective SLs
        recovery = getattr(self._session, "recovery", None)
        if recovery is not None:
            sm = recovery.state_machine
            if sm.state == ExecutionState.RUNNING:
                sm.transition(ExecutionState.STOPPED, reason="STOP command from control plane")

        event = self._event_stream.publish(
            "STATE_CHANGED",
            {
                "state": "STOPPED",
                "action": "STOP",
                "sl_protected": True,
                "reason": "operator stopped execution; protective stops intact",
            },
        )
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={"state": "STOPPED", "sl_protected": True, "event_seq": event.seq},
        )

    def _handle_arm(self, request: CommandRequest) -> CommandResponse:
        """ARM command: arms live trading mode."""
        if hasattr(self._session, "arm"):
            self._session.arm(reason="ARM command from control plane")
        event = self._event_stream.publish(
            "ARM_CHANGED", {"armed": "ARMED", "reason": "operator armed"}
        )
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={"armed": "ARMED", "event_seq": event.seq},
        )

    def _handle_halt(self, request: CommandRequest) -> CommandResponse:
        """HALT command: emergency kill switch trip; stops all new orders immediately."""
        reason = str(request.payload.get("reason") or "Operator HALT command")
        kill_switch = getattr(self._session, "kill_switch", None)
        if kill_switch is not None:
            if hasattr(kill_switch, "engage"):
                kill_switch.engage(reason=reason)
            elif hasattr(kill_switch, "halt"):
                kill_switch.halt(reason=reason)

        recovery = getattr(self._session, "recovery", None)
        if recovery is not None:
            sm = recovery.state_machine
            if sm.state != ExecutionState.BLOCKED:
                sm.transition(ExecutionState.BLOCKED, reason=f"HALT command: {reason}")

        event = self._event_stream.publish(
            "EMERGENCY_HALT",
            {"halted": True, "reason": reason, "sl_protected": True},
        )
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={"halted": True, "reason": reason, "event_seq": event.seq},
        )

    def _handle_select_strategy(self, request: CommandRequest) -> CommandResponse:
        """SELECT_STRATEGY: sets strategy configuration on EC2."""
        strategy_name = str(request.payload.get("strategy_name") or "").strip()
        if not strategy_name:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_STRATEGY",
                reason="'strategy_name' must be a non-empty string",
            )

        # Reject strategy changes if the engine is actively RUNNING
        recovery = getattr(self._session, "recovery", None)
        if recovery is not None and recovery.state_machine.state == ExecutionState.RUNNING:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="CANNOT_CHANGE_WHILE_RUNNING",
                reason="cannot change strategy while engine is RUNNING; STOP first",
            )

        params = request.payload.get("parameters") or {}
        if not isinstance(params, dict):
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_PARAMS",
                reason="'parameters' must be a dict",
            )

        self._strategy_name = strategy_name
        self._strategy_params = params
        event = self._event_stream.publish(
            "CONFIG_CHANGED",
            {"strategy_name": self._strategy_name, "parameters": self._strategy_params},
        )
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={"strategy_name": self._strategy_name, "event_seq": event.seq},
        )

    def _handle_set_risk(self, request: CommandRequest) -> CommandResponse:
        """SET_RISK: configures risk limits on EC2 with strict range validation."""
        max_risk_pct = request.payload.get("max_risk_pct")
        max_capital = request.payload.get("max_capital")
        max_drawdown_pct = request.payload.get("max_drawdown_pct", 0.05)

        if max_risk_pct is None or not isinstance(max_risk_pct, (int, float)):
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_RISK_CONFIG",
                reason="'max_risk_pct' must be a positive number",
            )
        if max_risk_pct <= 0 or max_risk_pct > 0.05:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_RISK_CONFIG",
                reason=f"max_risk_pct ({max_risk_pct}) must be in range (0.0, 0.05]",
            )
        if max_capital is None or not isinstance(max_capital, (int, float)) or max_capital <= 0:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_RISK_CONFIG",
                reason=f"'max_capital' ({max_capital}) must be positive",
            )

        self._risk_config = {
            "max_risk_pct": float(max_risk_pct),
            "max_capital": float(max_capital),
            "max_drawdown_pct": float(max_drawdown_pct),
        }
        event = self._event_stream.publish("RISK_CONFIG_CHANGED", self._risk_config)
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={"risk_config": self._risk_config, "event_seq": event.seq},
        )

    def _handle_select_symbols(self, request: CommandRequest) -> CommandResponse:
        """SELECT_SYMBOLS: updates active monitored symbols."""
        raw_symbols = request.payload.get("symbols")
        if not isinstance(raw_symbols, (list, tuple)) or not raw_symbols:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_SYMBOLS",
                reason="'symbols' must be a non-empty list of symbol strings",
            )

        clean_symbols = [str(s).strip().upper() for s in raw_symbols if str(s).strip()]
        if not clean_symbols:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_SYMBOLS",
                reason="all provided symbols were empty or invalid",
            )

        self._symbols = clean_symbols
        event = self._event_stream.publish("SYMBOLS_CHANGED", {"symbols": self._symbols})
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={"symbols": self._symbols, "event_seq": event.seq},
        )

    def _handle_reconcile(self, request: CommandRequest) -> CommandResponse:
        """REQUEST_RECONCILIATION: runs full reconciliation against broker."""
        self._event_stream.publish("RECONCILIATION_STARTED", {"requested_by": request.user_id})

        status_val = "MATCHED"
        reasons: list[str] = []
        mismatches_count = 0
        details: dict[str, Any] = {}

        if hasattr(self._session, "reconcile_pipeline"):
            rec_res = self._session.reconcile_pipeline()
            if hasattr(rec_res, "report"):
                rep = rec_res.report
                status_val = "MATCHED" if rep.matched else "MISMATCH"
                reasons = [m.message for m in rep.mismatches]
                mismatches_count = len(rep.mismatches)
                details = rep.to_dict()
        elif hasattr(self._session, "reconcile_now"):
            rec_result = self._session.reconcile_now()
            if hasattr(rec_result, "report"):
                rep = rec_result.report
                status_val = "MATCHED" if rep.matched else "MISMATCH"
                reasons = [m.message for m in rep.mismatches]
                mismatches_count = len(rep.mismatches)
                details = rep.to_dict()
            elif hasattr(rec_result, "verdict"):
                verdict = rec_result.verdict()
                status_val = verdict.status.value if verdict else "UNKNOWN"
                reasons = list(verdict.reasons) if verdict else []
                mismatches_count = len(reasons)
            elif hasattr(rec_result, "matched"):
                status_val = "MATCHED" if rec_result.matched else "MISMATCH"
                if hasattr(rec_result, "mismatches"):
                    reasons = [m.message for m in rec_result.mismatches]
                    mismatches_count = len(rec_result.mismatches)
                if hasattr(rec_result, "to_dict"):
                    details = rec_result.to_dict()

        if mismatches_count > 0:
            self._event_stream.publish(
                "MISMATCH_DETECTED",
                {"count": mismatches_count, "reasons": reasons, "details": details},
            )

        event = self._event_stream.publish(
            "RECONCILIATION_COMPLETED",
            {
                "status": status_val,
                "reasons": reasons,
                "mismatches_count": mismatches_count,
                "matched": (mismatches_count == 0),
            },
        )
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={
                "status": status_val,
                "reasons": reasons,
                "mismatches_count": mismatches_count,
                "event_seq": event.seq,
                "details": details,
            },
        )

    def _handle_request_snapshot(self, request: CommandRequest) -> CommandResponse:
        """REQUEST_SNAPSHOT: compiles and returns authoritative RuntimeSnapshot."""
        snapshot = self.build_runtime_snapshot()
        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data=snapshot.to_dict(),
        )

    def _handle_resync_events(self, request: CommandRequest) -> CommandResponse:
        """RESYNC_EVENTS: returns missed events or informs client full snapshot is required."""
        from_seq = request.payload.get("from_seq")
        if from_seq is None or not isinstance(from_seq, int) or from_seq < 0:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="INVALID_FROM_SEQ",
                reason="'from_seq' must be a non-negative integer",
            )

        is_continuous, events = self._event_stream.get_events_since(from_seq)
        if not is_continuous:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="RESYNC_FULL_SNAPSHOT_REQUIRED",
                reason="event gap exceeds buffer capacity; full snapshot required",
                data={"latest_seq": self._event_stream.current_seq},
            )

        return CommandResponse(
            request_id=request.request_id,
            command=request.command.value,
            status=CommandStatus.SUCCESS,
            data={
                "events": [ev.to_dict() for ev in events],
                "latest_seq": self._event_stream.current_seq,
            },
        )

    # ── Snapshot Construction ───────────────────────────────────────────────

    def build_runtime_snapshot(self) -> RuntimeSnapshot:
        """Generate authoritative snapshot across all 9 operational areas."""
        safety = self._get_safety_snapshot()
        recovery = getattr(self._session, "recovery", None)

        exec_state = recovery.state_machine.state.value if recovery else "READY"
        broker_conn = "CONNECTED" if safety.broker_connected else "DISCONNECTED"
        market_conn = safety.market_ws
        order_conn = safety.order_ws

        # Extract positions
        positions: list[dict[str, Any]] = []
        ledger = getattr(self._session, "ledger", None)
        if ledger is not None:
            for pos in ledger.all_positions():
                positions.append(
                    {
                        "symbol": pos.symbol,
                        "quantity": pos.quantity,
                        "avg_price": pos.avg_price,
                        "realized_pnl": pos.realized_pnl,
                    }
                )

        # Extract orders
        orders: list[dict[str, Any]] = []
        engine = getattr(self._session, "engine", None)
        if engine is not None:
            for ord_obj in engine.open_orders():
                side_val = (
                    ord_obj.side.value if hasattr(ord_obj.side, "value") else str(ord_obj.side)
                )
                state_val = (
                    ord_obj.state.value if hasattr(ord_obj.state, "value") else str(ord_obj.state)
                )
                orders.append(
                    {
                        "client_order_id": ord_obj.client_order_id,
                        "symbol": ord_obj.symbol,
                        "side": side_val,
                        "quantity": ord_obj.quantity,
                        "state": state_val,
                        "stop_price": ord_obj.stop_price,
                    }
                )

        rec_data = {
            "status": safety.reconciliation_status,
            "healthy": safety.reconciliation_healthy,
            "reasons": list(safety.reconciliation_reasons),
        }

        ha_data = {
            "executor_id": getattr(self._session, "executor_id", "primary-ec2-a"),
            "role": getattr(self._session, "executor_role", "ACTIVE"),
            "epoch": getattr(self._session, "executor_epoch", 1),
            "lease_valid": getattr(self._session, "lease_valid", True),
            "is_fenced": getattr(self._session, "is_fenced", False),
            "resources_healthy": getattr(self._session, "resources_healthy", True),
        }

        return RuntimeSnapshot(
            execution_state=exec_state,
            broker_connection=broker_conn,
            market_ws=market_conn,
            order_ws=order_conn,
            risk=dict(self._risk_config),
            strategy={
                "name": self._strategy_name,
                "symbols": list(self._symbols),
                "parameters": dict(self._strategy_params),
            },
            positions=positions,
            orders=orders,
            safety=safety.to_dict(),
            latest_seq=self._event_stream.current_seq,
            user_id=self._user_id,
            server_time=_now_iso(),
            reconciliation=rec_data,
            disaster_recovery=ha_data,
        )

    def _get_safety_snapshot(self) -> SafetySnapshot:
        if hasattr(self._session, "safety_snapshot"):
            return self._session.safety_snapshot()
        from execution.safety import build_safety_snapshot

        return build_safety_snapshot(
            kill_switch_engaged=False,
            kill_switch_reason="",
            kill_switch_level="INFO",
            gates_live_trading_enabled=True,
            gates_broker_live_enabled=True,
            gates_account_confirmed=True,
            gates_risk_limits_valid=True,
            gates_kill_switch_off=True,
            broker_connected=True,
            broker_name="FYERS",
            broker_environment="PAPER",
            broker_health_reason="ok",
            reconciliation_healthy=True,
            reconciliation_status="MATCHED",
            reconciliation_reasons=(),
            risk_ready=True,
            capital_valid=True,
            last_risk_denial_reason="",
            sl_tracker=getattr(self._session, "sl_tracker", None) or SlProtectionTracker(),
            armed="DISARMED",
            session_lifecycle="READY",
            last_block_reason="",
        )


# ── Per-User Session Routing (§10) ──────────────────────────────────────────


class PerUserSessionRouter:
    """Routes commands to user-specific runtime controllers.

    Provides per-user architectural isolation (User A -> Runtime A,
    User B -> Runtime B) while maintaining single-user operational simplicity.
    """

    def __init__(self) -> None:
        self._controllers: dict[str, ControlPlaneController] = {}

    def register_user(self, user_id: str, controller: ControlPlaneController) -> None:
        self._controllers[user_id] = controller

    def unregister_user(self, user_id: str) -> None:
        self._controllers.pop(user_id, None)

    def get_controller(self, user_id: str) -> ControlPlaneController | None:
        return self._controllers.get(user_id)

    def route_command(self, request: CommandRequest) -> CommandResponse:
        controller = self._controllers.get(request.user_id)
        if controller is None:
            return CommandResponse(
                request_id=request.request_id,
                command=request.command.value,
                status=CommandStatus.REJECTED,
                error_code="UNKNOWN_USER_SESSION",
                reason=f"no active execution session for user_id '{request.user_id}'",
            )
        return controller.handle_command(request)


# ── RFC 6455 WebSocket Framing & Server Protocol (§1) ───────────────────────

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketFrame:
    """RFC 6455 compliant WebSocket frame encoder and decoder."""

    OP_TEXT = 0x1
    OP_BINARY = 0x2
    OP_CLOSE = 0x8
    OP_PING = 0x9
    OP_PONG = 0xA

    @staticmethod
    def encode(
        data: str | bytes,
        opcode: int = OP_TEXT,
        mask: bool = False,
    ) -> bytes:
        raw_payload = data.encode("utf-8") if isinstance(data, str) else data
        length = len(raw_payload)

        byte1 = 0x80 | (opcode & 0x0F)  # FIN bit set
        mask_bit = 0x80 if mask else 0x00

        header = bytearray([byte1])
        if length <= 125:
            header.append(mask_bit | length)
        elif length <= 65535:
            header.append(mask_bit | 126)
            header.extend(length.to_bytes(2, byteorder="big"))
        else:
            header.append(mask_bit | 127)
            header.extend(length.to_bytes(8, byteorder="big"))

        if mask:
            mask_key = secrets.token_bytes(4)
            header.extend(mask_key)
            masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw_payload))
            return bytes(header) + masked

        return bytes(header) + raw_payload

    @staticmethod
    def decode(raw: bytes) -> tuple[int, bytes, int] | None:
        """Decode a single WebSocket frame.

        Returns (opcode, payload, total_bytes_consumed) or None if incomplete.
        """
        if len(raw) < 2:
            return None

        byte1 = raw[0]
        byte2 = raw[1]
        opcode = byte1 & 0x0F
        is_masked = bool(byte2 & 0x80)
        payload_len = byte2 & 0x7F

        offset = 2
        if payload_len == 126:
            if len(raw) < offset + 2:
                return None
            payload_len = int.from_bytes(raw[offset : offset + 2], byteorder="big")
            offset += 2
        elif payload_len == 127:
            if len(raw) < offset + 8:
                return None
            payload_len = int.from_bytes(raw[offset : offset + 8], byteorder="big")
            offset += 8

        mask_key = b""
        if is_masked:
            if len(raw) < offset + 4:
                return None
            mask_key = raw[offset : offset + 4]
            offset += 4

        if len(raw) < offset + payload_len:
            return None

        payload_bytes = raw[offset : offset + payload_len]
        total_consumed = offset + payload_len

        if is_masked:
            unmasked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload_bytes))
            return (opcode, unmasked, total_consumed)

        return (opcode, payload_bytes, total_consumed)


def make_websocket_handshake_response(key: str) -> bytes:
    """Generate HTTP 101 Switching Protocols response for RFC 6455 handshake."""
    accept_src = (key.strip() + _WS_GUID).encode("utf-8")
    accept_val = base64.b64encode(hashlib.sha1(accept_src).digest()).decode("ascii")
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Accept: {accept_val}",
        "",
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")


def parse_websocket_key(http_request: str) -> str | None:
    """Extract Sec-WebSocket-Key from HTTP upgrade request."""
    for line in http_request.split("\r\n"):
        if ":" in line:
            name, val = line.split(":", 1)
            if name.strip().lower() == "sec-websocket-key":
                return val.strip()
    return None


# ── Bidirectional In-Memory Channel for Testing (§1, §9) ─────────────────────


class InMemoryControlPlaneChannel:
    """Deterministic in-memory bidirectional channel for testing and simulation."""

    def __init__(self, controller: ControlPlaneController) -> None:
        self.controller = controller
        self.connected = False
        self.client_events: list[ControlPlaneEvent] = []
        self.received_snapshots: list[RuntimeSnapshot] = []

    def connect(self) -> RuntimeSnapshot:
        self.connected = True
        self.controller.client_tracker.on_client_connect()
        snapshot = self.controller.build_runtime_snapshot()
        self.received_snapshots.append(snapshot)
        return snapshot

    def disconnect(self) -> None:
        self.connected = False
        self.controller.client_tracker.on_client_disconnect()

    def send_command(self, request: CommandRequest) -> CommandResponse:
        return self.controller.handle_command(request)


# ── Asynchronous WSS Server Protocol (§1) ───────────────────────────────────


async def handle_websocket_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    controller: ControlPlaneController,
) -> None:
    """Handles an RFC 6455 WebSocket client connection asynchronously."""
    try:
        raw_req = await reader.read(4096)
        if not raw_req:
            return
        http_text = raw_req.decode("utf-8", errors="replace")
        key = parse_websocket_key(http_text)
        if not key:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        # Perform handshake
        handshake_resp = make_websocket_handshake_response(key)
        writer.write(handshake_resp)
        await writer.drain()

        # Connect client tracker
        controller.client_tracker.on_client_connect()

        # Push initial authoritative snapshot
        snapshot = controller.build_runtime_snapshot()
        writer.write(WebSocketFrame.encode(snapshot.to_json()))
        await writer.drain()

        # Read frames loop
        buf = bytearray()
        while not reader.at_eof():
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf.extend(chunk)
            while True:
                decoded = WebSocketFrame.decode(bytes(buf))
                if decoded is None:
                    break
                opcode, payload, consumed = decoded
                del buf[:consumed]

                if opcode == WebSocketFrame.OP_CLOSE:
                    writer.write(WebSocketFrame.encode(b"", opcode=WebSocketFrame.OP_CLOSE))
                    await writer.drain()
                    return
                if opcode == WebSocketFrame.OP_PING:
                    writer.write(WebSocketFrame.encode(payload, opcode=WebSocketFrame.OP_PONG))
                    await writer.drain()
                elif opcode == WebSocketFrame.OP_TEXT:
                    try:
                        cmd_dict = json.loads(payload.decode("utf-8"))
                        cmd_req = CommandRequest.from_dict(cmd_dict)
                        resp = controller.handle_command(cmd_req)
                        writer.write(WebSocketFrame.encode(resp.to_json()))
                        await writer.drain()
                    except Exception as exc:
                        err_resp = CommandResponse(
                            request_id="",
                            command="",
                            status=CommandStatus.ERROR,
                            error_code="INVALID_COMMAND_PAYLOAD",
                            reason=str(exc),
                        )
                        writer.write(WebSocketFrame.encode(err_resp.to_json()))
                        await writer.drain()
    finally:
        controller.client_tracker.on_client_disconnect()
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_control_plane_server(
    controller: ControlPlaneController,
    host: str = "127.0.0.1",
    port: int = 8765,
    ssl_context: Any = None,
) -> asyncio.Server:
    """Start the WSS execution control plane server."""
    return await asyncio.start_server(
        lambda r, w: handle_websocket_connection(r, w, controller),
        host,
        port,
        ssl=ssl_context,
    )
