"""Safe-disconnect state machine tests (mocks only, no broker/network)."""

from __future__ import annotations

import json
from typing import Any

from app.services.safe_disconnect_service import SafeDisconnectService, SafeDisconnectState


class FakeService:
    def __init__(self, status: str = "RUNNING") -> None:
        self.status = status
        self.disarmed = 0
        self.stopped = 0
        self.stop_ack = True

    def disarm_live_execution(self, _reason: str = "") -> None:
        self.disarmed += 1

    def stop(self, progress_cb: Any = None) -> None:
        self.stopped += 1
        if self.stop_ack:
            self.status = "STOPPED"
        if progress_cb is not None:
            progress_cb("disarming", "fake disarm")


class FakeManager:
    def __init__(
        self,
        *,
        positions: list[dict[str, Any]] | None = None,
        orders: list[dict[str, Any]] | None = None,
        status: str = "CONNECTED",
        fail_read: bool = False,
        connector: Any = None,
        disconnect_ok: bool = True,
        disconnect_message: str = "disconnected",
    ) -> None:
        self.positions = positions or []
        self.orders = orders or []
        self.status = status
        self.fail_read = fail_read
        self.disconnects: list[str] = []
        self.connector = connector
        self.disconnect_ok = disconnect_ok
        self.disconnect_message = disconnect_message

    def state(self, _broker_id: str) -> dict[str, Any]:
        return {"status": self.status, "adapter": self.connector or object()}

    def disconnect_broker(self, _broker_id: str, request_id: str = "") -> tuple[bool, str]:
        self.disconnects.append(request_id)
        if not self.disconnect_ok:
            self.status = "ERROR"
            return False, self.disconnect_message
        self.status = "DISCONNECTED"
        return True, "disconnected"


class ExposureAdapter:
    def __init__(self, positions: list[dict[str, Any]], orders: list[dict[str, Any]]) -> None:
        self._positions = positions
        self._orders = orders

    def positions(self) -> list[dict[str, Any]]:
        return self._positions

    def open_orders(self) -> list[dict[str, Any]]:
        return self._orders


def _coordinator(
    tmp_path: Any, service: FakeService, manager: FakeManager
) -> SafeDisconnectService:
    return SafeDisconnectService(str(tmp_path), service, manager)


def test_flat_broker_disconnects_after_verified_stop(tmp_path: Any) -> None:
    engine, manager = FakeService(), FakeManager(connector=ExposureAdapter([], []))
    result = _coordinator(tmp_path, engine, manager).run("r1", "fyers")

    assert result["state"] == SafeDisconnectState.DISCONNECTED.value
    assert result["ok"] is True
    assert engine.disarmed == 1
    assert engine.stopped == 1
    assert manager.disconnects == ["r1"]
    assert result["exposure"] == {
        "positions": 0,
        "orders": 0,
        "unknown": False,
        "source": "BROKER_AUTHORITATIVE",
    }
    states = [step["state"] for step in result["steps"]]
    assert states == [
        SafeDisconnectState.STOPPING.value,
        SafeDisconnectState.RECONCILING.value,
        SafeDisconnectState.SAFE_TO_DISCONNECT.value,
        SafeDisconnectState.DISCONNECTING.value,
        SafeDisconnectState.DISCONNECTED.value,
    ]


def test_open_position_blocks_disconnect_and_keeps_broker(tmp_path: Any) -> None:
    engine = FakeService(status="STOPPED")
    adapter = ExposureAdapter([{"qty": 1, "symbol": "NOCIL"}], [])
    manager = FakeManager(connector=adapter)
    result = _coordinator(tmp_path, engine, manager).run("r2", "fyers")

    assert result["state"] == SafeDisconnectState.BLOCKED.value
    assert "positions=1" in result["reason"]
    assert manager.disconnects == []
    assert manager.status == "CONNECTED"
    assert engine.stopped == 0  # no stop needed when already stopped


def test_working_order_blocks_disconnect(tmp_path: Any) -> None:
    engine = FakeService(status="STOPPED")
    manager = FakeManager(connector=ExposureAdapter([], [{"id": "o1", "status": "PENDING"}]))
    result = _coordinator(tmp_path, engine, manager).run("r3", "fyers")

    assert result["state"] == SafeDisconnectState.BLOCKED.value
    assert "working_orders=1" in result["reason"]
    assert manager.disconnects == []


def test_unknown_exposure_blocks_even_with_acknowledgement(tmp_path: Any) -> None:
    class BrokenAdapter:
        def positions(self) -> list[dict[str, Any]]:
            raise RuntimeError("venue circuit open")

        def open_orders(self) -> list[dict[str, Any]]:
            return []

    manager = FakeManager(connector=BrokenAdapter())
    result = _coordinator(tmp_path, FakeService(status="STOPPED"), manager).run(
        "r4", "fyers", acknowledge_exposure=True
    )
    assert result["state"] == SafeDisconnectState.BLOCKED.value
    assert result["exposure"]["unknown"] is True
    assert manager.disconnects == []


def test_explicit_acknowledgement_allows_disconnect_without_liquidation(tmp_path: Any) -> None:
    adapter = ExposureAdapter([{"qty": 2}], [{"id": "o1"}])
    manager = FakeManager(connector=adapter)
    result = _coordinator(tmp_path, FakeService(status="STOPPED"), manager).run(
        "r5", "fyers", acknowledge_exposure=True
    )
    assert result["state"] == SafeDisconnectState.DISCONNECTED.value
    assert manager.disconnects == ["r5"]
    assert adapter._positions == [{"qty": 2}]  # never touched


def test_stop_not_acknowledged_keeps_broker(tmp_path: Any) -> None:
    engine = FakeService()
    engine.stop_ack = False
    manager = FakeManager(connector=ExposureAdapter([], []))
    result = _coordinator(tmp_path, engine, manager).run("r6", "fyers")

    assert result["state"] == SafeDisconnectState.FAILED.value
    assert "did not acknowledge STOP" in result["reason"]
    assert manager.disconnects == []


def test_duplicate_request_replays_cached_result(tmp_path: Any) -> None:
    engine, manager = FakeService(status="STOPPED"), FakeManager(connector=ExposureAdapter([], []))
    coordinator = _coordinator(tmp_path, engine, manager)
    first = coordinator.run("r7", "fyers")
    second = coordinator.run("r7", "fyers")
    assert second["cached"] is True
    assert second["state"] == first["state"]
    assert manager.disconnects == ["r7"]


def test_concurrent_duplicate_command_is_ignored(tmp_path: Any) -> None:
    engine = FakeService(status="STOPPED")
    manager = FakeManager(connector=ExposureAdapter([], []))

    def _observe(stage: str, _detail: str) -> None:
        if stage == "safe_to_disconnect":
            duplicate = coordinator.run("r8", "fyers")
            assert duplicate["reason"].startswith("a disconnect sequence is already")

    coordinator = SafeDisconnectService(str(tmp_path), engine, manager)
    result = coordinator.run("r8", "fyers", progress_cb=_observe)
    assert result["state"] == SafeDisconnectState.DISCONNECTED.value
    assert manager.disconnects == ["r8"]


def test_crash_recovery_fails_closed(tmp_path: Any) -> None:
    (tmp_path / "safe_disconnect_state.json").write_text(
        json.dumps({"state": "DISCONNECTING", "request_id": "r9", "broker_id": "fyers"}),
        encoding="utf-8",
    )
    coordinator = SafeDisconnectService(
        str(tmp_path), FakeService(), FakeManager(connector=ExposureAdapter([], []))
    )
    snap = coordinator.snapshot()
    assert snap["state"] == SafeDisconnectState.FAILED.value
    assert "restarted" in snap["reason"]


def test_failed_disconnect_clears_after_verified_reconnect(tmp_path: Any) -> None:
    manager = FakeManager(
        connector=ExposureAdapter([], []),
        disconnect_ok=False,
        disconnect_message="disconnect timed out after 10s; broker sockets may still be alive",
    )
    coordinator = _coordinator(tmp_path, FakeService(status="STOPPED"), manager)

    failed = coordinator.run("r10", "fyers")
    assert failed["state"] == SafeDisconnectState.FAILED.value
    assert coordinator.snapshot()["state"] == SafeDisconnectState.FAILED.value

    manager.status = "CONNECTED"
    coordinator.note_broker_connected("fyers")
    cleared = coordinator.snapshot()
    assert cleared["state"] == SafeDisconnectState.CONNECTED.value
    assert "re-established" in cleared["reason"]


def test_reconnect_reconciles_enum_broker_status(tmp_path: Any) -> None:
    """BrokerManager.state() returns a BrokerStatus enum, not a plain string."""

    class _EnumStatus:
        value = "CONNECTED"

        def __str__(self) -> str:
            return "BrokerStatus.CONNECTED"

    manager = FakeManager(
        connector=ExposureAdapter([], []),
        disconnect_ok=False,
        disconnect_message="disconnect timed out after 10s",
    )
    coordinator = _coordinator(tmp_path, FakeService(status="STOPPED"), manager)
    assert coordinator.run("r15", "fyers")["state"] == SafeDisconnectState.FAILED.value

    manager.status = _EnumStatus()  # type: ignore[assignment]
    coordinator.note_broker_connected("fyers")
    assert coordinator.snapshot()["state"] == SafeDisconnectState.CONNECTED.value


def test_failed_disconnect_stays_failed_while_broker_is_not_connected(tmp_path: Any) -> None:
    manager = FakeManager(
        connector=ExposureAdapter([], []),
        disconnect_ok=False,
        disconnect_message="disconnect timed out after 10s",
    )
    coordinator = _coordinator(tmp_path, FakeService(status="STOPPED"), manager)
    assert coordinator.run("r11", "fyers")["state"] == SafeDisconnectState.FAILED.value

    # The manager is still ERROR, so a reconnect note is not proof of a link.
    coordinator.note_broker_connected("fyers")
    assert coordinator.snapshot()["state"] == SafeDisconnectState.FAILED.value


def test_blocked_exposure_is_not_cleared_by_reconnect(tmp_path: Any) -> None:
    manager = FakeManager(connector=ExposureAdapter([{"qty": 1, "symbol": "NOCIL"}], []))
    coordinator = _coordinator(tmp_path, FakeService(status="STOPPED"), manager)
    assert coordinator.run("r12", "fyers")["state"] == SafeDisconnectState.BLOCKED.value

    manager.status = "CONNECTED"
    coordinator.note_broker_connected("fyers")
    blocked = coordinator.snapshot()
    assert blocked["state"] == SafeDisconnectState.BLOCKED.value
    assert "positions=1" in blocked["reason"]


def test_startup_clears_stale_disconnect_failure_when_broker_connected(tmp_path: Any) -> None:
    (tmp_path / "safe_disconnect_state.json").write_text(
        json.dumps(
            {
                "state": "FAILED",
                "request_id": "r13",
                "broker_id": "fyers",
                "reason": "broker did not confirm disconnect: disconnect timed out after 10s",
            }
        ),
        encoding="utf-8",
    )
    manager = FakeManager(status="CONNECTED", connector=ExposureAdapter([], []))
    coordinator = _coordinator(tmp_path, FakeService(status="STOPPED"), manager)

    snap = coordinator.snapshot()
    assert snap["state"] == SafeDisconnectState.CONNECTED.value
    assert "re-established" in snap["reason"]


def test_startup_keeps_engine_stop_failure_even_when_broker_connected(tmp_path: Any) -> None:
    (tmp_path / "safe_disconnect_state.json").write_text(
        json.dumps(
            {
                "state": "FAILED",
                "request_id": "r14",
                "broker_id": "fyers",
                "reason": (
                    "engine did not acknowledge STOP (status=RUNNING) - broker kept connected"
                ),
            }
        ),
        encoding="utf-8",
    )
    manager = FakeManager(status="CONNECTED", connector=ExposureAdapter([], []))
    coordinator = _coordinator(tmp_path, FakeService(status="STOPPED"), manager)

    snap = coordinator.snapshot()
    assert snap["state"] == SafeDisconnectState.FAILED.value
    assert "did not acknowledge STOP" in snap["reason"]
