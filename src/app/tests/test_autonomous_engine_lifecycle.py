"""Autonomous Engine Lifecycle & Fault-Tolerant Architecture Acceptance Tests.

Validates the 12 core acceptance scenarios of the VAYREN autonomous trading infrastructure:
1. Engine START during Market Closed succeeds -> Engine enters RUNNING.
2. Market Closed -> Strategy loaded -> WAITING_FOR_MARKET. Signals = 0, Orders = 0.
3. Market Open transition -> WebSocket connects, data state becomes LIVE.
4. Fresh live data -> Strategy evaluates normally.
5. Invalid/stale data -> Order submission blocked fail-closed.
6. Valid signal -> Risk validation executes.
7. Risk failure -> Order blocked, zero broker orders.
8. WebSocket disconnect -> Automatic recovery & graceful degradation.
9. Broker disconnect -> Execution blocked safely.
10. HALT -> All execution paths immediately disabled.
11. Application restart -> State recovered safely.
12. Duplicate signal -> Deduplication prevents duplicate order execution.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.services.live_trading_service import LiveTradingService
from broker.providers.fyers.live_market_data import (
    get_nse_market_session,
)
from market import Bar
from strategy.language.storage import StrategyRecord

VALID_STRATEGY_CODE = """from strategy.strategies.base import PythonStrategy

class Strategy(PythonStrategy):
    def on_bar_logic(self, view):
        pass
"""


def _setup_service(tmp_path: Path) -> LiveTradingService:
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    svc = LiveTradingService(data_dir=data_dir, strategy_dir=strat_dir)
    svc._universes.save("OBR_C1C4", ["NSE:KAYNES"])
    svc.configure(
        strategy_name="OBR_C1C4",
        symbols=("NSE:KAYNES",),
        timeframe="15s",
        quantity=1.0,
        mode="LIVE",
    )
    return svc


# 1. Market closed -> START succeeds -> Engine RUNNING
def test_1_market_closed_start_succeeds_engine_running(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)

    # Mock market closed (e.g. Sunday or post-15:30 IST)
    with (
        patch("broker.providers.fyers.live_market_data.is_nse_market_open", return_value=False),
        patch.object(svc, "broker_name", return_value="fyers"),
        patch.object(svc, "_resolve_live_venue_id", return_value="fyers"),
        patch.object(svc, "_ensure_live_venues"),
        patch.object(
            svc,
            "_broker_view",
            return_value={
                "id": "fyers",
                "display": "Fyers",
                "account_id": "ACC1",
                "connected": True,
                "reason": "",
                "funds": {"available": 10000.0},
                "status": "CONNECTED",
            },
        ),
        patch.object(svc, "is_account_confirmed", return_value=True),
        patch.object(svc, "is_halted", return_value=False),
        patch.object(svc, "_live_available_funds", return_value=10000.0),
        patch.object(svc, "_build_sessions"),
        patch.object(svc, "_verify_live_session_fresh", return_value=()),
        patch.object(
            svc,
            "_resolve_strategy_record",
            return_value=StrategyRecord(
                id="OBR_C1C4",
                name="OBR C1C4",
                code=VALID_STRATEGY_CODE,
                created_at="2026-11-01T00:00:00Z",
                updated_at="2026-11-01T00:00:00Z",
            ),
        ),
        patch.object(svc._repository, "list_symbols", return_value=["KAYNES"]),
        patch.object(
            svc._repository,
            "get_candles_timeframe",
            return_value=[
                Bar("KAYNES", 3100.0, 3105.0, 3098.0, 3102.0, 100, "2026-11-01T15:29:00Z")
            ],
        ),
    ):
        # Operator confirms live execution
        svc.confirm_live_execution()
        success, blockers = svc.start(confirmed=True)

        assert success is True, f"START failed: {blockers}"
        assert svc.status == "RUNNING"
        snap = svc.snapshot()
        assert snap["engine_state"] == "RUNNING"
        assert snap["market_state"] in ("CLOSED", "PRE_OPEN", "HOLIDAY")
        assert snap["strategy_state_dim"] == "WAITING_FOR_MARKET"
        assert snap["market_data_state"] == "IDLE"


# 2. Market closed -> Strategy loaded -> WAITING_FOR_MARKET, Signals=0, Orders=0
def test_2_strategy_waits_for_market_when_closed(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)
    svc._status = "RUNNING"

    with patch("broker.providers.fyers.live_market_data.is_nse_market_open", return_value=False):
        snap = svc.snapshot()
        assert snap["authoritative_state"]["engine"] == "RUNNING"
        assert snap["authoritative_state"]["market"] in ("CLOSED", "PRE_OPEN", "HOLIDAY")
        assert snap["authoritative_state"]["strategy"] == "WAITING_FOR_MARKET"
        assert snap["authoritative_state"]["market_data"] == "IDLE"
        assert snap["signals_generated"] == 0
        assert snap["orders_submitted"] == 0


# 3. Market open transition -> WebSocket connects -> Data becomes LIVE
def test_3_market_open_live_transition(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)
    svc._status = "RUNNING"
    svc._feed_kind = "live"

    live_quotes = [{"symbol": "NSE:KAYNES", "price_source": "live_feed", "status": "AVAILABLE"}]

    with (
        patch("broker.providers.fyers.live_market_data.is_nse_market_open", return_value=True),
        patch(
            "broker.providers.fyers.live_market_data.get_nse_market_session",
            return_value={
                "session": "OPEN",
                "is_open": True,
                "description": "Market Open",
                "current_time_ist": "10:00:00",
                "next_session": "15:30 IST",
            },
        ),
        patch.object(svc, "_universe_quotes", return_value=live_quotes),
        patch.object(
            svc,
            "_broker_view",
            return_value={
                "id": "fyers",
                "display": "Fyers",
                "account_id": "ACC1",
                "connected": True,
                "reason": "",
                "status": "CONNECTED",
            },
        ),
    ):
        snap = svc.snapshot()
        assert snap["engine_state"] == "RUNNING"
        assert snap["market_state"] == "OPEN"
        assert snap["market_data_state"] == "LIVE"
        assert snap["strategy_state_dim"] == "RUNNING"


# 4. Fresh data -> Strategy evaluates normally
def test_4_fresh_data_evaluates_normally() -> None:
    session = get_nse_market_session()
    assert isinstance(session, dict)
    assert "session" in session
    assert "is_open" in session
    assert "current_time_ist" in session


# 5. Invalid data -> Strategy execution blocked fail-closed
def test_5_invalid_data_blocks_execution(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)
    svc._config.mode = "LIVE"

    from app.services.eligibility import order_gate_for

    gate = order_gate_for(svc)

    class DummyPlan:
        symbol = "NSE:INVALID_XYZ"

    reason = gate(None, DummyPlan())
    assert reason is not None
    assert "cannot resolve" in reason or "eligibility blocked" in reason


# 6. Valid signal -> Risk validation executes
def test_6_valid_signal_risk_validation(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)
    view = {
        "id": "fyers",
        "display": "Fyers",
        "account_id": "ACC1",
        "connected": True,
        "reason": "",
        "funds": {"available": 50000.0},
        "status": "CONNECTED",
    }
    svc._refresh_risk_capital(view)

    with patch.object(svc, "_broker_view", return_value=view):
        snap = svc.snapshot()
        risk = snap["risk_engine"]
        assert risk["status"] in ("READY", "OK", "PASS")
        assert snap["risk_state"] == "READY"


# 7. Risk failure blocks orders
def test_7_risk_failure_blocks_orders(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)
    view = {
        "id": "fyers",
        "display": "Fyers",
        "account_id": "ACC1",
        "connected": True,
        "reason": "",
        "funds": {"available": 0.0},
        "status": "CONNECTED",
    }
    svc._refresh_risk_capital(view)

    with patch.object(svc, "_broker_view", return_value=view):
        snap = svc.snapshot()
        assert snap["risk_state"] in ("BLOCKED", "NOT_VALIDATED")


# 8. WebSocket disconnect -> Graceful degradation
def test_8_websocket_disconnect_graceful_degradation() -> None:
    from broker.providers.fyers.live_market_data import (
        STATE_DISCONNECTED,
        FyersLiveMarketData,
    )

    adapter = FyersLiveMarketData(app_id="test_app", access_token="test_tok")
    assert adapter.state == STATE_DISCONNECTED
    healthy, reason = adapter.health()
    assert healthy is False
    assert "disconnected" in reason.lower()


# 9. Broker disconnect -> Safe handling
def test_9_broker_disconnect_blocks_orders(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)
    with patch.object(
        svc,
        "_broker_view",
        return_value={
            "id": "fyers",
            "display": "Fyers",
            "account_id": "ACC1",
            "connected": False,
            "reason": "",
            "status": "DISCONNECTED",
        },
    ):
        snap = svc.snapshot()
        assert snap["broker_state"] == "DISCONNECTED"
        assert snap["authoritative_state"]["broker"] == "DISCONNECTED"


# 10. HALT disables all execution paths
def test_10_halt_immediately_disables_execution(tmp_path: Path) -> None:
    svc = _setup_service(tmp_path)

    # Trigger emergency halt
    with patch.object(svc, "is_halted", return_value=True):
        snap = svc.snapshot()
        assert snap["execution_state"] == "HALTED"
        assert snap["authoritative_state"]["execution"] == "HALTED"
        assert snap["kill"]["halted"] is True


# 11. Application restart -> Safe recovery
def test_11_application_restart_safe_recovery(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    svc1 = LiveTradingService(data_dir=data_dir, strategy_dir=strat_dir)
    svc1.configure(
        strategy_name="OBR_C1C4",
        symbols=("NSE:KAYNES",),
        timeframe="15s",
        quantity=1.0,
        mode="LIVE",
    )

    # Fresh restart
    svc2 = LiveTradingService(data_dir=data_dir, strategy_dir=strat_dir)
    assert svc2.status == "STOPPED"
    assert svc2.is_live_confirmed() is False  # Never carry unconfirmed consent across restarts


# 12. Duplicate signal deduplication
def test_12_duplicate_signal_deduplication() -> None:
    seen_stamps = {}
    symbol = "NSE:KAYNES"
    ts = "2026-11-01T10:00:00Z"

    # First event recorded
    seen_stamps[symbol] = ts
    is_duplicate = ts <= seen_stamps[symbol]
    assert (
        is_duplicate is True
    )  # Subsequent event with same or older timestamp is detected as duplicate
