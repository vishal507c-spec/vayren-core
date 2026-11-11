"""Regression tests for the SYSTEM-layer critical fixes.

Pins the specific P0 defects:
- credential/session actions target the OPEN panel, not the persisted default;
- a broker switch never carries the previous broker's form values;
- saving credentials NEVER authenticates/connects;
- the connect handler never blocks the single command loop for 15s;
- remote credential saves go to the EC2 gateway's manager, never a local one;
- the SYSTEM snapshot's environment/health/log rows describe one broker.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import app.headless as headless  # noqa: E402
from app.services.broker_manager import BrokerManager  # noqa: E402
from app.services.broker_selection_service import (  # noqa: E402
    BrokerSelectionService,
    app_selection_store,
)
from broker.status import BrokerStatus  # noqa: E402


def _manager(
    brokers: list[dict[str, Any]],
    *,
    session: dict[str, Any] | None = None,
) -> MagicMock:
    manager = MagicMock(spec=BrokerManager)
    manager.snapshot.return_value = {
        "brokers": brokers,
        "active_broker": brokers[0]["id"] if brokers else "",
        "callback_url": "",
    }
    manager.get_session_snapshot.return_value = session or {}
    manager.state.return_value = {"status": BrokerStatus.DISCONNECTED}
    return manager


def _venue(
    bid: str,
    *,
    status: str = "DISCONNECTED",
    connected: bool = False,
    configured: bool = True,
) -> dict[str, Any]:
    return {
        "id": bid,
        "name": bid.upper(),
        "status": status,
        "connected": connected,
        "configured": configured,
        "reason": "",
        "checks": {},
        "credential_schema": [
            {"key": "api_key", "label": "API Key", "secret": False, "required": True},
            {"key": "secret", "label": "Secret", "secret": True, "required": True},
        ],
    }


# ── P0-1: one broker identity per action ───────────────────────────────────


def test_system_snapshot_uses_the_open_panel_for_all_facts(tmp_path: Path) -> None:
    service = BrokerSelectionService(app_selection_store(tmp_path))
    service.select("fyers")

    manager = _manager(
        [
            _venue("fyers", status="CONNECTED", connected=True),
            _venue("zerodha", status="DISCONNECTED", connected=False),
        ],
        session={"state": "DISCONNECTED", "reason": "not connected", "auth_valid": False},
    )

    snap = headless._system_snapshot(str(tmp_path), "zerodha", manager, str(tmp_path))

    assert snap["selected_id"] == "fyers", "default stays the selection"
    assert snap["panel_id"] == "zerodha"
    assert snap["status_raw"] == "DISCONNECTED", "panel facts, not the default's"
    assert snap["connected"] is False
    # The default's environment must never be painted on another broker's panel.
    assert snap["environment"] == ""

    default_panel = headless._system_snapshot(str(tmp_path), None, manager, str(tmp_path))
    assert default_panel["environment"] == "live"
    assert default_panel["status_raw"] in ("DISCONNECTED", "CONNECTED")


def test_connect_handler_targets_the_requested_broker(tmp_path: Path) -> None:
    manager = _manager([_venue("zerodha")])
    manager.connect_broker.return_value = (True, "connection started", {})

    headless._handle_connect_broker(
        {"broker_id": "zerodha", "credentials": {"api_key": "abc"}},
        str(tmp_path),
        manager,
    )

    manager.connect_broker.assert_called_once()
    assert manager.connect_broker.call_args.args[0] == "zerodha"


def test_connect_handler_refuses_an_empty_broker_id(tmp_path: Path) -> None:
    manager = _manager([_venue("zerodha")])
    snap = headless._handle_connect_broker(
        {"broker_id": "", "credentials": {"api_key": "abc"}}, str(tmp_path), manager
    )
    assert snap["status_raw"] == "ERROR"
    manager.connect_broker.assert_not_called()


# ── P0-3/P0-5: save is save-only ────────────────────────────────────────────


def test_save_credentials_never_connects(tmp_path: Path) -> None:
    manager = _manager([_venue("zerodha")])
    manager.save_credentials.return_value = (True, "configuration saved")

    snap = headless._handle_save_broker_credentials(
        {"broker_id": "zerodha", "credentials": {"api_key": "abc"}}, str(tmp_path), manager
    )

    manager.save_credentials.assert_called_once_with("zerodha", {"api_key": "abc"})
    manager.connect_broker.assert_not_called()
    assert snap["action_note"] == "configuration saved"


def test_save_credentials_reports_missing_backend(tmp_path: Path) -> None:
    snap = headless._handle_save_broker_credentials(
        {"broker_id": "zerodha", "credentials": {"api_key": "abc"}}, str(tmp_path), None
    )
    assert snap["status_raw"] == "ERROR"
    assert "unavailable" in snap["reason"]


# ── P1-12: no blocking wait in the command loop ─────────────────────────────


def test_connect_handler_does_not_sleep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda *_args: sleeps.append(1.0))
    manager = _manager([_venue("zerodha")])
    manager.connect_broker.return_value = (True, "connection started", {})
    manager.state.return_value = {"status": BrokerStatus.AUTHENTICATING}

    headless._handle_connect_broker(
        {"broker_id": "zerodha", "credentials": {}}, str(tmp_path), manager
    )

    assert sleeps == [], "the single command thread must never block on a settle wait"


# ── P2-15: health/log rows come from real backend facts ─────────────────────


def test_system_snapshot_emits_real_health_and_log_rows(tmp_path: Path) -> None:
    manager = _manager(
        [_venue("fyers", status="CONNECTED", connected=True)],
        session={
            "state": "CONNECTED",
            "reason": "session verified",
            "auth_valid": True,
            "next_action": "",
            "legs": [
                {"name": "market_ws", "status": "READY"},
                {"name": "order_ws", "status": "FAILED: timeout"},
            ],
        },
    )
    snap = headless._system_snapshot(str(tmp_path), "fyers", manager, str(tmp_path))

    health = {row["label"]: row for row in snap["health_rows"]}
    assert health["Broker Session"]["value"] == "CONNECTED"
    assert health["Broker Session"]["tone"] == 1
    assert health["order_ws"]["value"].startswith("FAILED")
    assert health["order_ws"]["tone"] == 3
    assert any("session verified" in row["text"] for row in snap["log_rows"])


# ── P1-10: ephemeral manager lifecycle ─────────────────────────────────────


def test_system_snapshot_ephemeral_manager_is_stopped_after_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeManager:
        def __init__(self, data_dir: str) -> None:
            self.data_dir = data_dir
            self.stopped = False

        def snapshot(self) -> dict[str, Any]:
            return {"brokers": [], "active_broker": "", "callback_url": ""}

        def get_session_snapshot(self, _broker_id: str) -> dict[str, Any]:
            return {}

        def stop_worker(self) -> None:
            self.stopped = True

    monkeypatch.setattr("app.services.broker_manager.BrokerManager", FakeManager)
    snap = headless._system_snapshot(str(tmp_path), None, None, str(tmp_path))
    assert snap["brokers"] == []


# ── P0-3 remote: credential saves reach the EC2 gateway ─────────────────────


def test_gateway_save_credentials_uses_manager_and_never_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remote.gateway import HeadlessGateway

    manager = _manager([_venue("zerodha")])
    manager.save_credentials.return_value = (True, "configuration saved")
    manager.get_session_snapshot.return_value = {"state": "LOGIN_REQUIRED"}

    gateway = HeadlessGateway(str(tmp_path), str(tmp_path))
    monkeypatch.setattr(gateway, "_ensure_manager", lambda: manager)
    monkeypatch.setattr(
        headless,
        "_trading_service_snapshot",
        lambda *_args, **_kwargs: {"session_status": "STOPPED"},
    )

    result = gateway.apply_command(
        {
            "action": "save_broker_credentials",
            "broker_id": "zerodha",
            "credentials": {"api_key": "abc"},
        }
    )

    manager.save_credentials.assert_called_once_with("zerodha", {"api_key": "abc"})
    manager.connect_broker.assert_not_called()
    assert result["action_ok"] is True
    assert result["action_note"] == "configuration saved"
