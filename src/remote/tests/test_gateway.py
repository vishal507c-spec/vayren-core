"""Gateway tests: delegation without logic, bounded event diffing."""

from typing import Any

from remote.gateway import BackendGateway, HeadlessGateway, entry_signature, new_entries


def _entry(text: str, stamp: str) -> dict[str, Any]:
    return {
        "timestamp": stamp,
        "strategy": "OBR",
        "symbol": "AAA",
        "event": text,
        "status": "ok",
        "category": "ORDERS",
    }


def test_new_entries_empty_previous_resyncs_bounded() -> None:
    current = [_entry(f"e{i}", f"t{i}") for i in range(30)]
    fresh, resync = new_entries([], current, cap=20)
    assert resync is True
    assert len(fresh) == 20
    assert fresh[0]["event"] == "e10"


def test_new_entries_returns_only_fresh_suffix() -> None:
    previous = [_entry(f"e{i}", f"t{i}") for i in range(5)]
    current = list(previous) + [_entry("e5", "t5"), _entry("e6", "t6")]
    fresh, resync = new_entries(previous, current)
    assert resync is False
    assert [item["event"] for item in fresh] == ["e5", "e6"]


def test_new_entries_empty_when_nothing_new() -> None:
    previous = [_entry(f"e{i}", f"t{i}") for i in range(3)]
    fresh, resync = new_entries(previous, list(previous))
    assert (fresh, resync) == ([], False)


def test_new_entries_overlap_lost_resyncs() -> None:
    previous = [_entry("old", "t0")]
    current = [_entry(f"e{i}", f"t{i}") for i in range(5)]
    fresh, resync = new_entries(previous, current)
    assert resync is True
    assert len(fresh) == 5


def test_entry_signature_stable() -> None:
    assert entry_signature(_entry("x", "t")) == ("t", "OBR", "AAA", "x")
    assert entry_signature({}) == ("", "", "", "")


class _FakeBackend(BackendGateway):
    def __init__(self) -> None:
        self.snapshots = 0
        self.commands: list[dict[str, Any]] = []

    def get_snapshot(self) -> dict[str, Any]:
        self.snapshots += 1
        return {"session_status": "STOPPED", "events": []}

    def apply_command(self, action: dict[str, Any]) -> dict[str, Any]:
        self.commands.append(action)
        return {"session_status": "STOPPED", "applied": action}

    def close(self) -> None:
        pass


def test_gateway_interface_delegates() -> None:
    backend = _FakeBackend()
    assert backend.get_snapshot()["session_status"] == "STOPPED"
    assert backend.apply_command({"action": "stop"})["applied"] == {"action": "stop"}
    assert backend.snapshots == 1


def test_headless_gateway_delegates_to_headless(monkeypatch) -> None:  # noqa: ANN001
    import app.headless

    seen: dict[str, Any] = {}

    def _fake_snapshot(data_dir: str, strategy_dir: str, manager: Any = None) -> dict[str, Any]:
        seen["snapshot_args"] = (data_dir, strategy_dir, manager)
        return {"session_status": "STOPPED"}

    def _fake_action(
        data_dir: str, strategy_dir: str, action: dict[str, Any], _manager: Any = None
    ) -> dict[str, Any]:
        seen["action_args"] = (data_dir, strategy_dir, action)
        return {"session_status": "STOPPED"}

    monkeypatch.setattr(app.headless, "_trading_service_snapshot", _fake_snapshot)
    monkeypatch.setattr(app.headless, "_live_action", _fake_action)
    monkeypatch.setattr(HeadlessGateway, "_ensure_manager", lambda _self: None)
    gateway = HeadlessGateway("/data", "/strategies")
    assert gateway.get_snapshot() == {"session_status": "STOPPED"}
    assert gateway.apply_command({"action": "stop"}) == {"session_status": "STOPPED"}
    assert seen["snapshot_args"][:2] == ("/data", "/strategies")
    assert seen["action_args"][2] == {"action": "stop"}
    gateway.close()


def test_headless_gateway_close_is_safe_without_manager() -> None:
    gateway = HeadlessGateway("/data", "/strategies")
    gateway.close()
