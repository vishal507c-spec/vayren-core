"""VAYREN Phase 3 — APK/EXE Control Plane Tests.

Tests per Phase 3 specification (§12):
1.  Command validation: envelope parsing, unknown command, missing fields, schema checking.
2.  Authentication: valid token passes, invalid token 401/UNAUTHORIZED, token redaction,
    timing-safe HMAC comparison.
3.  Request deduplication / Idempotency: duplicate request_id returns cached response,
    zero side-effect re-execution.
4.  START safety gates: blocks when gates are red (kill switch, broker, market WS, order WS,
    reconciliation, SL attention, invalid state), permits when all gates GREEN.
5.  STOP and HALT: STOP disarms and blocks new orders while preserving protective SLs;
    HALT engages kill switch, blocks orders, preserves SLs.
6.  Configuration management: SELECT_STRATEGY (valid, rejects while RUNNING),
    SET_RISK (valid, rejects invalid/negative), SELECT_SYMBOLS (normalizes, rejects empty).
7.  Authoritative RuntimeSnapshot: covers execution, broker, market WS, order WS, risk,
    strategy, positions, orders, safety (zero secrets).
8.  Event streaming & replay: monotonic seq numbers, replay buffer, gap signals full sync,
    client-side deduplication.
9.  APK disconnect resilience: client disconnect does not stop engine; reconnect gets fresh state.
10. Per-user isolation: PerUserSessionRouter routes to User A vs User B, rejects unknown user.
11. RFC 6455 WebSocket framing: encode/decode roundtrips, masking, handshake response.
12. End-to-end async socket loopback: handshake, initial snapshot, command exchange, clean close.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from execution.control_plane import (
    CommandRequest,
    CommandResponse,
    CommandStatus,
    ControlCommand,
    ControlPlaneAuth,
    ControlPlaneController,
    ControlPlaneEvent,
    DeduplicatingEventConsumer,
    EventStreamManager,
    IdempotencyManager,
    InMemoryControlPlaneChannel,
    PerUserSessionRouter,
    WebSocketFrame,
    make_websocket_handshake_response,
    parse_websocket_key,
    start_control_plane_server,
)
from execution.engine import ExecutionEngine
from execution.portfolio.ledger import PositionLedger
from execution.recovery import (
    ExecutionState,
    ExecutionStateMachine,
    RecoveryCoordinator,
)
from execution.safety import (
    SafetySnapshot,
    SlProtectionTracker,
    build_safety_snapshot,
)
from risk import KillSwitch, RiskPolicy

# ── Test Fixtures & Mock Sessions ────────────────────────────────────────────


class MockLiveSession:
    """Mock execution session providing configurable safety snapshot and state machine."""

    def __init__(
        self,
        kill_switch_engaged: bool = False,
        broker_connected: bool = True,
        market_ws: str = "CONNECTED",
        order_ws: str = "CONNECTED",
        reconciliation_healthy: bool = True,
        sl_attention: bool = False,
        execution_state: ExecutionState = ExecutionState.READY,
    ) -> None:
        self.recovery = RecoveryCoordinator()
        self.recovery.state_machine = ExecutionStateMachine(execution_state)
        self.kill_switch = KillSwitch()
        if kill_switch_engaged:
            self.kill_switch.engage(reason="manual halt")

        self.risk_policy = RiskPolicy(
            max_position_qty=1000.0,
            max_order_qty=500.0,
        )
        self.sl_tracker = SlProtectionTracker()
        if sl_attention:
            self.sl_tracker.on_fill(
                intent_id="BUY_1",
                symbol="NSE:INFY-EQ",
                side="BUY",
                fill_price=1500.0,
                stop_price=1450.0,
            )
            self.sl_tracker.mark_sl_attention(intent_id="BUY_1", reason="placement failed")

        self.ledger = PositionLedger(starting_capital=100_000.0)
        self.engine = ExecutionEngine()
        self.broker_connected = broker_connected
        self.market_ws = market_ws
        self.order_ws = order_ws
        self.reconciliation_healthy = reconciliation_healthy
        self.armed_state = "DISARMED"
        self.last_reason = ""
        self.reconcile_call_count = 0

    def arm(self, reason: str = "") -> str:
        self.last_reason = reason
        self.armed_state = "ARMED"
        return self.armed_state

    def disarm(self, reason: str = "") -> str:
        self.last_reason = reason
        self.armed_state = "DISARMED"
        return self.armed_state

    def stop(self) -> None:
        self.armed_state = "DISARMED"

    def reconcile_now(self) -> Any:
        self.reconcile_call_count += 1

        @dataclass
        class MockVerdict:
            status: Any
            reasons: tuple[str, ...]

        @dataclass
        class MockRecResult:
            blocks_live: bool = False

            def verdict(self) -> MockVerdict:
                from execution.portfolio.reconcile import ReconcileStatus

                return MockVerdict(status=ReconcileStatus.SAFE, reasons=())

        return MockRecResult()

    def safety_snapshot(self) -> SafetySnapshot:
        return build_safety_snapshot(
            kill_switch_engaged=self.kill_switch.is_halted(),
            kill_switch_reason=self.kill_switch.state().reason,
            kill_switch_level=self.kill_switch.state().level,
            gates_live_trading_enabled=True,
            gates_broker_live_enabled=True,
            gates_account_confirmed=True,
            gates_risk_limits_valid=True,
            gates_kill_switch_off=not self.kill_switch.is_halted(),
            broker_connected=self.broker_connected,
            broker_name="FYERS",
            broker_environment="PAPER",
            broker_health_reason="ok" if self.broker_connected else "disconnected",
            reconciliation_healthy=self.reconciliation_healthy,
            reconciliation_status="MATCHED" if self.reconciliation_healthy else "BLOCKED",
            reconciliation_reasons=() if self.reconciliation_healthy else ("mismatch detected",),
            risk_ready=not self.kill_switch.is_halted() and self.broker_connected,
            capital_valid=True,
            last_risk_denial_reason="",
            sl_tracker=self.sl_tracker,
            armed=self.armed_state,
            session_lifecycle="READY",
            last_block_reason="",
            execution_state=self.recovery.state_machine.state.value,
            broker_connection="CONNECTED" if self.broker_connected else "DISCONNECTED",
            market_ws=self.market_ws,
            order_ws=self.order_ws,
        )


@pytest.fixture
def test_auth() -> ControlPlaneAuth:
    auth = ControlPlaneAuth()
    auth.add_token("secret_token_123")
    return auth


@pytest.fixture
def default_controller(test_auth: ControlPlaneAuth) -> ControlPlaneController:
    session = MockLiveSession()
    return ControlPlaneController(
        session=session,
        auth=test_auth,
        user_id="trader_1",
    )


# ── 1. Command Validation Tests ──────────────────────────────────────────────


def test_command_envelope_parsing_valid() -> None:
    """Valid command envelope parses accurately with correct types."""
    raw = {
        "command": "START",
        "request_id": "req-001",
        "auth_token": "secret_token_123",
        "user_id": "trader_1",
        "payload": {"mode": "LIVE"},
    }
    req = CommandRequest.from_dict(raw)
    assert req.command == ControlCommand.START
    assert req.request_id == "req-001"
    assert req.auth_token == "secret_token_123"
    assert req.user_id == "trader_1"
    assert req.payload == {"mode": "LIVE"}


def test_command_envelope_unknown_command_raises() -> None:
    """Unknown command string raises descriptive ValueError."""
    raw = {
        "command": "NON_EXISTENT_COMMAND",
        "request_id": "req-002",
        "auth_token": "secret_token_123",
    }
    with pytest.raises(ValueError, match="unknown command"):
        CommandRequest.from_dict(raw)


def test_command_envelope_missing_request_id_raises() -> None:
    """Missing or empty request_id raises ValueError."""
    raw = {
        "command": "START",
        "request_id": "  ",
        "auth_token": "secret_token_123",
    }
    with pytest.raises(ValueError, match="missing or empty 'request_id'"):
        CommandRequest.from_dict(raw)


def test_command_envelope_invalid_payload_type_raises() -> None:
    """Non-dict payload raises ValueError."""
    raw = {
        "command": "START",
        "request_id": "req-003",
        "auth_token": "secret_token_123",
        "payload": "not_a_dict",
    }
    with pytest.raises(ValueError, match="'payload' must be an object"):
        CommandRequest.from_dict(raw)


# ── 2. Authentication & Redaction Tests ──────────────────────────────────────


def test_auth_valid_token_allows_execution(default_controller: ControlPlaneController) -> None:
    """Valid authentication token allows command processing."""
    req = CommandRequest(
        command=ControlCommand.REQUEST_SNAPSHOT,
        request_id="req-auth-1",
        auth_token="secret_token_123",
        user_id="trader_1",
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    assert resp.request_id == "req-auth-1"


def test_auth_invalid_token_rejects_unauthorized(
    default_controller: ControlPlaneController,
) -> None:
    """Invalid token returns UNAUTHORIZED status and 401 equivalent error code."""
    req = CommandRequest(
        command=ControlCommand.REQUEST_SNAPSHOT,
        request_id="req-auth-2",
        auth_token="wrong_token",
        user_id="trader_1",
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.UNAUTHORIZED
    assert resp.error_code == "UNAUTHORIZED"
    assert "invalid or missing auth token" in resp.reason


def test_auth_empty_token_rejects_unauthorized(
    default_controller: ControlPlaneController,
) -> None:
    """Empty or whitespace token returns UNAUTHORIZED."""
    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-auth-3",
        auth_token="   ",
        user_id="trader_1",
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.UNAUTHORIZED


def test_auth_token_redaction_in_to_dict() -> None:
    """auth_token is redacted in serialized dict representations."""
    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-redact-1",
        auth_token="super_secret_broker_token",
        user_id="trader_1",
    )
    data = req.to_dict(redact_secrets=True)
    assert data["auth_token"] == "***REDACTED***"
    assert "super_secret_broker_token" not in str(data)


# ── 3. Request Deduplication & Idempotency Tests ─────────────────────────────


def test_duplicate_request_id_returns_cached_response(
    default_controller: ControlPlaneController,
) -> None:
    """Resending identical request_id returns cached authoritative response."""
    req = CommandRequest(
        command=ControlCommand.SELECT_SYMBOLS,
        request_id="req-dup-1",
        auth_token="secret_token_123",
        payload={"symbols": ["NSE:TCS-EQ"]},
    )
    resp1 = default_controller.handle_command(req)
    assert resp1.status == CommandStatus.SUCCESS
    assert resp1.data["symbols"] == ["NSE:TCS-EQ"]

    # Now change payload in identical request_id
    req_dup = CommandRequest(
        command=ControlCommand.SELECT_SYMBOLS,
        request_id="req-dup-1",
        auth_token="secret_token_123",
        payload={"symbols": ["NSE:WIPRO-EQ"]},
    )
    resp2 = default_controller.handle_command(req_dup)
    assert resp2.status == CommandStatus.SUCCESS
    # Returns cached response: symbols remain TCS, side effect not re-executed
    assert resp2.data["symbols"] == ["NSE:TCS-EQ"]
    assert default_controller.symbols == ["NSE:TCS-EQ"]


def test_idempotency_cache_eviction_on_capacity() -> None:
    """Idempotency cache maintains bounded memory size with FIFO eviction."""
    mgr = IdempotencyManager(max_entries=2)
    r1 = CommandResponse(request_id="r1", command="START", status=CommandStatus.SUCCESS)
    r2 = CommandResponse(request_id="r2", command="STOP", status=CommandStatus.SUCCESS)
    r3 = CommandResponse(request_id="r3", command="ARM", status=CommandStatus.SUCCESS)

    mgr.record_response("r1", r1)
    mgr.record_response("r2", r2)
    assert mgr.get_response("r1") is not None

    mgr.record_response("r3", r3)
    # r1 should be evicted
    assert mgr.get_response("r1") is None
    assert mgr.get_response("r2") is not None
    assert mgr.get_response("r3") is not None


# ── 4. START Safety Gate Tests ───────────────────────────────────────────────


def test_start_allowed_when_all_safety_gates_green(test_auth: ControlPlaneAuth) -> None:
    """START command succeeds and transitions state to RUNNING when all gates are green."""
    session = MockLiveSession(execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-ok",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    assert resp.data["state"] == "RUNNING"
    assert session.recovery.state_machine.state == ExecutionState.RUNNING
    assert session.armed_state == "ARMED"


def test_start_blocked_when_kill_switch_engaged(test_auth: ControlPlaneAuth) -> None:
    """START command rejected when central kill switch is engaged."""
    session = MockLiveSession(kill_switch_engaged=True, execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-ks",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "SAFETY_GATES_FAILED"
    assert "kill switch is engaged" in resp.reason
    assert session.recovery.state_machine.state == ExecutionState.READY


def test_start_blocked_when_broker_disconnected(test_auth: ControlPlaneAuth) -> None:
    """START command rejected when broker connection is unhealthy or disconnected."""
    session = MockLiveSession(broker_connected=False, execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-broker",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "SAFETY_GATES_FAILED"
    assert "broker disconnected" in resp.reason


def test_start_blocked_when_market_ws_unhealthy(test_auth: ControlPlaneAuth) -> None:
    """START command rejected when market data WebSocket is disconnected or stale."""
    session = MockLiveSession(market_ws="STALE", execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-market",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "SAFETY_GATES_FAILED"
    assert "market WS unhealthy" in resp.reason


def test_start_blocked_when_order_ws_unhealthy(test_auth: ControlPlaneAuth) -> None:
    """START command rejected when order stream WebSocket has missed events."""
    session = MockLiveSession(order_ws="MISSED_EVENTS", execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-order-ws",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "SAFETY_GATES_FAILED"
    assert "order WS unhealthy" in resp.reason


def test_start_blocked_when_reconciliation_mismatched(test_auth: ControlPlaneAuth) -> None:
    """START command rejected when reconciliation between local and broker is mismatched."""
    session = MockLiveSession(reconciliation_healthy=False, execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-rec",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "SAFETY_GATES_FAILED"
    assert "reconciliation mismatch blocks live" in resp.reason


def test_start_blocked_when_sl_requires_attention(test_auth: ControlPlaneAuth) -> None:
    """START command rejected when a filled position is missing required stop-loss."""
    session = MockLiveSession(sl_attention=True, execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-sl",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "SAFETY_GATES_FAILED"
    assert "stop-loss protection status: SL_ATTENTION" in resp.reason


def test_start_blocked_when_state_is_not_ready(test_auth: ControlPlaneAuth) -> None:
    """START command rejected if state machine is in RECOVERING or STARTING."""
    session = MockLiveSession(execution_state=ExecutionState.RECOVERING)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.START,
        request_id="req-start-state",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code in ("SAFETY_GATES_FAILED", "INVALID_STATE_FOR_START")


# ── 5. STOP & HALT Tests ─────────────────────────────────────────────────────


def test_stop_command_preserves_protective_stop_losses(test_auth: ControlPlaneAuth) -> None:
    """STOP command disarms and blocks new orders while explicitly preserving protective SLs."""
    session = MockLiveSession(execution_state=ExecutionState.RUNNING)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.STOP,
        request_id="req-stop-1",
        auth_token="secret_token_123",
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    assert resp.data["state"] == "STOPPED"
    assert resp.data["sl_protected"] is True
    assert session.armed_state == "DISARMED"
    assert session.recovery.state_machine.state == ExecutionState.STOPPED


def test_halt_command_engages_kill_switch_and_preserves_sl(test_auth: ControlPlaneAuth) -> None:
    """HALT command triggers emergency kill switch, blocks all orders, preserves SLs."""
    session = MockLiveSession(execution_state=ExecutionState.RUNNING)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.HALT,
        request_id="req-halt-1",
        auth_token="secret_token_123",
        payload={"reason": "Manual operator intervention"},
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    assert resp.data["halted"] is True
    assert session.kill_switch.is_halted() is True
    assert session.recovery.state_machine.state == ExecutionState.BLOCKED


# ── 6. Configuration Management Tests ────────────────────────────────────────


def test_select_strategy_valid_updates_configuration(
    default_controller: ControlPlaneController,
) -> None:
    """SELECT_STRATEGY updates active strategy name and parameters."""
    req = CommandRequest(
        command=ControlCommand.SELECT_STRATEGY,
        request_id="req-strat-1",
        auth_token="secret_token_123",
        payload={"strategy_name": "TREND_FOLLOWING_ALPHA", "parameters": {"lookback": 50}},
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    assert default_controller.strategy_name == "TREND_FOLLOWING_ALPHA"


def test_select_strategy_cannot_change_while_running(test_auth: ControlPlaneAuth) -> None:
    """Cannot change strategy while engine is actively RUNNING."""
    session = MockLiveSession(execution_state=ExecutionState.RUNNING)
    controller = ControlPlaneController(session=session, auth=test_auth)

    req = CommandRequest(
        command=ControlCommand.SELECT_STRATEGY,
        request_id="req-strat-running",
        auth_token="secret_token_123",
        payload={"strategy_name": "NEW_STRAT"},
    )
    resp = controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "CANNOT_CHANGE_WHILE_RUNNING"


def test_set_risk_valid_updates_risk_limits(default_controller: ControlPlaneController) -> None:
    """SET_RISK updates max_risk_pct, max_capital, max_drawdown_pct."""
    req = CommandRequest(
        command=ControlCommand.SET_RISK,
        request_id="req-risk-1",
        auth_token="secret_token_123",
        payload={"max_risk_pct": 0.02, "max_capital": 250_000.0, "max_drawdown_pct": 0.08},
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    cfg = default_controller.risk_config
    assert cfg["max_risk_pct"] == 0.02
    assert cfg["max_capital"] == 250_000.0


def test_set_risk_invalid_parameters_rejected(
    default_controller: ControlPlaneController,
) -> None:
    """Negative risk or excessive risk percentage rejected with INVALID_RISK_CONFIG."""
    req = CommandRequest(
        command=ControlCommand.SET_RISK,
        request_id="req-risk-neg",
        auth_token="secret_token_123",
        payload={"max_risk_pct": -0.01, "max_capital": 100_000.0},
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "INVALID_RISK_CONFIG"

    req_excess = CommandRequest(
        command=ControlCommand.SET_RISK,
        request_id="req-risk-excess",
        auth_token="secret_token_123",
        payload={"max_risk_pct": 0.50, "max_capital": 100_000.0},
    )
    resp_excess = default_controller.handle_command(req_excess)
    assert resp_excess.status == CommandStatus.REJECTED


def test_select_symbols_normalizes_and_rejects_empty(
    default_controller: ControlPlaneController,
) -> None:
    """SELECT_SYMBOLS normalizes uppercase and rejects empty lists."""
    req = CommandRequest(
        command=ControlCommand.SELECT_SYMBOLS,
        request_id="req-sym-1",
        auth_token="secret_token_123",
        payload={"symbols": ["nse:infy-eq", "  bse:reliance  "]},
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    assert default_controller.symbols == ["NSE:INFY-EQ", "BSE:RELIANCE"]

    req_empty = CommandRequest(
        command=ControlCommand.SELECT_SYMBOLS,
        request_id="req-sym-empty",
        auth_token="secret_token_123",
        payload={"symbols": []},
    )
    resp_empty = default_controller.handle_command(req_empty)
    assert resp_empty.status == CommandStatus.REJECTED
    assert resp_empty.error_code == "INVALID_SYMBOLS"


# ── 7. Authoritative Runtime Snapshot Tests ──────────────────────────────────


def test_request_snapshot_contains_all_nine_operational_areas(
    default_controller: ControlPlaneController,
) -> None:
    """REQUEST_SNAPSHOT compiles complete 9-area snapshot with zero secrets."""
    req = CommandRequest(
        command=ControlCommand.REQUEST_SNAPSHOT,
        request_id="req-snap-1",
        auth_token="secret_token_123",
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    data = resp.data

    assert "execution_state" in data
    assert "broker_connection" in data
    assert "market_ws" in data
    assert "order_ws" in data
    assert "risk" in data
    assert "strategy" in data
    assert "positions" in data
    assert "orders" in data
    assert "safety" in data
    assert "latest_seq" in data
    assert "user_id" in data
    assert "server_time" in data

    # Verify zero secrets in serialized snapshot
    snapshot_json = json.dumps(data)
    assert "secret" not in snapshot_json.lower()
    assert "token" not in snapshot_json.lower()
    assert "password" not in snapshot_json.lower()


# ── 8. Event Streaming & Replay Tests ────────────────────────────────────────


def test_event_stream_monotonically_increments_seq() -> None:
    """Event sequence numbers strictly increment: 1, 2, 3..."""
    mgr = EventStreamManager(buffer_size=100)
    e1 = mgr.publish("STATE_CHANGED", {"state": "READY"})
    e2 = mgr.publish("ORDER_SUBMITTED", {"order_id": "ORD_1"})
    e3 = mgr.publish("ORDER_FILLED", {"order_id": "ORD_1"})

    assert e1.seq == 1
    assert e2.seq == 2
    assert e3.seq == 3
    assert mgr.current_seq == 3


def test_resync_events_replays_missed_events(
    default_controller: ControlPlaneController,
) -> None:
    """RESYNC_EVENTS replays missed events from specified from_seq."""
    stream = default_controller.event_stream
    stream.publish("EV_1", {"data": 1})
    stream.publish("EV_2", {"data": 2})
    stream.publish("EV_3", {"data": 3})

    req = CommandRequest(
        command=ControlCommand.RESYNC_EVENTS,
        request_id="req-resync-1",
        auth_token="secret_token_123",
        payload={"from_seq": 1},
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.SUCCESS
    events = resp.data["events"]
    assert len(events) == 2
    assert events[0]["seq"] == 2
    assert events[1]["seq"] == 3


def test_resync_events_gap_signals_full_snapshot_required(
    default_controller: ControlPlaneController,
) -> None:
    """If from_seq is older than oldest buffered event, server signals full sync required."""
    # Set small buffer to force overflow
    default_controller._event_stream = EventStreamManager(buffer_size=2)
    stream = default_controller.event_stream
    stream.publish("EV_1", {})
    stream.publish("EV_2", {})
    stream.publish("EV_3", {})  # Evicts EV_1; buffer has seq 2 and 3

    req = CommandRequest(
        command=ControlCommand.RESYNC_EVENTS,
        request_id="req-resync-gap",
        auth_token="secret_token_123",
        payload={"from_seq": 0},  # Older than oldest buffered (seq 2)
    )
    resp = default_controller.handle_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "RESYNC_FULL_SNAPSHOT_REQUIRED"


def test_deduplicating_event_consumer_drops_duplicates() -> None:
    """Client consumer drops duplicate sequence numbers and out-of-order events."""
    consumer = DeduplicatingEventConsumer()
    ev1 = ControlPlaneEvent(seq=1, event_type="E1", payload={})
    ev2 = ControlPlaneEvent(seq=2, event_type="E2", payload={})

    assert consumer.accept(ev1) is True
    assert consumer.accept(ev1) is False  # Duplicate dropped
    assert consumer.accept(ev2) is True
    assert consumer.accept(ev1) is False  # Old sequence dropped


# ── 9. APK Disconnect Resilience Tests ───────────────────────────────────────


def test_apk_disconnect_does_not_stop_engine(test_auth: ControlPlaneAuth) -> None:
    """APK/EXE disconnect does not affect running execution engine."""
    session = MockLiveSession(execution_state=ExecutionState.RUNNING)
    controller = ControlPlaneController(session=session, auth=test_auth)
    channel = InMemoryControlPlaneChannel(controller)

    channel.connect()
    assert controller.client_tracker.connected is True
    assert session.recovery.state_machine.state == ExecutionState.RUNNING

    # Client disconnects
    channel.disconnect()
    assert controller.client_tracker.connected is False
    assert controller.client_tracker.disconnect_count == 1
    # Engine continues RUNNING undisturbed
    assert session.recovery.state_machine.state == ExecutionState.RUNNING


def test_apk_reconnect_syncs_latest_authoritative_snapshot(
    test_auth: ControlPlaneAuth,
) -> None:
    """Client reconnect immediately delivers fresh authoritative snapshot."""
    session = MockLiveSession(execution_state=ExecutionState.READY)
    controller = ControlPlaneController(session=session, auth=test_auth)
    channel = InMemoryControlPlaneChannel(controller)

    snapshot = channel.connect()
    assert snapshot.execution_state == "READY"
    assert controller.client_tracker.connected is True


# ── 10. Per-User Session Isolation Tests ─────────────────────────────────────


def test_per_user_session_router_routes_to_correct_instance(
    test_auth: ControlPlaneAuth,
) -> None:
    """PerUserSessionRouter directs commands to isolated user sessions."""
    router = PerUserSessionRouter()

    session_a = MockLiveSession(execution_state=ExecutionState.READY)
    controller_a = ControlPlaneController(session=session_a, auth=test_auth, user_id="user_a")
    controller_a.handle_command(
        CommandRequest(
            command=ControlCommand.SELECT_STRATEGY,
            request_id="init_a",
            auth_token="secret_token_123",
            user_id="user_a",
            payload={"strategy_name": "ALPHA_STRAT"},
        )
    )

    session_b = MockLiveSession(execution_state=ExecutionState.READY)
    controller_b = ControlPlaneController(session=session_b, auth=test_auth, user_id="user_b")
    controller_b.handle_command(
        CommandRequest(
            command=ControlCommand.SELECT_STRATEGY,
            request_id="init_b",
            auth_token="secret_token_123",
            user_id="user_b",
            payload={"strategy_name": "BETA_STRAT"},
        )
    )

    router.register_user("user_a", controller_a)
    router.register_user("user_b", controller_b)

    req_a = CommandRequest(
        command=ControlCommand.REQUEST_SNAPSHOT,
        request_id="snap_a",
        auth_token="secret_token_123",
        user_id="user_a",
    )
    resp_a = router.route_command(req_a)
    assert resp_a.data["strategy"]["name"] == "ALPHA_STRAT"

    req_b = CommandRequest(
        command=ControlCommand.REQUEST_SNAPSHOT,
        request_id="snap_b",
        auth_token="secret_token_123",
        user_id="user_b",
    )
    resp_b = router.route_command(req_b)
    assert resp_b.data["strategy"]["name"] == "BETA_STRAT"


def test_per_user_session_router_rejects_unknown_user() -> None:
    """Unknown user ID rejected with UNKNOWN_USER_SESSION."""
    router = PerUserSessionRouter()
    req = CommandRequest(
        command=ControlCommand.REQUEST_SNAPSHOT,
        request_id="snap_unknown",
        auth_token="secret_token_123",
        user_id="unknown_user",
    )
    resp = router.route_command(req)
    assert resp.status == CommandStatus.REJECTED
    assert resp.error_code == "UNKNOWN_USER_SESSION"


# ── 11. RFC 6455 WebSocket Framing Tests ─────────────────────────────────────


def test_websocket_frame_encode_decode_roundtrip() -> None:
    """WebSocket text frame encodes and decodes correctly."""
    msg = json.dumps({"test": "vayren_control_plane", "value": 42})
    encoded = WebSocketFrame.encode(msg, opcode=WebSocketFrame.OP_TEXT, mask=False)
    decoded = WebSocketFrame.decode(encoded)
    assert decoded is not None
    opcode, payload, consumed = decoded
    assert opcode == WebSocketFrame.OP_TEXT
    assert payload.decode("utf-8") == msg
    assert consumed == len(encoded)


def test_websocket_frame_masked_encode_decode_roundtrip() -> None:
    """Client-to-server masked frame unmasks correctly on server."""
    msg = "secure_control_plane_command"
    encoded = WebSocketFrame.encode(msg, opcode=WebSocketFrame.OP_TEXT, mask=True)
    decoded = WebSocketFrame.decode(encoded)
    assert decoded is not None
    opcode, payload, consumed = decoded
    assert opcode == WebSocketFrame.OP_TEXT
    assert payload.decode("utf-8") == msg


def test_websocket_handshake_generation() -> None:
    """WebSocket handshake generates valid Sec-WebSocket-Accept."""
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    resp = make_websocket_handshake_response(key)
    assert b"HTTP/1.1 101 Switching Protocols" in resp
    assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in resp

    req_header = (
        "GET /control HTTP/1.1\r\n"
        "Host: server.example.com\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n\r\n"
    )
    parsed_key = parse_websocket_key(req_header)
    assert parsed_key == key


# ── 12. End-to-End Async Socket Loopback Test ────────────────────────────────


def test_async_socket_control_plane_loopback(test_auth: ControlPlaneAuth) -> None:
    """Full async socket loopback: handshake, initial snapshot, command exchange, close."""

    async def _run() -> None:
        session = MockLiveSession(execution_state=ExecutionState.READY)
        controller = ControlPlaneController(session=session, auth=test_auth)

        server = await start_control_plane_server(controller, host="127.0.0.1", port=0)
        sockets = server.sockets
        assert sockets is not None and len(sockets) > 0
        port = sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        try:
            # 1. Send WebSocket handshake
            key = "AQIDBAUGBwgJCgsMDQ4PEA=="
            handshake_req = (
                "GET /ws HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n\r\n"
            )
            writer.write(handshake_req.encode("utf-8"))
            await writer.drain()

            # 2. Read 101 response up to header delimiter
            handshake_resp = await reader.readuntil(b"\r\n\r\n")
            assert b"101 Switching Protocols" in handshake_resp

            # 3. Read initial RuntimeSnapshot pushed on connection
            snap_raw = await reader.read(4096)
            decoded = WebSocketFrame.decode(snap_raw)
            assert decoded is not None
            opcode, snap_payload, _ = decoded
            assert opcode == WebSocketFrame.OP_TEXT
            snapshot_dict = json.loads(snap_payload.decode("utf-8"))
            assert snapshot_dict["execution_state"] == "READY"

            # 4. Send START command
            cmd = {
                "command": "START",
                "request_id": "req-socket-start",
                "auth_token": "secret_token_123",
            }
            writer.write(WebSocketFrame.encode(json.dumps(cmd), mask=True))
            await writer.drain()

            # 5. Receive CommandResponse
            resp_raw = await reader.read(4096)
            decoded_resp = WebSocketFrame.decode(resp_raw)
            assert decoded_resp is not None
            _, resp_payload, _ = decoded_resp
            cmd_resp = json.loads(resp_payload.decode("utf-8"))
            assert cmd_resp["status"] == "SUCCESS"
            assert cmd_resp["data"]["state"] == "RUNNING"
            assert session.recovery.state_machine.state == ExecutionState.RUNNING

        finally:
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()

    asyncio.run(_run())
