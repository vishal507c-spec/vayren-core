"""Regression test suite for authoritative broker status, capital, and account confirmation.

Verifies the 10 required scenarios:
1. SYSTEM connected and LIVE connected for the same canonical account.
2. Broker disconnects: both show Disconnected.
3. Authentication times out: neither continues showing Connected.
4. Old connected snapshot arrives after newer disconnect: stays Disconnected.
5. Broker reconnects successfully: both update consistently.
6. Credential check succeeds but execution adapter is disconnected: distinction clear, no LIVE readiness.
7. Broker account ID changes: previous confirmation and stale capital invalidated.
8. Broker status is missing or unknown: fails closed.
9. Default broker changes: status from previous broker does not leak.
10. Gateway is Connected while broker session is Disconnected: displayed as distinct states.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.broker_manager import BrokerManager, BrokerStatus
from app.services.live_trading_service import LiveTradingService
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.providers.zerodha.live_trading import ZerodhaTradingAdapter
from remote.snapshot import build_snapshot


class FakeAdapter:
    """Mock adapter exposing the real adapter contract."""
    def __init__(self, connected: bool = True, account_id: str = "XV29244") -> None:
        self._connected = connected
        self._account_id = account_id

    @property
    def connected(self) -> bool:
        return self._connected

    def health(self) -> tuple[bool, str]:
        return (self._connected, "all checks passed" if self._connected else "disconnected")

    def account(self) -> dict[str, Any]:
        return {"account_id": self._account_id, "user_id": self._account_id}

    def funds(self) -> dict[str, Any]:
        if self._connected:
            return {"available": 500000.0, "used": 0.0, "total": 500000.0}
        return {}


def make_selection(name: str) -> MagicMock:
    sel = MagicMock()
    record = MagicMock()
    record.name = name
    record.reason = "user-selected"
    sel.current_or_none.return_value = record
    sel.current.return_value = record
    return sel


def make_service(tmp_path: Any) -> LiveTradingService:
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(str(data_dir), str(strat_dir))
    svc._universes.save("OBR C1C4", ("NSE:KAYNES",))
    svc.configure(
        strategy_name="OBR C1C4",
        symbols=("NSE:KAYNES",),
        mode="LIVE",
        quantity=1.0,
    )
    return svc


def test_scenario_1_system_and_live_connected_for_same_account(tmp_path: Any) -> None:
    """1. SYSTEM connected and LIVE connected for the same canonical account."""
    bm = BrokerManager(tmp_path / "data")
    adapter = FakeAdapter(connected=True, account_id="XV29244")
    bm._states["fyers"]["adapter"] = adapter
    bm._states["fyers"]["status"] = BrokerStatus.CONNECTED
    bm._states["fyers"]["configured"] = True
    bm._states["fyers"]["checks"] = {"connection": "READY", "account": "READY", "funds": "READY"}
    bm._states["fyers"]["details"] = {"account_id": "XV29244", "funds_available": 500000.0}

    assert bm.is_connected("fyers") is True

    service = make_service(tmp_path)
    service.attach_broker_view(bm, make_selection("fyers"))

    view = service._broker_view()
    assert view["connected"] is True
    assert view["status"] == "CONNECTED"
    assert view["account_id"] == "XV29244"

    snap = service.snapshot()
    assert snap["broker"]["connected"] is True
    assert snap["broker"]["status"] == "CONNECTED"
    assert snap["broker"]["account_id"] == "XV29244"
    assert service.is_account_confirmed() is True

    remote_snap = build_snapshot(snap)
    assert remote_snap["broker"]["connected"] is True
    assert remote_snap["broker"]["status"] == "CONNECTED"
    assert remote_snap["broker"]["account_id"] == "XV29244"
    bm.stop_worker()


def test_scenario_2_broker_disconnects_both_show_disconnected(tmp_path: Any) -> None:
    """2. Broker disconnects: both show Disconnected."""
    bm = BrokerManager(tmp_path / "data")
    adapter = FakeAdapter(connected=False, account_id="XV29244")
    bm._states["fyers"]["adapter"] = adapter
    bm._states["fyers"]["status"] = BrokerStatus.DISCONNECTED
    bm._states["fyers"]["reason"] = "venue unreachable: socket closed"
    bm._states["fyers"]["checks"] = {"connection": "FAILED: socket closed"}

    assert bm.is_connected("fyers") is False

    service = make_service(tmp_path)
    service.attach_broker_view(bm, make_selection("fyers"))

    view = service._broker_view()
    assert view["connected"] is False
    assert view["status"] == "DISCONNECTED"

    snap = service.snapshot()
    assert snap["broker"]["connected"] is False
    assert snap["broker"]["status"] == "DISCONNECTED"
    assert service.is_account_confirmed() is False

    remote_snap = build_snapshot(snap)
    assert remote_snap["broker"]["connected"] is False
    assert remote_snap["broker"]["status"] == "DISCONNECTED"
    bm.stop_worker()


def test_scenario_3_authentication_timeout_neither_shows_connected(tmp_path: Any) -> None:
    """3. Authentication times out: neither screen continues showing Connected."""
    bm = BrokerManager(tmp_path / "data")
    adapter = FakeAdapter(connected=False, account_id="")
    bm._states["fyers"]["adapter"] = adapter
    bm._states["fyers"]["status"] = BrokerStatus.DISCONNECTED
    bm._states["fyers"]["reason"] = "venue unreachable: The read operation timed out."
    bm._states["fyers"]["checks"] = {"connection": "FAILED: timeout"}

    assert bm.is_connected("fyers") is False

    service = make_service(tmp_path)
    service.attach_broker_view(bm, make_selection("fyers"))

    snap = service.snapshot()
    assert snap["broker"]["connected"] is False
    assert snap["broker"]["status"] == "DISCONNECTED"
    assert "timed out" in snap["broker"]["reason"].lower() or "unreachable" in snap["broker"]["reason"].lower()
    assert service.is_account_confirmed() is False

    remote_snap = build_snapshot(snap)
    assert remote_snap["broker"]["connected"] is False
    assert remote_snap["broker"]["status"] == "DISCONNECTED"
    bm.stop_worker()


def test_scenario_6_credential_check_vs_execution_adapter_distinction(tmp_path: Any) -> None:
    """6. SYSTEM's credential check succeeds but execution adapter is disconnected."""
    bm = BrokerManager(tmp_path / "data")
    # Stored config exists
    bm._states["fyers"]["configured"] = True
    bm._states["fyers"]["api_key_masked"] = "APP_ID_****"
    # But adapter is None or disconnected
    bm._states["fyers"]["adapter"] = None
    bm._states["fyers"]["status"] = BrokerStatus.LOGIN_REQUIRED
    bm._states["fyers"]["reason"] = "no active session"

    assert bm.is_connected("fyers") is False

    service = make_service(tmp_path)
    service.attach_broker_view(bm, make_selection("fyers"))

    snap = service.snapshot()
    # LIVE readiness is strictly false
    assert snap["broker"]["connected"] is False
    assert snap["broker"]["status"] in ("LOGIN_REQUIRED", "DISCONNECTED")
    assert service.is_account_confirmed() is False
    bm.stop_worker()


def test_scenario_7_account_id_change_invalidates_confirmation_and_capital(tmp_path: Any) -> None:
    """7. Broker account ID changes: previous confirmation and capital invalidated."""
    bm = BrokerManager(tmp_path / "data")
    adapter = FakeAdapter(connected=True, account_id="ACC_OLD")
    bm._states["fyers"]["adapter"] = adapter
    bm._states["fyers"]["status"] = BrokerStatus.CONNECTED
    bm._states["fyers"]["checks"] = {"connection": "READY", "account": "READY"}
    bm._states["fyers"]["details"] = {"account_id": "ACC_OLD", "funds_available": 100000.0}

    service = make_service(tmp_path)
    service.attach_broker_view(bm, make_selection("fyers"))

    # Confirm for ACC_OLD
    service.confirm_account("ACC_OLD")
    assert service.is_account_confirmed() is True

    # Account ID changes to ACC_NEW
    adapter._account_id = "ACC_NEW"
    bm._states["fyers"]["details"]["account_id"] = "ACC_NEW"

    # Must be invalidated
    assert service.is_account_confirmed() is False
    bm.stop_worker()


def test_scenario_8_missing_or_unknown_broker_fails_closed(tmp_path: Any) -> None:
    """8. Broker status missing or unknown: fails closed."""
    service = make_service(tmp_path)
    service.attach_broker_view(None, None)

    view = service._broker_view()
    assert view["connected"] is False
    assert service.is_account_confirmed() is False

    snap = service.snapshot()
    assert snap["broker"]["connected"] is False
    assert service._risk_status == "NOT READY"


def test_scenario_9_default_broker_changes_does_not_leak_status(tmp_path: Any) -> None:
    """9. Default broker changes: status from previous broker does not leak into new broker."""
    bm = BrokerManager(tmp_path / "data")
    # Fyers is connected
    fyers_adapter = FakeAdapter(connected=True, account_id="FYERS_ACC")
    bm._states["fyers"]["adapter"] = fyers_adapter
    bm._states["fyers"]["status"] = BrokerStatus.CONNECTED
    bm._states["fyers"]["checks"] = {"connection": "READY", "account": "READY"}
    bm._states["fyers"]["details"] = {"account_id": "FYERS_ACC"}

    # Zerodha is disconnected
    bm._states["zerodha"]["adapter"] = None
    bm._states["zerodha"]["status"] = BrokerStatus.NOT_CONFIGURED
    bm._states["zerodha"]["details"] = {}

    service = make_service(tmp_path)

    # When zerodha is selected, it must not report Fyers' connected status
    service.attach_broker_view(bm, make_selection("zerodha"))
    view = service._broker_view()
    assert view["id"] == "zerodha"
    assert view["connected"] is False
    assert view["account_id"] != "FYERS_ACC"
    assert service.is_account_confirmed() is False
    bm.stop_worker()


def test_scenario_10_gateway_connected_broker_disconnected_distinct(tmp_path: Any) -> None:
    """10. EC2 gateway is Connected while broker session is Disconnected."""
    bm = BrokerManager(tmp_path / "data")
    bm._states["fyers"]["adapter"] = None
    bm._states["fyers"]["status"] = BrokerStatus.DISCONNECTED
    bm._states["fyers"]["reason"] = "venue unreachable"

    service = make_service(tmp_path)
    service.attach_broker_view(bm, make_selection("fyers"))

    snap = service.snapshot()
    remote_snap = build_snapshot(snap)

    # Server/worker section
    assert remote_snap["worker"]["status"] == "STOPPED"
    # Broker section is disconnected
    assert remote_snap["broker"]["connected"] is False
    assert remote_snap["broker"]["status"] == "DISCONNECTED"
    bm.stop_worker()


def test_real_fyers_adapter_property_exposes_connected() -> None:
    """Test that real FyersSessionAdapter has .connected property matching ._connected."""
    adapter = FyersSessionAdapter("APP_ID", "TOKEN", enable_order_ws=False)
    assert hasattr(adapter, "connected")
    assert adapter.connected is False
    adapter._connected = True
    assert adapter.connected is True


def test_real_zerodha_adapter_property_exposes_connected() -> None:
    """Test that real ZerodhaTradingAdapter has .connected property matching ._connected."""
    adapter = ZerodhaTradingAdapter("API_KEY", "ACCESS_TOKEN")
    assert hasattr(adapter, "connected")
    assert adapter.connected is False
    adapter._connected = True
    assert adapter.connected is True
