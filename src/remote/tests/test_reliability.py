"""Gateway reliability regressions: slow commands, health, bounded retries.

Fake backend, loopback sockets, no broker network and no real orders.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from remote.tests.test_server import FakeGateway, authed_client, make_live, start_server


class BlockingGateway(FakeGateway):
    """A venue call that hangs: the link must still answer pings."""

    def __init__(self) -> None:
        super().__init__(make_live())
        self.release = threading.Event()
        self.entered = threading.Event()

    def apply_command(self, action: dict[str, Any]) -> dict[str, Any]:
        if action.get("action") == "connect_broker":
            self.entered.set()
            assert self.release.wait(5.0), "test did not release the blocked command"
        return super().apply_command(action)


def test_slow_broker_call_does_not_block_ping_or_heartbeat() -> None:
    gateway = BlockingGateway()
    server, _ = start_server(gateway, heartbeat_interval_s=60.0)
    client, _welcome, _snapshot = authed_client(server)
    try:
        client.send_obj(
            {
                "type": "command",
                "v": 1,
                "request_id": "slow-1",
                "payload": {
                    "action": "connect_broker",
                    "params": {"broker_id": "fyers", "credentials": {}},
                },
            }
        )
        assert gateway.entered.wait(2.0), "deferred worker never started"
        client.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong: dict[str, Any] | None = None
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            frame = client.recv_obj(timeout=0.5)
            assert frame is not None
            if frame["type"] == "pong":
                pong = frame
                break
        assert pong is not None, "gateway stopped answering pings during a slow broker call"
        assert pong["payload"]["health"]["inflight_commands"] >= 1
        gateway.release.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            frame = client.recv_obj(timeout=1.0)
            assert frame is not None
            if frame["type"] == "command_result":
                assert frame["request_id"] == "slow-1"
                break
        else:
            raise AssertionError("deferred command never answered")
    finally:
        gateway.release.set()
        client.close()
        server.stop()


def test_deferred_command_reports_progress_and_latency() -> None:
    gateway = FakeGateway()
    server, _ = start_server(gateway)
    client, _welcome, _snapshot = authed_client(server)
    try:
        client.send_obj(
            {
                "type": "command",
                "v": 1,
                "request_id": "op-1",
                "payload": {"action": "server_op", "params": {"op": "health"}},
            }
        )
        frames: list[dict[str, Any]] = []
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            frame = client.recv_obj(timeout=1.0)
            if frame is None:
                continue
            frames.append(frame)
            if frame["type"] == "command_result":
                break
        kinds = [frame["type"] for frame in frames]
        assert "command_progress" in kinds, kinds
        assert kinds[-1] == "command_result", kinds
        assert frames[-1]["request_id"] == "op-1"
        client.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong = client.recv_obj(timeout=2.0)
        assert pong is not None
        health = pong["payload"]["health"]
        assert "p50_ms" in health["commands"]["server_op"]
        assert health["commands"]["server_op"]["count"] >= 1
    finally:
        client.close()
        server.stop()


def test_circuit_opens_on_repeated_backend_failure() -> None:
    gateway = FakeGateway()
    gateway.fail_commands = True
    server, _ = start_server(gateway, circuit_failure_threshold=1)
    client, _welcome, _snapshot = authed_client(server)
    try:
        command = {
            "type": "command",
            "v": 1,
            "request_id": "fail-1",
            "payload": {
                "action": "broker_status",
                "params": {"broker_id": "fyers"},
            },
        }
        client.send_obj(command)
        first = client.recv_obj(timeout=2.0)
        assert first is not None and first["type"] == "error", first
        client.send_obj({**command, "request_id": "fail-2"})
        second = client.recv_obj(timeout=2.0)
        assert second is not None and second["type"] == "error", second
        assert second["payload"]["code"] == "CIRCUIT_OPEN", second
        client.send_obj({"type": "ping", "v": 1, "payload": {}})
        pong = client.recv_obj(timeout=2.0)
        assert pong is not None
        assert pong["payload"]["health"]["circuits"]["broker_status"] == "OPEN"
    finally:
        client.close()
        server.stop()
