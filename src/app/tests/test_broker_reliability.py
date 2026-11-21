"""Reliability regressions: truthful order-stream state and bounded disconnect."""

from __future__ import annotations

import time
from types import SimpleNamespace

from app.services.broker_manager import BrokerManager
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.status import BrokerStatus


class _StubWorker:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, str]] = []

    def submit(self, kind: str, broker_id: str) -> None:
        self.jobs.append((kind, broker_id))


class _StaleAdapter:
    def order_stream_health(self) -> tuple[bool, str]:
        return False, "stale — no order update for 100s"

    def restart_order_stream(self, *, timeout_s: float = 8.0) -> tuple[bool, str]:
        return True, f"verified in {timeout_s}s"


def test_order_stream_leg_schedules_one_recovery_and_reports_reconnecting(tmp_path) -> None:
    manager = BrokerManager(str(tmp_path), specs={})
    manager._specs = {"fyers": SimpleNamespace(broker_id="fyers")}  # type: ignore[attr-defined]
    manager._states = {"fyers": {"adapter": _StaleAdapter()}}
    manager._unified = {"fyers": {"legs": {}}}
    manager._worker = _StubWorker()  # type: ignore[assignment]
    manager._desired = {"fyers": "CONNECTED"}
    manager._order_ws_recovery_next = {}
    manager._order_ws_recovery_attempts = {}
    manager.ORDER_WS_RECOVERY_COOLDOWN_S = 60.0

    first = manager._sync_order_stream_leg("fyers")
    first_status = manager._unified["fyers"]["legs"]["order_ws"]["status"]
    second = manager._sync_order_stream_leg("fyers")

    assert "recovery in progress" in first
    assert "cooldown" in second
    assert manager._worker.jobs == [("order-stream-recovery", "fyers")]  # type: ignore[attr-defined]
    assert first_status == "RECONNECTING"
    # Cooldown is truthfully FAILED again (never a false READY).
    assert manager._unified["fyers"]["legs"]["order_ws"]["status"] == "FAILED"


class _FakeSocket:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.subscribed: str | None = None

    def connect(self) -> None:
        self.kwargs["on_connect"]()

    def subscribe(self, data_type: str) -> None:
        self.subscribed = data_type


def test_restart_order_stream_resets_staleness_and_does_not_duplicate() -> None:
    sockets: list[_FakeSocket] = []

    def factory(**kwargs):
        sock = _FakeSocket(**kwargs)
        sockets.append(sock)
        return sock

    adapter = FyersSessionAdapter(
        "APP",
        "TOKEN",
        enable_order_ws=True,
        order_socket_factory=factory,
    )
    adapter._order_ws_messages_received = 1
    adapter._order_ws_last_message_at = time.monotonic() - 1000.0
    adapter._order_ws = object()  # stale socket that must be detached first

    ok, detail = adapter.restart_order_stream(timeout_s=1.0)

    assert ok is True, detail
    assert len(sockets) == 1
    assert sockets[0].subscribed == "OnOrders,OnTrades"
    assert adapter.order_stream_age_s is not None and adapter.order_stream_age_s < 5.0
    assert adapter._order_ws_recovery_in_flight is False


def test_disconnect_failure_reports_error_instead_of_false_disconnected(tmp_path) -> None:
    manager = BrokerManager(str(tmp_path), specs={})
    slow_spec = SimpleNamespace(broker_id="fyers", venue_unregister=None)
    manager._specs = {"fyers": slow_spec}  # type: ignore[attr-defined]
    state = manager._fresh_state(slow_spec)  # type: ignore[arg-type]
    state["adapter"] = SimpleNamespace(disconnect=lambda: time.sleep(0.3))
    manager._states = {"fyers": state}
    manager.DISCONNECT_TIMEOUT_S = 0.01

    ok, message = manager.disconnect_broker("fyers", request_id="")

    assert ok is False
    assert "timed out" in message
    assert manager._states["fyers"]["status"] == BrokerStatus.ERROR


def test_duplicate_disconnect_request_replays_cached_failure(tmp_path) -> None:
    manager = BrokerManager(str(tmp_path), specs={})
    slow_spec = SimpleNamespace(broker_id="fyers", venue_unregister=None)
    manager._specs = {"fyers": slow_spec}  # type: ignore[attr-defined]
    state = manager._fresh_state(slow_spec)  # type: ignore[arg-type]
    state["adapter"] = SimpleNamespace(disconnect=lambda: time.sleep(0.3))
    manager._states = {"fyers": state}
    manager.DISCONNECT_TIMEOUT_S = 0.01

    first = manager.disconnect_broker("fyers", request_id="req-1")
    second = manager.disconnect_broker("fyers", request_id="req-1")

    assert first == second
