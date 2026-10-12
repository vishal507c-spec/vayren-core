"""Regression tests for ACCOUNT_CONFIRMED live gate and operator consent lifecycle.

Reproduces the exact screenshot scenario:
- Operator confirmation is TRUE, but broker account is unconfirmed -> reports ACCOUNT_CONFIRMED, drops consent blocker.
- Operator confirmation is TRUE, and broker account IS confirmed -> all gates pass.
- Operator confirmation is FALSE, and account is unconfirmed -> reports BOTH blockers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.live_trading_service import LiveTradingService


@pytest.fixture
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


def test_screenshot_scenario_operator_confirmed_true_account_unconfirmed(service: LiveTradingService) -> None:
    """When operator confirms live execution but the account is unconfirmed,

    start must be refused with 'live gate off: ACCOUNT_CONFIRMED', and the
    operator consent blocker must NOT be reported.
    """
    assert not service.is_account_confirmed()

    broker_view = {
        "id": "fyers",
        "display": "FYERS",
        "connected": False,
        "status": "DISCONNECTED",
        "reason": "",
        "account_id": "",
        "configured": True,
        "funds": {},
        "last_sync": "",
    }

    with patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"), \
         patch.object(service, "_broker_view", return_value=broker_view), \
         patch.object(service, "broker_name", return_value="fyers"), \
         patch.object(service, "available_symbols", return_value=("NSE:KAYNES",)), \
         patch.object(service, "available_timeframes", return_value=("5m",)), \
         patch.object(service, "_compiled", return_value=None), \
         patch.object(service, "_universe_quotes", return_value=[{"symbol": "NSE:KAYNES", "status": "AVAILABLE"}]):

        ok, blockers = service.start(confirmed=True)
        assert not ok
        # ACCOUNT_CONFIRMED must be reported
        assert any("ACCOUNT_CONFIRMED" in b for b in blockers)
        # Operator consent must NOT be reported since confirmed=True was passed
        assert not any("consent" in b or "confirmation required" in b for b in blockers)
        # The service recorded the operator consent
        assert service.is_live_confirmed()

        # Subsequent snapshot must maintain this honest state:
        # Operator consent is retained, remaining blocker is ONLY ACCOUNT_CONFIRMED
        snap_blockers = service.snapshot()["start_blockers"]
        assert any("ACCOUNT_CONFIRMED" in b for b in snap_blockers)
        assert not any("consent" in b or "confirmation required" in b for b in snap_blockers)


def test_operator_confirmed_true_and_account_confirmed(service: LiveTradingService) -> None:
    """When operator confirms live execution AND account is legitimately confirmed,

    both gates pass and start is not blocked by either.
    """
    broker_view = {
        "id": "fyers",
        "display": "FYERS",
        "connected": True,
        "status": "CONNECTED",
        "reason": "session verified",
        "account_id": "FY12345",
        "configured": True,
        "funds": {"available": 500000.0, "used": 0.0, "total": 500000.0},
        "last_sync": "",
    }

    with patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"), \
         patch.object(service, "_broker_view", return_value=broker_view), \
         patch.object(service, "broker_name", return_value="fyers"), \
         patch.object(service, "available_symbols", return_value=("NSE:KAYNES",)), \
         patch.object(service, "available_timeframes", return_value=("5m",)), \
         patch.object(service, "_compiled", return_value=None), \
         patch.object(service, "_universe_quotes", return_value=[{"symbol": "NSE:KAYNES", "status": "AVAILABLE"}]), \
         patch.object(service, "_live_available_funds", return_value=500000.0), \
         patch.object(service, "_build_sessions"):

        assert service.is_account_confirmed()
        ok, blockers = service.start(confirmed=True)
        assert ok
        assert not blockers
        assert service.is_live_confirmed()


def test_operator_unconfirmed_and_account_unconfirmed(service: LiveTradingService) -> None:
    """When operator has NOT confirmed and account is unconfirmed,

    BOTH blockers are legitimately reported.
    """
    broker_view = {
        "id": "fyers",
        "display": "FYERS",
        "connected": False,
        "status": "DISCONNECTED",
        "reason": "",
        "account_id": "",
        "configured": True,
        "funds": {},
        "last_sync": "",
    }

    with patch.object(service, "_resolve_live_venue_id", return_value="fyers-live"), \
         patch.object(service, "_broker_view", return_value=broker_view), \
         patch.object(service, "broker_name", return_value="fyers"), \
         patch.object(service, "available_symbols", return_value=("NSE:KAYNES",)), \
         patch.object(service, "available_timeframes", return_value=("5m",)), \
         patch.object(service, "_compiled", return_value=None), \
         patch.object(service, "_universe_quotes", return_value=[{"symbol": "NSE:KAYNES", "status": "AVAILABLE"}]):

        assert not service.is_account_confirmed()
        assert not service.is_live_confirmed()

        ok, blockers = service.start(confirmed=False)
        assert not ok
        assert any("ACCOUNT_CONFIRMED" in b for b in blockers)
        assert any("consent" in b or "confirmation required" in b for b in blockers)


def test_manual_account_confirmation_workflow(service: LiveTradingService) -> None:
    """Test explicit account-confirmation workflow (confirm_account / clear_account_confirmation)."""
    assert not service.is_account_confirmed()

    service.confirm_account("OPERATOR_ACC_99")
    assert service.is_account_confirmed()

    service.clear_account_confirmation()
    assert not service.is_account_confirmed()
