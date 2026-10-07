"""Transport tests over real sockets with a fake backend (no real orders).

A minimal in-test WebSocket client speaks to :class:`RemoteServer` on
loopback with a :class:`FakeGateway` standing in for the headless backend.
Covers authentication, snapshot-on-connect, streaming, heartbeat, timeout,
reconnect/resubscription, duplicate suppression, authorization, gate
preservation, malformed input, and version handling.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import struct
import threading
import time
from typing import Any

import pytest

from remote.auth import RemoteRole, TokenEntry
from remote.gateway import BackendGateway
from remote.protocol import SCHEMA_VERSION
from remote.server import ConfigError, RemoteConfig, RemoteServer
from remote.snapshot import FORBIDDEN_KEY_SUBSTRINGS, FORBIDDEN_VALUE_MARKERS

TRADE_TOKEN = "trade-secret-0123456789abcdef"
READ_TOKEN = "read-secret-0123456789abcdef"

TOKENS = {
    TRADE_TOKEN: TokenEntry(name="trade", role=RemoteRole.TRADE, secret=TRADE_TOKEN),
    READ_TOKEN: TokenEntry(name="read", role=RemoteRole.READ, secret=READ_TOKEN),
}


def make_live(**overrides: Any) -> dict[str, Any]:
    live: dict[str, Any] = {
        "mode": "PAPER",
        "session_status": "STOPPED",
        "status_reason": "",
        "as_of": "2026-10-06T00:00:00+00:00",
        "websocket": {"status": "DISCONNECTED"},
        "market_data": {"status": "STOPPED", "exchange": "NSE Cash", "timeframe": "15m"},
        "feed": "none",
        "risk_engine": {
            "effective_capital": 400000.0,
            "max_allowed_risk": 600.0,
            "broker_capital": None,
            "sizing_status": "NOT READY",
            "sizing_reason": "broker capital unavailable",
        },
        "broker": {
            "name": "FYERS",
            "connected": False,
            "reason": "not started",
            "account_id": "",
            "status": "",
        },
        "positions": [],
        "orders": [],
        "fills": [],
        "reconciliation": {"status": "NOT CONFIGURED", "blocks_live": False},
        "kill": {"halted": False},
        "execution": {"ready": False, "reason": "session not running"},
        "capital": {"source": "configured", "available_margin": None},
        "start_blockers": ["no strategy selected"],
        "events": [],
    }
    live.update(overrides)
    return live


class FakeGateway(BackendGateway):
    """Scripted backend: records commands, never trades."""

    def __init__(self, live: dict[str, Any] | None = None) -> None:
        self.live = live if live is not None else make_live()
        self.commands: list[dict[str, Any]] = []
        self.fail_commands = False

    def get_snapshot(self) -> dict[str, Any]:
        return self.live

    def apply_command(self, action: dict[str, Any]) -> dict[str, Any]:
        self.commands.append(dict(action))
        if self.fail_commands:
            raise RuntimeError("backend exploded")
        running = dict(self.live)
        if action.get("action") == "start":
            running["session_status"] = "RUNNING"
            running["start_blockers"] = []
        return running

    def close(self) -> None:
        pass


class WsClient:
    """Minimal masked-frame WebSocket client for tests."""

    def __init__(self, port: int, path: str = "/vayren/v1") -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("latin-1")
        )
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake failed")
            response += chunk
        status = response.split(b"\r\n", 1)[0]
        if b"101" not in status:
            self.sock.close()
            raise ConnectionError(f"handshake rejected: {status!r}")

    def send_obj(self, obj: dict[str, Any]) -> None:
        self.send_raw(json.dumps(obj).encode("utf-8"))

    def send_raw(self, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytes([0x81, 0x80 | (len(payload) & 0x7F)])
        if len(payload) >= 126:
            raise ValueError("test payload too large")
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv_frame(self, timeout: float = 5.0) -> tuple[int, bytes] | None:
        """Next server frame; None on clean close/EOF."""
        self.sock.settimeout(timeout)
        try:
            header = self._recv_exact(2)
        except (TimeoutError, ConnectionError, OSError):
            return None
        length = header[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._recv_exact(8))
        payload = self._recv_exact(length) if length else b""
        opcode = header[0] & 0x0F
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            self._send_pong(payload)
            return self.recv_frame(timeout)
        return opcode, payload

    def _send_pong(self, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytes([0x8A, 0x80 | len(payload)])
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with contextlib.suppress(OSError):
            self.sock.sendall(header + mask + masked)

    def _recv_exact(self, count: int) -> bytes:
        chunks: list[bytes] = []
        remaining = count
        try:
            while remaining > 0:
                chunk = self.sock.recv(remaining)
                if not chunk:
                    raise ConnectionError("closed")
                chunks.append(chunk)
                remaining -= len(chunk)
        except TimeoutError as exc:
            raise TimeoutError("recv timed out") from exc
        return b"".join(chunks)

    def recv_obj(self, timeout: float = 5.0) -> dict[str, Any] | None:
        """Next text message as JSON; None on close/timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.recv_frame(timeout=max(0.1, deadline - time.monotonic()))
            if frame is None:
                return None
            opcode, payload = frame
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))
        return None

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


def start_server(
    gateway: FakeGateway, **overrides: Any
) -> tuple[RemoteServer, threading.Thread | None]:
    import dataclasses

    config = RemoteConfig(
        host="127.0.0.1",
        port=0,
        path="/vayren/v1",
        auth_timeout_s=5.0,
        heartbeat_interval_s=60.0,
        idle_timeout_s=30.0,
        poll_interval_s=0.05,
        max_connections=8,
    )
    if overrides:
        config = dataclasses.replace(config, **overrides)
    server = RemoteServer(gateway, TOKENS, config)
    server.start()
    return server, None


@pytest.fixture()
def live_server() -> Any:
    gateway = FakeGateway()
    server, _ = start_server(gateway)
    yield server, gateway
    server.stop()


def authed_client(
    server: RemoteServer, token: str = TRADE_TOKEN
) -> tuple[WsClient, dict[str, Any], dict[str, Any]]:
    client = WsClient(server.port)
    client.send_obj({"type": "hello", "v": 1, "payload": {"token": token}})
    welcome = client.recv_obj()
    assert welcome is not None and welcome["type"] == "welcome"
    snapshot = client.recv_obj()
    assert snapshot is not None and snapshot["type"] == "snapshot"
    return client, welcome, snapshot


def test_hello_gets_welcome_and_snapshot(live_server: Any) -> None:
    server, _gateway = live_server
    client, welcome, snapshot = authed_client(server)
    try:
        assert welcome["payload"]["role"] == "trade"
        assert welcome["v"] == SCHEMA_VERSION
        sections = snapshot["payload"]["snapshot"]
        for section in (
            "broker",
            "market",
            "strategy",
            "risk",
            "capital",
            "orders",
            "positions",
            "stops",
            "reconciliation",
            "system",
            "blockers",
        ):
            assert section in sections, section
        assert sections["strategy"]["status"] == "BLOCKED"
    finally:
        client.close()


def test_unauthenticated_command_rejected(live_server: Any) -> None:
    server, gateway = live_server
    client = WsClient(server.port)
    try:
        client.send_obj(
            {"type": "command", "v": 1, "request_id": "r-1", "payload": {"action": "stop"}}
        )
        error = client.recv_obj()
        assert error is not None and error["type"] == "error"
        assert error["payload"]["code"] == "NOT_AUTHENTICATED"
        assert gateway.commands == []
    finally:
        client.close()


def test_bad_token_attempts_then_close(live_server: Any) -> None:
    server, _gateway = live_server
    client = WsClient(server.port)
    try:
        for _ in range(3):
            client.send_obj({"type": "hello", "v": 1, "payload": {"token": "bad-token-0123456789"}})
            error = client.recv_obj()
            assert error is not None and error["type"] == "error"
            assert error["payload"]["code"] == "AUTH_FAILED"
        assert client.recv_obj(timeout=5.0) is None
    finally:
        client.close()


def test_command_passthrough_and_duplicate_cached(live_server: Any) -> None:
    server, gateway = live_server
    client, _welcome, _snapshot = authed_client(server)
    try:
        command = {"type": "command", "v": 1, "request_id": "req-42", "payload": {"action": "stop"}}
        client.send_obj(command)
        first = client.recv_obj()
        assert first is not None and first["type"] == "command_result"
        assert first["request_id"] == "req-42"
        assert first["payload"]["cached"] is False
        assert gateway.commands == [{"action": "stop"}]
        client.send_obj(command)
        second = client.recv_obj()
        assert second is not None and second["type"] == "command_result"
        assert second["payload"]["cached"] is True
        assert len(gateway.commands) == 1
    finally:
        client.close()


def test_read_token_cannot_send_commands(live_server: Any) -> None:
    server, gateway = live_server
    client, welcome, _snapshot = authed_client(server, token=READ_TOKEN)
    try:
        assert welcome["payload"]["role"] == "read"
        client.send_obj(
            {"type": "command", "v": 1, "request_id": "r-2", "payload": {"action": "stop"}}
        )
        error = client.recv_obj()
        assert error is not None and error["type"] == "error"
        assert error["payload"]["code"] == "NOT_AUTHORIZED"
        assert gateway.commands == []
    finally:
        client.close()


def test_start_requires_explicit_confirmation(live_server: Any) -> None:
    server, gateway = live_server
    client, _welcome, _snapshot = authed_client(server)
    try:
        client.send_obj(
            {
                "type": "command",
                "v": 1,
                "request_id": "r-3",
                "payload": {"action": "start", "params": {}},
            }
        )
        error = client.recv_obj()
        assert error is not None and error["type"] == "error"
        assert error["payload"]["code"] == "COMMAND_REJECTED"
        assert gateway.commands == []
        client.send_obj(
            {
                "type": "command",
                "v": 1,
                "request_id": "r-4",
                "payload": {"action": "start", "params": {"confirmed": True}},
            }
        )
        result = client.recv_obj()
        assert result is not None and result["type"] == "command_result"
        assert result["payload"]["session_status"] == "RUNNING"
        assert gateway.commands[-1]["confirmed"] is True
    finally:
        client.close()


def test_unknown_action_rejected(live_server: Any) -> None:
    server, gateway = live_server
    client, _welcome, _snapshot = authed_client(server)
    try:
        client.send_obj(
            {
                "type": "command",
                "v": 1,
                "request_id": "r-5",
                "payload": {"action": "place_fyers_order"},
            }
        )
        error = client.recv_obj()
        assert error is not None and error["payload"]["code"] == "UNKNOWN_COMMAND"
        assert gateway.commands == []
    finally:
        client.close()


def test_gate_blockers_preserved_in_result(live_server: Any) -> None:
    server, gateway = live_server
    client, _welcome, _snapshot = authed_client(server)
    try:
        client.send_obj(
            {"type": "command", "v": 1, "request_id": "r-6", "payload": {"action": "stop"}}
        )
        result = client.recv_obj()
        assert result is not None and result["type"] == "command_result"
        assert result["payload"]["start_blockers"] == ["no strategy selected"]
        assert result["payload"]["snapshot"]["strategy"]["status"] == "BLOCKED"
    finally:
        client.close()


def test_malformed_message_does_not_kill_connection(live_server: Any) -> None:
    server, _gateway = live_server
    client, _welcome, _snapshot = authed_client(server)
    try:
        client.send_raw(b"this is not json")
        error = client.recv_obj()
        assert error is not None and error["type"] == "error"
        assert error["payload"]["code"] == "BAD_MESSAGE"
        client.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong = client.recv_obj()
        assert pong is not None and pong["type"] == "pong"
    finally:
        client.close()


def test_unsupported_schema_version_rejected(live_server: Any) -> None:
    server, _gateway = live_server
    client = WsClient(server.port)
    try:
        client.send_obj({"type": "hello", "v": 99, "payload": {"token": TRADE_TOKEN}})
        error = client.recv_obj()
        assert error is not None and error["type"] == "error"
        assert error["payload"]["code"] == "UNSUPPORTED_VERSION"
    finally:
        client.close()


def test_ping_gets_pong(live_server: Any) -> None:
    server, _gateway = live_server
    client, _welcome, _snapshot = authed_client(server)
    try:
        client.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong = client.recv_obj()
        assert pong is not None and pong["type"] == "pong"
    finally:
        client.close()


def test_event_streaming_then_resubscribe_replays() -> None:
    gateway = FakeGateway()
    server, _ = start_server(gateway)
    try:
        client, _welcome, _snapshot = authed_client(server)
        try:
            gateway.live["events"] = [
                {
                    "timestamp": "t1",
                    "strategy": "OBR",
                    "symbol": "AAA",
                    "event": "ORDER SUBMITTED · c1",
                    "status": "info",
                    "category": "ORDERS",
                },
                {
                    "timestamp": "t2",
                    "strategy": "OBR",
                    "symbol": "AAA",
                    "event": "FILLED · 10 @ 100.5",
                    "status": "ok",
                    "category": "ORDERS",
                },
            ]
            names: list[str] = []
            seqs: list[int] = []
            deadline = time.monotonic() + 5.0
            while len(names) < 2 and time.monotonic() < deadline:
                message = client.recv_obj(timeout=2.0)
                assert message is not None, "expected streamed events"
                if message["type"] == "event":
                    names.append(message["payload"]["name"])
                    seqs.append(message["seq"])
            assert names == ["ORDER_SENT", "FILL"]
            assert seqs[1] > seqs[0]
        finally:
            client.close()
        second = WsClient(server.port)
        try:
            second.send_obj({"type": "hello", "v": 1, "payload": {"token": TRADE_TOKEN}})
            assert second.recv_obj() is not None
            assert second.recv_obj() is not None
            second.send_obj(
                {"type": "subscribe", "v": 1, "payload": {"channels": ["events"], "last_seq": 0}}
            )
            replayed: list[str] = []
            deadline = time.monotonic() + 5.0
            while len(replayed) < 2 and time.monotonic() < deadline:
                message = second.recv_obj(timeout=2.0)
                assert message is not None, "expected replayed events"
                if message["type"] == "event":
                    replayed.append(message["payload"]["name"])
            assert replayed == ["ORDER_SENT", "FILL"]
        finally:
            second.close()
    finally:
        server.stop()


def collect_wire_texts(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages).lower()


def test_no_broker_secrets_on_wire(live_server: Any) -> None:
    server, _gateway = live_server
    client, welcome, snapshot = authed_client(server)
    seen = [welcome, snapshot]
    try:
        client.send_obj(
            {"type": "command", "v": 1, "request_id": "r-7", "payload": {"action": "stop"}}
        )
        result = client.recv_obj()
        assert result is not None
        seen.append(result)
        client.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong = client.recv_obj()
        assert pong is not None
        seen.append(pong)
    finally:
        client.close()
    raw = collect_wire_texts(seen)
    for marker in FORBIDDEN_VALUE_MARKERS:
        assert marker not in raw, marker

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).lower()
                for fragment in FORBIDDEN_KEY_SUBSTRINGS:
                    assert fragment not in lowered, key
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    for message in seen:
        _walk(message)


def test_unknown_endpoint_rejected() -> None:
    gateway = FakeGateway()
    server, _ = start_server(gateway)
    try:
        with pytest.raises(ConnectionError):
            WsClient(server.port, path="/nope")
    finally:
        server.stop()


def test_plaintext_non_loopback_refused() -> None:
    config = RemoteConfig(host="0.0.0.0", port=0)
    with pytest.raises(ConfigError):
        RemoteServer(FakeGateway(), TOKENS, config)


def test_auth_timeout_closes_quiet_client() -> None:
    gateway = FakeGateway()
    server, _ = start_server(gateway, auth_timeout_s=1.0, idle_timeout_s=60.0)
    try:
        client = WsClient(server.port)
        try:
            timed_out = False
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                message = client.recv_obj(timeout=2.0)
                if message is None:
                    break
                if (
                    message.get("type") == "error"
                    and message.get("payload", {}).get("code") == "NOT_AUTHENTICATED"
                ):
                    timed_out = True
            assert timed_out, "expected an authentication-timeout error"
            assert client.recv_obj(timeout=3.0) is None, "expected the close to follow"
        finally:
            client.close()
    finally:
        server.stop()


def test_idle_timeout_closes_silent_client() -> None:
    gateway = FakeGateway()
    server, _ = start_server(gateway, idle_timeout_s=1.5, heartbeat_interval_s=60.0)
    try:
        client, _welcome, _snapshot = authed_client(server)
        try:
            deadline = time.monotonic() + 8.0
            closed = False
            while time.monotonic() < deadline:
                if client.recv_obj(timeout=2.0) is None:
                    closed = True
                    break
            assert closed, "idle client should be disconnected"
        finally:
            client.close()
    finally:
        server.stop()
