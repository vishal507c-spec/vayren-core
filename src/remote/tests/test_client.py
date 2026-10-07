"""Client transport tests over real sockets with a fake backend.

Drives :class:`RemoteClient` against :class:`RemoteServer` on loopback
(plaintext, ephemeral port): hello/welcome/snapshot, auth rejection,
ping/pong, seq-ordered events, resubscribe replay, idempotent command
retries, and token hygiene (never logged, never hardcoded).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from remote.auth import RemoteRole, TokenEntry
from remote.client import (
    ClientConfig,
    ClientConfigError,
    RemoteClient,
    load_client_token,
    new_request_id,
)
from remote.gateway import BackendGateway
from remote.server import RemoteConfig, RemoteServer

TRADE_TOKEN = "trade-secret-0123456789abcdef"

TOKENS = {TRADE_TOKEN: TokenEntry(name="trade", role=RemoteRole.TRADE, secret=TRADE_TOKEN)}


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
        "broker": {"name": "FYERS", "connected": False, "reason": "not started"},
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

    def __init__(self) -> None:
        self.live = make_live()
        self.commands: list[dict[str, Any]] = []

    def get_snapshot(self) -> dict[str, Any]:
        return self.live

    def apply_command(self, action: dict[str, Any]) -> dict[str, Any]:
        self.commands.append(dict(action))
        return dict(self.live)

    def close(self) -> None:
        pass


@pytest.fixture()
def live_server() -> Any:
    gateway = FakeGateway()
    config = RemoteConfig(
        host="127.0.0.1",
        port=0,
        path="/vayren/v1",
        auth_timeout_s=5.0,
        heartbeat_interval_s=60.0,
        idle_timeout_s=60.0,
        poll_interval_s=0.05,
        max_connections=8,
    )
    server = RemoteServer(gateway, TOKENS, config)
    server.start()
    yield server, gateway
    server.stop()


def make_client(server: RemoteServer, token: str = TRADE_TOKEN, **kwargs: Any) -> RemoteClient:
    callback_names = ("on_snapshot", "on_state_update", "on_event", "on_command_result", "on_error")
    callbacks = {name: kwargs.pop(name) for name in callback_names if name in kwargs}
    config = ClientConfig(
        host="127.0.0.1",
        port=server.port,
        path="/vayren/v1",
        use_tls=False,
        connect_timeout_s=5.0,
        read_timeout_s=5.0,
        auto_ping_interval_s=60.0,
        **kwargs,
    )
    return RemoteClient(config, token, **callbacks)  # type: ignore[arg-type]


def test_hello_gets_welcome_and_snapshot(live_server: Any) -> None:
    server, _gateway = live_server
    client = make_client(server)
    try:
        welcome, _envelope = client.connect()
        assert welcome["role"] == "trade"
        assert client.role == "trade"
        assert "strategy" in client.snapshot
        assert client.snapshot["strategy"]["status"] == "BLOCKED"
    finally:
        client.close()


def test_bad_token_rejected_without_snapshot(live_server: Any) -> None:
    server, _gateway = live_server
    errors: list[tuple[str, str]] = []
    client = make_client(
        server, token="wrong-token-0123456789abcdef", on_error=lambda c, d: errors.append((c, d))
    )
    try:
        with pytest.raises(TimeoutError):
            client.connect()
        assert client.snapshot == {}
        assert any(code == "AUTH_FAILED" for code, _detail in errors)
    finally:
        client.close()


def test_ping_pong(live_server: Any) -> None:
    server, _gateway = live_server
    client = make_client(server)
    try:
        client.connect()
        client.ping()
        deadline = time.monotonic() + 5.0
        pong = None
        while time.monotonic() < deadline and pong is None:
            pong = client._pop("pong")
            time.sleep(0.02)
        assert pong is not None
    finally:
        client.close()


def test_events_arrive_in_seq_order(live_server: Any) -> None:
    server, gateway = live_server
    seen: list[tuple[str, int]] = []
    client = make_client(server, on_event=lambda body, seq: seen.append((body["name"], seq)))
    try:
        client.connect()
        gateway.live["events"] = [
            {
                "timestamp": "t1",
                "strategy": "OBR",
                "symbol": "AAA",
                "event": "ORDER SUBMITTED c1",
                "status": "info",
            },
            {
                "timestamp": "t2",
                "strategy": "OBR",
                "symbol": "AAA",
                "event": "FILLED 10 @ 100",
                "status": "ok",
            },
        ]
        deadline = time.monotonic() + 5.0
        while len(seen) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert [name for name, _seq in seen] == ["ORDER_SENT", "FILL"]
        seqs = [seq for _name, seq in seen]
        assert seqs[1] > seqs[0]
        assert client.last_seq == seqs[1]
    finally:
        client.close()


def test_resubscribe_replays_from_last_seq(live_server: Any) -> None:
    server, gateway = live_server
    first: list[str] = []
    client = make_client(server, on_event=lambda body, _seq: first.append(body["name"]))
    try:
        client.connect()
        gateway.live["events"] = [
            {
                "timestamp": "t1",
                "strategy": "OBR",
                "symbol": "AAA",
                "event": "ORDER SUBMITTED c1",
                "status": "info",
            },
        ]
        deadline = time.monotonic() + 5.0
        while not first and time.monotonic() < deadline:
            time.sleep(0.02)
        assert first == ["ORDER_SENT"]
    finally:
        client.close()
    replayed: list[str] = []
    snapshots: list[dict[str, Any]] = []
    second = make_client(
        server,
        on_event=lambda body, _seq: replayed.append(body["name"]),
        on_snapshot=lambda snap, _resync: snapshots.append(snap),
    )
    try:
        second.connect()
        second.subscribe(last_seq=0)
        deadline = time.monotonic() + 5.0
        while not replayed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert replayed == ["ORDER_SENT"]
        assert snapshots  # fresh snapshot still delivered on (re)subscribe
    finally:
        second.close()


def test_unknown_command_rejected_without_backend_effect(live_server: Any) -> None:
    server, gateway = live_server
    client = make_client(server)
    try:
        client.connect()
        key = client.send_command("place_fyers_order", {}, request_id="r-client-1")
        assert key == "r-client-1"
        result = client.wait_for_result("r-client-1", timeout=5.0)
        assert result is not None and result["type"] == "error"
        assert result["payload"]["code"] == "UNKNOWN_COMMAND"
        assert gateway.commands == []
    finally:
        client.close()


def test_request_id_reuse_replays_cached_result(live_server: Any) -> None:
    server, gateway = live_server
    client = make_client(server)
    try:
        client.connect()
        key = client.send_command("stop", {}, request_id="r-client-2")
        first = client.wait_for_result(key, timeout=5.0)
        assert first is not None and first["type"] == "command_result"
        assert first["payload"]["cached"] is False
        client.send_command("stop", {}, request_id=key)
        second = client.wait_for_result(key, timeout=5.0)
        assert second is not None and second["type"] == "command_result"
        assert second["payload"]["cached"] is True
        assert len(gateway.commands) == 1
    finally:
        client.close()


def test_token_hygiene(live_server: Any, caplog: Any) -> None:
    assert load_client_token({"VAYREN_REMOTE_TOKEN": TRADE_TOKEN}) == TRADE_TOKEN
    with pytest.raises(ClientConfigError):
        load_client_token({})
    with pytest.raises(ClientConfigError):
        load_client_token({"VAYREN_REMOTE_TOKEN": "short"})
    with pytest.raises(ClientConfigError):
        RemoteClient(make_client(live_server[0])._config, "short")
    assert new_request_id() != new_request_id()
    server, _gateway = live_server
    with caplog.at_level("INFO", logger="remote.client"):
        bad = make_client(server, token="wrong-token-0123456789abcdef")
        try:
            with pytest.raises(TimeoutError):
                bad.connect()
        finally:
            bad.close()
    assert "wrong-token-0123456789abcdef" not in caplog.text
    assert repr(bad) == f"RemoteClient(127.0.0.1:{server.port} token=REDACTED)"
    assert "wrong-token-0123456789abcdef" not in repr(bad)
