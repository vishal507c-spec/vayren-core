"""Tests for broker-agnostic capital display in Live trading UI."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.broker_manager import BrokerManager  # noqa: E402
from app.services.live_trading_service import LiveTradingService  # noqa: E402


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    headless._ARMED[0] = False
    return str(tmp_path), str(tmp_path)


def test_live_capital_block_with_connected_fyers_funds(tmp_path: Path) -> None:
    svc = LiveTradingService(data_dir=tmp_path)
    svc._broker_manager = MagicMock(spec=BrokerManager)
    svc._broker_manager.snapshot.return_value = {
        "active_broker": "fyers",
        "brokers": [
            {
                "id": "fyers",
                "display_name": "Fyers",
                "status": "connected",
                "connected": True,
                "session_active": True,
                "funds": {
                    "available_margin": 540250.0,
                    "used_margin": 12000.0,
                    "currency": "INR",
                },
                "last_sync": "2026-10-11T10:15:30Z",
            }
        ],
    }

    snap = svc.snapshot()
    cap = snap["capital"]

    assert cap["broker_name"] == "FYERS"
    assert cap["available_margin"] == 540250.0
    assert cap["currency"] == "INR"
    assert cap["status"] == "CONNECTED"
    assert cap["label"] == "Available margin"
    assert "10:15:30" in cap["last_updated"]


def test_live_capital_block_with_zerodha_zero_margin(tmp_path: Path) -> None:
    svc = LiveTradingService(data_dir=tmp_path)
    svc._broker_manager = MagicMock(spec=BrokerManager)
    svc._broker_manager.snapshot.return_value = {
        "active_broker": "zerodha",
        "brokers": [
            {
                "id": "zerodha",
                "display_name": "Zerodha",
                "status": "connected",
                "connected": True,
                "session_active": True,
                "funds": {
                    "available_margin": 0.0,
                    "used_margin": 0.0,
                    "currency": "INR",
                },
                "last_sync": "11:20:00",
            }
        ],
    }

    snap = svc.snapshot()
    cap = snap["capital"]

    assert cap["broker_name"] == "ZERODHA"
    assert cap["available_margin"] == 0.0
    assert cap["status"] == "CONNECTED"
    assert cap["last_updated"] == "11:20:00"


def test_live_capital_block_disconnected_broker(tmp_path: Path) -> None:
    svc = LiveTradingService(data_dir=tmp_path)
    svc._broker_manager = MagicMock(spec=BrokerManager)
    svc._broker_manager.snapshot.return_value = {
        "active_broker": "fyers",
        "brokers": [
            {
                "id": "fyers",
                "display_name": "Fyers",
                "status": "disconnected",
                "connected": False,
                "session_active": False,
                "funds": None,
            }
        ],
    }

    snap = svc.snapshot()
    cap = snap["capital"]

    assert cap["broker_name"] == "FYERS"
    assert cap["available_margin"] is None
    assert cap["status"] == "DISCONNECTED"


def test_live_capital_block_login_required(tmp_path: Path) -> None:
    svc = LiveTradingService(data_dir=tmp_path)
    svc._broker_manager = MagicMock(spec=BrokerManager)
    svc._broker_manager.snapshot.return_value = {
        "active_broker": "zerodha",
        "brokers": [
            {
                "id": "zerodha",
                "display_name": "Zerodha",
                "status": "session_expired",
                "connected": False,
                "session_active": False,
                "funds": None,
            }
        ],
    }

    snap = svc.snapshot()
    cap = snap["capital"]

    assert cap["broker_name"] == "ZERODHA"
    assert cap["available_margin"] is None
    assert cap["status"] == "LOGIN REQUIRED"


def test_refresh_capital_live_action_triggers_broker_manager(dirs: tuple[str, str]) -> None:
    mock_mgr = MagicMock(spec=BrokerManager)
    mock_mgr.broker_ids.return_value = ["fyers"]
    mock_mgr.snapshot.return_value = {
        "active_broker": "fyers",
        "brokers": [
            {
                "id": "fyers",
                "display_name": "Fyers",
                "status": "connected",
                "connected": True,
                "funds": {"available_margin": 100000.0},
            }
        ],
    }

    snap = headless._live_action(
        dirs[0],
        dirs[1],
        {"action": "refresh_capital"},
        broker_manager=mock_mgr,
    )

    mock_mgr.refresh_funds.assert_called_with("fyers")
    assert snap["capital"]["broker_name"] == "FYERS"
    assert snap["capital"]["available_margin"] == 100000.0
    # A verified broker response may say refreshed…
    assert snap["action_note"] == "broker capital refreshed"


def test_refresh_capital_never_claims_funds_it_could_not_read(
    dirs: tuple[str, str],
) -> None:
    """No broker number in the answer => the note says so, never success."""
    mock_mgr = MagicMock(spec=BrokerManager)
    mock_mgr.broker_ids.return_value = ["fyers"]
    mock_mgr.snapshot.return_value = {
        "active_broker": "fyers",
        "brokers": [{"id": "fyers", "display_name": "Fyers", "connected": False}],
    }

    snap = headless._live_action(
        dirs[0],
        dirs[1],
        {"action": "refresh_capital"},
        broker_manager=mock_mgr,
    )

    assert snap["capital"]["available_margin"] is None
    assert "unavailable" in snap["action_note"]
    assert "no verified broker response" in snap["action_note"]


def test_refresh_capital_without_a_broker_says_so(dirs: tuple[str, str]) -> None:
    mock_mgr = MagicMock(spec=BrokerManager)
    mock_mgr.broker_ids.return_value = []
    mock_mgr.snapshot.return_value = {"active_broker": ""}
    # No venue resolvable at all: the service reports no broker, so the
    # handler takes the registry path (which is empty here).
    service = MagicMock()
    service._broker_view.return_value = {}
    service.broker_name.return_value = ""
    service.snapshot.return_value = {"capital": {"source": "configured", "available_margin": None}}
    service.is_live_confirmed.return_value = False

    with (
        patch.object(headless, "_live_service", return_value=service),
        patch.object(
            headless,
            "_trading_service_snapshot",
            return_value={"capital": {"source": "configured", "available_margin": None}},
        ),
    ):
        snap = headless._live_action(
            dirs[0],
            dirs[1],
            {"action": "refresh_capital"},
            broker_manager=mock_mgr,
        )

    mock_mgr.refresh_funds.assert_not_called()
    assert snap["action_note"] == "no broker configured — nothing to refresh"
