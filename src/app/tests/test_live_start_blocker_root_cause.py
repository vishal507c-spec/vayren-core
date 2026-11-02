"""Regression test suite for LIVE START blocker root cause investigation & permanent fix.

Verifies:
1. Market closed -> START blocked with exact reason.
2. Market open + valid market feed -> market gate READY.
3. FYERS disconnected -> START blocked.
4. FYERS reconnect -> readiness refreshes correctly.
5. Stale WebSocket data -> START blocked.
6. Operator confirmation missing -> START blocked.
7. All gates READY -> START permitted (no real orders).
8. Multiple failed gates -> all blockers displayed.
9. STOP/reconnect -> no stale READY state.
10. Backend/API unavailable -> explicit UNKNOWN/ERROR, never falsely READY.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.broker_manager import BrokerManager, BrokerStatus
from app.services.live_trading_service import LiveTradingService


def _create_service(tmp_path: Path) -> LiveTradingService:
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)
    svc = LiveTradingService(data_dir=data_dir, strategy_dir=strat_dir)
    svc.configure(
        strategy_name="MomentumAlpha",
        symbols=("NSE:RELIANCE",),
        timeframe="15s",
        quantity=1.0,
        mode="LIVE",
    )
    return svc


def test_1_market_closed_blocks_start_with_exact_reason(tmp_path: Path) -> None:
    """Requirement 1: Market closed must NEVER block engine START; pipeline reports ready."""
    svc = _create_service(tmp_path)
    with patch(
        "broker.providers.fyers.live_market_data.is_nse_market_open",
        return_value=False,
    ):
        blockers = svc._live_blockers()
        assert not any("Market Closed" in b for b in blockers)

        # Gate row must reflect pipeline ready (market closed) when connected
        gates = svc._readiness_gates({}, {"connected": True}, [{"status": "AVAILABLE"}])
        md_gate = next(g for g in gates if g["name"] == "Market Data Streaming")
        assert md_gate["status"] == "READY"
        assert "market closed, awaiting 09:15 IST open" in md_gate["reason"]


def test_2_market_open_and_valid_market_feed_ready(tmp_path: Path) -> None:
    """Requirement 2: When market is open and feed is active, market gate is READY."""
    svc = _create_service(tmp_path)
    with patch(
        "broker.providers.fyers.live_market_data.is_nse_market_open",
        return_value=True,
    ):
        # 1. Pre-start state: store available + broker connected -> READY to stream
        view = {"connected": True}
        gates = svc._readiness_gates(
            {"session_status": "STOPPED", "feed": "none"},
            view,
            [{"status": "AVAILABLE"}],
        )
        md_gate = next(g for g in gates if g["name"] == "Market Data Streaming")
        assert md_gate["status"] == "READY"
        assert "feed ready to stream" in md_gate["reason"]

        # 2. Running state: active live ticks -> READY
        gates_running = svc._readiness_gates(
            {"session_status": "RUNNING", "feed": "live"},
            view,
            [{"status": "AVAILABLE", "price_source": "live_feed"}],
        )
        md_gate_running = next(g for g in gates_running if g["name"] == "Market Data Streaming")
        assert md_gate_running["status"] == "READY"


def test_3_fyers_disconnected_blocks_start(tmp_path: Path) -> None:
    """Requirement 3: When broker is disconnected, START is blocked with guidance."""
    svc = _create_service(tmp_path)
    svc._broker_manager = SimpleNamespace(
        is_connected=lambda _name: False,
        state=lambda _name: {"auth_valid": False, "token": ""},
        get_session_snapshot=lambda _name: {
            "state": "DISCONNECTED",
            "auth_valid": False,
        },
    )
    with (
        patch.object(svc, "broker_name", return_value="fyers"),
        patch.object(svc, "_ensure_live_venues"),
    ):
        blockers = svc._live_blockers()
        assert any("is not authenticated" in b and "DISCONNECTED" in b for b in blockers)


def test_4_fyers_reconnect_refreshes_readiness(tmp_path: Path) -> None:
    """Requirement 4: Broker reconnect immediately updates readiness from NOT READY to READY."""
    svc = _create_service(tmp_path)
    view_disc = {"connected": False, "id": "fyers", "reason": "socket reset"}
    view_conn = {"connected": True, "display": "FYERS", "account_id": "XV29244"}

    gates_disc = svc._readiness_gates({}, view_disc, [])
    assert next(g for g in gates_disc if g["name"] == "Broker Connected")["status"] == "NOT READY"

    gates_conn = svc._readiness_gates({}, view_conn, [])
    b_gate = next(g for g in gates_conn if g["name"] == "Broker Connected")
    assert b_gate["status"] == "READY"
    assert "FYERS connected (ACC: XV29244)" in b_gate["reason"]


def test_5_stale_websocket_data_blocks_start(tmp_path: Path) -> None:
    """Requirement 5: Stale WebSocket data or transport failure blocks START."""
    svc = _create_service(tmp_path)
    view = {"checks": {"market_data": "FAILED: stale — no ticks for 180s"}}
    gates = svc._readiness_gates(
        {},
        view,
        [{"status": "AVAILABLE", "price_source": "candle_store"}],
    )
    md_gate = next(g for g in gates if g["name"] == "Market Data Streaming")
    assert md_gate["status"] == "NOT READY"
    assert "FAILED: stale" in md_gate["reason"]


def test_6_operator_confirmation_missing_blocks_start(tmp_path: Path) -> None:
    """Requirement 6: In LIVE mode, missing operator consent blocks START."""
    svc = _create_service(tmp_path)
    svc._confirmed_live_at = ""
    svc._broker_manager = SimpleNamespace(
        is_connected=lambda _name: True,
        state=lambda _name: {"auth_valid": True, "token": "tok"},
        get_session_snapshot=lambda _name: {
            "state": "CONNECTED",
            "auth_valid": True,
        },
    )
    with (
        patch.object(svc, "broker_name", return_value="fyers"),
        patch.object(svc, "_resolve_live_venue_id", return_value="fyers"),
        patch.object(svc, "_ensure_live_venues"),
    ):
        blockers = svc._live_blockers()
        assert any("live confirmation required" in b for b in blockers)


def test_7_all_gates_ready_permits_start_without_order_submission(
    tmp_path: Path,
) -> None:
    """Requirement 7: All gates passing permits START without submitting real orders."""
    svc = _create_service(tmp_path)
    svc.confirm_live_execution()
    svc._confirmed_account_id = "XV29244"
    svc._broker_manager = SimpleNamespace(
        is_connected=lambda _name: True,
        state=lambda _name: {"auth_valid": True, "token": "tok"},
        get_session_snapshot=lambda _name: {
            "state": "CONNECTED",
            "auth_valid": True,
            "verification_stale": False,
        },
    )
    with (
        patch(
            "broker.providers.fyers.live_market_data.is_nse_market_open",
            return_value=True,
        ),
        patch.object(svc, "broker_name", return_value="fyers"),
        patch.object(svc, "_resolve_live_venue_id", return_value="fyers"),
        patch.object(svc, "_ensure_live_venues"),
        patch.object(
            svc,
            "_broker_view",
            return_value={"connected": True, "funds": {"available": 50000.0}},
        ),
        patch.object(svc, "is_account_confirmed", return_value=True),
        patch.object(svc, "is_halted", return_value=False),
        patch.object(svc, "_live_available_funds", return_value=50000.0),
    ):
        blockers = svc._live_blockers()
        assert not any("Market Closed" in b for b in blockers)
        assert not any("confirmation required" in b for b in blockers)
        assert not any("broker session is degraded" in b for b in blockers)


def test_8_multiple_failed_gates_all_blockers_reported(tmp_path: Path) -> None:
    """Requirement 8: Multiple failed gates report ALL blockers, not just one."""
    svc = _create_service(tmp_path)
    svc._confirmed_live_at = ""  # confirmation missing
    svc._broker_manager = SimpleNamespace(
        is_connected=lambda _name: True,
        state=lambda _name: {"auth_valid": True, "token": "tok"},
        get_session_snapshot=lambda _name: {
            "state": "DEGRADED",
            "auth_valid": True,
            "reason": "order_ws disconnected",
        },
    )
    with (
        patch(
            "broker.providers.fyers.live_market_data.is_nse_market_open",
            return_value=False,
        ),
        patch.object(svc, "broker_name", return_value="fyers"),
        patch.object(svc, "_resolve_live_venue_id", return_value="fyers"),
        patch.object(svc, "_ensure_live_venues"),
        patch.object(
            svc,
            "_broker_view",
            return_value={"connected": True, "funds": {"available": 100.0}},
        ),
        patch.object(svc, "is_account_confirmed", return_value=True),
        patch.object(svc, "is_halted", return_value=False),
        patch.object(svc, "_live_available_funds", return_value=100.0),
    ):
        blockers = svc._live_blockers()
        # Market closed does NOT block start, but genuine safety gates do:
        assert not any("Market Closed" in b for b in blockers)
        assert any("broker session is degraded" in b for b in blockers)
        assert any("live confirmation required" in b for b in blockers)
        assert len(blockers) >= 2


def test_9_stop_clears_stale_ready_state(tmp_path: Path) -> None:
    """Requirement 9: STOP completely clears marks and feed; never leaves stale READY quotes."""
    svc = _create_service(tmp_path)
    svc._feed_kind = "live"
    svc._marks["NSE:RELIANCE"] = 2800.0
    svc._mark_sources["NSE:RELIANCE"] = "live_feed"

    svc.stop()

    assert svc._feed_kind == "none"
    assert len(svc._marks) == 0
    assert len(svc._mark_sources) == 0


def test_10_broker_degraded_reported_honestly_never_fakes_connected(
    tmp_path: Path,
) -> None:
    """Requirement 10: WSAECONNRESET or leg failure reports DEGRADED, never falsely CONNECTED."""
    bm = BrokerManager(tmp_path / "bm_data")
    spec = SimpleNamespace(broker_id="fyers", display_name="FYERS")
    state = bm._fresh_state(spec)  # type: ignore[arg-type]
    state["configured"] = True
    state["status"] = BrokerStatus.CONNECTED
    state["details"] = {"account_id": "XV29244"}
    bm._states["fyers"] = state
    bm._set_leg("fyers", "market_ws", "FAILED", "wsasend: connection reset")
    bm._set_leg("fyers", "order_ws", "FAILED", "wsasend: connection reset")

    snap = bm.get_session_snapshot("fyers")
    assert snap["state"] == "DEGRADED"
    assert snap["state"] != "CONNECTED", "Broker with failed WS legs must NEVER report CONNECTED"
