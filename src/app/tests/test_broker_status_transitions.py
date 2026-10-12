"""Unit and regression tests for broker status transitions, timeout handling,
and gateway/broker separation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

from broker import BrokerStatus
from app.services.broker_manager import BrokerManager
from app.services.live_trading_service import LiveTradingService
import app.headless as headless
from remote.snapshot import build_snapshot


class DummyAdapter:
    def __init__(self, connected: bool = True):
        self._connected = connected

    @property
    def connected(self) -> bool:
        return self._connected


def test_is_connected_requires_adapter_and_ready_check(tmp_path: Path):
    """Cached or saved state with status=CONNECTED must not report is_connected=True
    without an active adapter and READY connection check."""
    bm = BrokerManager(str(tmp_path), specs={})
    bm._states["fyers"] = {
        "status": BrokerStatus.CONNECTED,
        "adapter": None,  # No active adapter
        "checks": {"connection": "PENDING"},
        "details": {},
        "configured": True,
    }

    # Merely having status == CONNECTED does not mean connected!
    assert bm.is_connected("fyers") is False

    # Adapter present but adapter.connected is False
    bm._states["fyers"]["adapter"] = DummyAdapter(connected=False)
    assert bm.is_connected("fyers") is False

    # Adapter connected, but connection check not READY
    bm._states["fyers"]["adapter"] = DummyAdapter(connected=True)
    bm._states["fyers"]["checks"] = {"connection": "PENDING"}
    assert bm.is_connected("fyers") is False

    # Only when all three conditions hold:
    bm._states["fyers"]["checks"] = {"connection": "READY"}
    assert bm.is_connected("fyers") is True
    bm._worker.stop()


def test_timeout_during_activation_sets_disconnected_with_unreachable_reason(tmp_path: Path):
    """When broker API raises read/socket timeout during activation,
    status must transition to DISCONNECTED with 'venue unreachable' reason,
    never masking as credential failure (LOGIN_REQUIRED)."""
    adapter = MagicMock()
    adapter.connect.side_effect = TimeoutError("The read operation timed out.")

    spec = SimpleNamespace(
        broker_id="fyers",
        display_name="FYERS",
        environment="LIVE",
        capabilities=[],
        key_field="api_key",
        build_adapter=MagicMock(return_value=adapter),
        create_auth_flow=MagicMock(),
        config_service="fyers_test",
        venue_id="fyers-live",
        venue_unregister=None,
    )
    bm = BrokerManager(str(tmp_path), specs={"fyers": spec})
    bm._activate(spec, {"api_key": "test_key"}, "test_token", "login ok")

    state = bm.state("fyers")
    assert state["status"] == BrokerStatus.DISCONNECTED
    assert "venue unreachable" in state["reason"].lower() or "timed out" in state["reason"].lower()
    assert state["status"] != BrokerStatus.LOGIN_REQUIRED
    assert bm.is_connected("fyers") is False
    bm._worker.stop()


def test_session_check_timeout_does_not_mask_as_expired_or_login_required(tmp_path: Path):
    """When background session check hits a venue read timeout, it must transition
    to DISCONNECTED with 'venue unreachable' reason without triggering auto-auth or
    marking session as expired."""
    flow = SimpleNamespace(
        validate=MagicMock(return_value=(False, "unreachable: The read operation timed out."))
    )
    session_store = SimpleNamespace(
        load_token=MagicMock(return_value={"access_token": "token123"})
    )

    spec = SimpleNamespace(
        broker_id="fyers",
        display_name="FYERS",
        environment="LIVE",
        capabilities=[],
        key_field="api_key",
        build_adapter=MagicMock(),
        build_flow=MagicMock(return_value=flow),
        config_service="fyers_test",
        venue_id="fyers-live",
        venue_unregister=None,
        auto_authenticate=None,
    )
    bm = BrokerManager(str(tmp_path), specs={"fyers": spec})
    bm._states["fyers"] = {
        "status": BrokerStatus.CONNECTED,
        "adapter": DummyAdapter(connected=True),
        "checks": {"connection": "READY"},
        "details": {},
        "configured": True,
        "reason": "",
    }
    bm._config_values = MagicMock(return_value={"api_key": "test_key"})
    bm._session_store = MagicMock(return_value=session_store)

    bm._job_session_check(spec)

    state = bm.state("fyers")
    assert state["status"] == BrokerStatus.DISCONNECTED
    assert "venue unreachable" in state["reason"].lower()
    assert bm.is_connected("fyers") is False
    bm._worker.stop()


def test_gateway_connected_broker_disconnected_snapshot_separation():
    """EC2 Gateway being CONNECTED does not mean Broker is connected.
    The snapshot must independently reflect broker connected status."""
    live = {
        "broker": {
            "name": "FYERS",
            "status": "DISCONNECTED",
            "connected": False,
        },
        "mode": "LIVE",
        "session_status": "STOPPED",
    }

    sections = build_snapshot(live)
    assert "broker" in sections
    broker_sec = sections["broker"]

    # When broker is disconnected, connected field must be False
    assert broker_sec.get("connected") is False
    assert broker_sec.get("status") == "DISCONNECTED"


def test_live_action_requires_explicit_consent(tmp_path: Path):
    """In LIVE mode, start without confirmed=True returns confirmation blocker."""
    service = headless._live_service(str(tmp_path), str(tmp_path))
    assert service is not None

    service.configure(strategy_name="OBR")
    service.configure(
        symbols=("NSE:RELIANCE",),
        timeframe="30m",
        quantity=10.0,
        mode="LIVE",
    )

    bar = SimpleNamespace(
        timestamp="2026-10-03T09:15:00",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
    )
    service._repository = SimpleNamespace(
        list_symbols=lambda: ["NSE:RELIANCE"],
        detect_timeframes=lambda _s: ("30m",),
        get_candles_timeframe=lambda _s, _tf, _n: [bar],
    )

    with (
        patch.object(LiveTradingService, "broker_name", return_value="FYERS"),
        patch.object(LiveTradingService, "_resolve_live_venue_id", return_value="FYERS-live"),
        patch.object(LiveTradingService, "_compiled", return_value=object()),
        patch("strategy.registry.get_strategy_registry") as reg,
        patch("app.services.live_trading_service.load_strategy_record") as load,
    ):
        reg.return_value.contains.return_value = False
        load.return_value = SimpleNamespace(
            id="OBR", name="OBR", code="x", created_at="", updated_at="", version="1"
        )

        ok, reasons = service.start(confirmed=False)
        assert not ok
        assert any("consent" in r or "confirmation" in r for r in reasons)

        with (
            patch("execution.modes.gates_from_env") as gates,
            patch.object(LiveTradingService, "_build_sessions", return_value=None),
        ):
            gates.return_value.missing.return_value = ()
            ok_conf, reasons_conf = service.start(confirmed=True)
            assert ok_conf
            assert not any("consent" in r or "confirmation" in r for r in reasons_conf)
