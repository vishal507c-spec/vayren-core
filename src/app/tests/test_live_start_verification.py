"""LIVE START-time session proof and SYSTEM freshness regressions.

Pins the remaining readiness gaps:

* START must not accept a cached ``CONNECTED`` snapshot — a fresh broker
  profile check and a bounded order-WebSocket proof run first.
* A dropped/cooldown stream fails closed with the exact blocker and never
  bypasses operator consent.
* Daily-token expiry is written to the authoritative session promptly and
  invalidates LIVE readiness instead of lingering as ``CONNECTED``.
* Freshness checks are queued at most once per window (no polling storm).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.broker_manager import BrokerManager
from app.services.live_trading_service import LiveTradingService
from broker.status import BrokerStatus


class _FakeAdapter:
    """Real class (not MagicMock) so bound-method checks behave like FYERS."""

    def __init__(
        self,
        *,
        ws_result: tuple[bool, str] = (True, "connected — no order updates received yet"),
        connect_error: BaseException | None = None,
    ) -> None:
        self._connected = False
        self.ws_result = ws_result
        self.connect_error = connect_error
        self.connect_calls = 0
        self.verify_calls = 0

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> tuple[bool, str]:
        return True, "ok"

    def verify_order_stream(self, timeout_s: float = 5.0) -> tuple[bool, str]:
        _ = timeout_s
        self.verify_calls += 1
        return self.ws_result

    def order_stream_health(self) -> tuple[bool, str]:
        return self.ws_result

    def account(self) -> dict[str, str]:
        return {"account_id": "XV29244"}

    def funds(self) -> dict[str, float]:
        return {"available": 100.0, "used": 0.0, "total": 100.0}

    def positions(self) -> list[object]:
        return []

    def open_orders(self) -> list[object]:
        return []


class _ExpiredAdapter(_FakeAdapter):
    def health(self) -> tuple[bool, str]:
        return False, "session expired or invalid: HTTP 401"

    def account(self) -> dict[str, str]:
        raise RuntimeError("token expired")

    def funds(self) -> dict[str, float]:
        raise RuntimeError("token expired")

    def positions(self) -> list[object]:
        raise RuntimeError("token expired")

    def open_orders(self) -> list[object]:
        raise RuntimeError("token expired")


class _FreshManager:
    """Minimal manager with the START proof bound method LIVE consults."""

    def __init__(
        self,
        result: tuple[bool, str] = (True, "session verified"),
        *,
        session_state: str = "CONNECTED",
        auth_valid: bool = True,
        verification_stale: bool = False,
    ) -> None:
        self.result = result
        self.verify_calls: list[tuple[str, float | None]] = []
        self.session = {
            "state": session_state,
            "auth_valid": auth_valid,
            "verification_stale": verification_stale,
            "version": 1,
            "account_id": "XV29244",
            "funds": {"available": 100.0},
            "reason": "test session",
        }

    def verify_live_session(
        self, broker_id: str, timeout_s: float | None = None
    ) -> tuple[bool, str]:
        self.verify_calls.append((broker_id, timeout_s))
        return self.result

    def get_session_snapshot(self, broker_id: str) -> dict[str, object]:
        return dict(self.session) if broker_id else {}

    def is_connected(self, broker_id: str) -> bool:
        return bool(broker_id) and self.session.get("state") == "CONNECTED"

    def snapshot(self) -> dict[str, object]:
        return {"active_broker": "fyers", "brokers": []}

    def state(self, _broker_id: str) -> dict[str, object]:
        return {
            "adapter": None,
            "market_data": None,
            "configured": True,
            "status": BrokerStatus.CONNECTED,
            "reason": "test session",
        }


@pytest.fixture()
def service(tmp_path) -> LiveTradingService:
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(str(data_dir), str(strat_dir))
    svc._universes.save("OBR C1C4", ("NSE:KAYNES",))
    svc.configure(
        strategy_name="OBR C1C4",
        symbols=("NSE:KAYNES",),
        timeframe="5m",
        mode="LIVE",
        quantity=1.0,
    )
    return svc


def _attach(service: LiveTradingService, manager: object) -> None:
    service._broker_manager = manager  # type: ignore[assignment]
    service._selection = SimpleNamespace(  # type: ignore[assignment]
        current=lambda: SimpleNamespace(name="fyers")
    )


def _manager_with_adapter(tmp_path, adapter: _FakeAdapter) -> BrokerManager:
    manager = BrokerManager(tmp_path / "data")
    manager._desired["fyers"] = "CONNECTED"
    adapter._connected = True
    state = manager._states["fyers"]
    state.update(
        {
            "adapter": adapter,
            "status": BrokerStatus.CONNECTED,
            "configured": True,
            "reason": "connected",
            "checks": {"connection": "READY", "account": "READY", "funds": "READY"},
            "details": {"account_id": "XV29244", "funds_available": 100.0},
        }
    )
    manager._unified["fyers"]["last_verified"] = ""
    manager._unified["fyers"]["last_verified_epoch"] = 0.0
    return manager


# ── START integration ────────────────────────────────────────────────


def test_immediate_disconnect_before_start_blocks_live(service: LiveTradingService) -> None:
    manager = _FreshManager(
        (False, "order stream not verified: order stream not connected within 0.1s")
    )
    _attach(service, manager)
    with (
        patch.object(service, "validate", return_value=()),
        patch.object(service, "_build_sessions") as build,
    ):
        ok, reasons = service.start(confirmed=True)

    assert not ok
    assert any("order stream not verified" in r for r in reasons), reasons
    assert manager.verify_calls == [("fyers", 8.0)]
    build.assert_not_called()


def test_healthy_fresh_stream_with_consent_proceeds(service: LiveTradingService) -> None:
    manager = _FreshManager()
    _attach(service, manager)
    try:
        with (
            patch.object(service, "validate", return_value=()),
            patch.object(service, "_build_sessions") as build,
        ):
            ok, reasons = service.start(confirmed=True)
        assert ok, reasons
        assert manager.verify_calls == [("fyers", 8.0)]
        build.assert_called_once()
    finally:
        service.stop("test cleanup")


def test_healthy_stream_never_bypasses_consent(service: LiveTradingService) -> None:
    manager = _FreshManager()
    _attach(service, manager)
    with patch.object(service, "_build_sessions") as build:
        ok, reasons = service.start(confirmed=False)

    assert not ok
    assert any("confirmation" in r or "consent" in r for r in reasons), reasons
    assert manager.verify_calls == [], "no venue proof without operator consent"
    build.assert_not_called()


# ── manager verification authority ──────────────────────────────────


def test_fresh_verification_proves_rest_and_order_stream(tmp_path) -> None:
    adapter = _FakeAdapter()
    manager = _manager_with_adapter(tmp_path, adapter)
    try:
        ok, reason = manager.verify_live_session("fyers", timeout_s=0.1)
        assert ok, reason
        assert adapter.connect_calls == 1
        assert adapter.verify_calls == 1

        # Keep the assertion window free of the background freshness job.
        manager._health_requested["fyers"] = time.monotonic()
        snap = manager.get_session_snapshot("fyers")
        assert snap["state"] == "CONNECTED"
        assert snap["auth_valid"] is True
        assert snap["last_verified"]
        assert snap["verification_stale"] is False
        assert snap["verified_age_s"] is not None
    finally:
        manager.stop_worker()


def test_order_stream_failure_is_written_to_system_state(tmp_path) -> None:
    adapter = _FakeAdapter(
        ws_result=(False, "order stream not connected within 0.1s (state: CONNECTING)")
    )
    manager = _manager_with_adapter(tmp_path, adapter)
    try:
        ok, reason = manager.verify_live_session("fyers", timeout_s=0.1)
        assert not ok
        assert "order stream not verified" in reason

        manager._health_requested["fyers"] = time.monotonic()
        snap = manager.get_session_snapshot("fyers")
        assert snap["state"] == "DEGRADED"
        assert snap["status_raw"] == "MARKET_DATA_NOT_READY"
        assert "order stream not verified" in snap["reason"]
    finally:
        manager.stop_worker()


def test_expired_auth_updates_system_and_live_readiness(tmp_path) -> None:
    adapter = _ExpiredAdapter(connect_error=RuntimeError("token expired"))
    manager = _manager_with_adapter(tmp_path, adapter)
    try:
        ok, reason = manager.verify_live_session("fyers", timeout_s=0.1)
        assert not ok
        assert "session could not be verified" in reason

        manager._health_requested["fyers"] = time.monotonic()
        snap = manager.get_session_snapshot("fyers")
        assert snap["state"] == "LOGIN_REQUIRED"
        assert snap["auth_valid"] is False
        assert "session expired" in snap["reason"]
        assert manager.is_connected("fyers") is False

        svc = LiveTradingService(tmp_path / "live-data", tmp_path / "live-strategies")
        _attach(svc, manager)
        with patch.object(svc, "_resolve_live_venue_id", return_value="fyers-live"):
            blockers = svc._live_blockers()
        assert any("not authenticated" in b and "daily token expiry" in b for b in blockers), (
            blockers
        )
    finally:
        manager.stop_worker()


# ── bounded SYSTEM freshness ────────────────────────────────────────


def test_stale_connected_snapshot_queues_one_check_and_degrades(tmp_path) -> None:
    adapter = _FakeAdapter()
    manager = _manager_with_adapter(tmp_path, adapter)
    manager._unified["fyers"]["last_verified"] = "old"
    manager._unified["fyers"]["last_verified_epoch"] = (
        time.monotonic() - manager.VERIFICATION_STALE_AFTER_S - 5.0
    )
    submitted: list[tuple[str, str]] = []
    manager._worker.submit = lambda kind, broker_id: submitted.append((kind, broker_id))  # type: ignore[method-assign]
    try:
        first = manager.get_session_snapshot("fyers")
        assert first["verification_stale"] is True
        assert first["state"] == "CHECKING"
        assert first["auth_valid"] is False
        assert submitted == [("health-check", "fyers")]

        # A second snapshot inside the window must not enqueue another job.
        second = manager.get_session_snapshot("fyers")
        assert second["verification_stale"] is True
        assert submitted == [("health-check", "fyers")]
    finally:
        manager.stop_worker()


def test_queued_health_check_propagates_expired_token(tmp_path) -> None:
    adapter = _ExpiredAdapter()
    manager = _manager_with_adapter(tmp_path, adapter)
    manager._unified["fyers"]["last_verified"] = "old"
    manager._unified["fyers"]["last_verified_epoch"] = (
        time.monotonic() - manager.VERIFICATION_STALE_AFTER_S - 5.0
    )
    submitted: list[tuple[str, str]] = []
    manager._worker.submit = lambda kind, broker_id: submitted.append((kind, broker_id))  # type: ignore[method-assign]
    try:
        assert manager.get_session_snapshot("fyers")["state"] == "CHECKING"
        manager._job_health(SimpleNamespace(broker_id="fyers"))  # type: ignore[arg-type]
        snap = manager.get_session_snapshot("fyers")
        assert snap["state"] == "LOGIN_REQUIRED"
        assert snap["auth_valid"] is False
        assert manager.is_connected("fyers") is False
        # A terminal auth verdict must not keep hammering the venue each
        # freshness window; recovery is an explicit CONNECT/restart.
        manager._health_requested["fyers"] = 0.0
        manager.get_session_snapshot("fyers")
        assert submitted == [("health-check", "fyers")]
    finally:
        manager.stop_worker()
