"""Permanent verification tests for VAYREN Critical LIVE START Button Bug.

Validates all 9 mandatory test scenarios from the specification:
1. First START click -> successful execution without requiring a second click.
2. Backend response delayed beyond 25 seconds -> UI discovers actual state without second click.
3. Gateway temporarily slow -> no duplicate execution or double session build.
4. Repeated START clicks -> only one active worker/session.
5. Backend disconnected -> clear error, no false RUNNING status.
6. Worker already running -> START behaves safely and idempotently.
7. BLOCKED readiness -> START remains blocked with exact reasons.
8. FYERS disconnected -> no live order execution.
9. Restart gateway -> correct state synchronization (STARTING / RUNNING / STOPPED).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import app.headless as headless
from app.services.live_trading_service import LiveTradeConfig, LiveTradingService
from remote.snapshot import build_snapshot


class _MockSession:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def _setup_service(tmp_path: Path) -> tuple[LiveTradingService, _MockSession]:
    srv = LiveTradingService(data_dir=tmp_path)
    cfg = LiveTradeConfig()
    cfg.strategy_name = "OBR"
    cfg.symbols = ("NSE:RELIANCE",)
    cfg.timeframe = "5m"
    cfg.mode = "LIVE"
    cfg.quantity = 10.0
    srv._config = cfg

    mock_session = _MockSession("NSE:RELIANCE")

    def _mock_build_sessions(progress_cb=None, cancel_event=None, gen=None):  # noqa: ARG001
        srv._sessions = {"NSE:RELIANCE": mock_session}  # type: ignore[assignment]

    srv._build_sessions = _mock_build_sessions
    srv._compiled = MagicMock(return_value=MagicMock())
    srv.available_strategies = MagicMock(return_value=["OBR"])
    srv.available_symbols = MagicMock(return_value=["NSE:RELIANCE"])
    srv._resolve_strategy_record = MagicMock(
        return_value=SimpleNamespace(id="OBR", name="OBR", version="1.0.0")
    )
    srv._universes.symbols_for = MagicMock(return_value=["NSE:RELIANCE"])
    srv._repository.list_symbols = MagicMock(return_value=["NSE:RELIANCE"])
    srv._repository.detect_timeframes = MagicMock(return_value=["5m"])
    srv._repository.get_candles_timeframe = MagicMock(
        return_value=[
            {
                "open": 100,
                "high": 105,
                "low": 99,
                "close": 104,
                "volume": 1000,
                "timestamp": "2026-11-08T09:15:00+05:30",
            }
        ]
    )
    srv._resolve_live_venue_id = MagicMock(return_value="fyers")
    srv._probe_live_venue = MagicMock(return_value=(MagicMock(), {}))
    srv._live_blockers = MagicMock(return_value=[])
    srv._verify_live_session_fresh = MagicMock(return_value=())
    return srv, mock_session


# ── Scenario 1: First START click -> successful execution ──
def test_scenario_1_first_start_click_success(tmp_path: Path):
    srv, session = _setup_service(tmp_path)
    # Operator confirms LIVE execution on first click
    ok, blockers = srv.start(confirmed=True)
    assert ok, f"First click must succeed, got blockers: {blockers}"
    assert srv.status == "RUNNING"
    assert "NSE:RELIANCE" in srv._sessions

    snap = srv.snapshot()
    assert snap["session_status"] == "RUNNING"
    strat = build_snapshot(snap)["strategy"]
    assert strat["status"] == "RUNNING"


# ── Scenario 2: Backend response delayed beyond 25 seconds -> UI discovers actual state ──
def test_scenario_2_delayed_backend_discovery(tmp_path: Path):
    srv, _ = _setup_service(tmp_path)

    # Simulate backend in STARTING state
    srv._status = "STARTING"
    srv._status_reason = "initializing live engine and sessions"
    snap_starting = srv.snapshot()
    assert snap_starting["session_status"] == "STARTING"

    proj = build_snapshot(snap_starting)
    assert proj["strategy"]["status"] == "STARTING"
    assert "initializing" in proj["strategy"]["reason"]

    # When backend finishes after delay
    srv._status = "RUNNING"
    srv._status_reason = ""
    snap_running = srv.snapshot()
    assert snap_running["session_status"] == "RUNNING"
    proj_running = build_snapshot(snap_running)
    assert proj_running["strategy"]["status"] == "RUNNING"


# ── Scenario 3: Gateway temporarily slow -> no duplicate execution ──
def test_scenario_3_gateway_slow_no_duplicate_execution(tmp_path: Path):
    srv, _ = _setup_service(tmp_path)
    build_count = 0

    def _counted_build(progress_cb=None, cancel_event=None, gen=None):  # noqa: ARG001
        nonlocal build_count
        build_count += 1
        srv._status = "RUNNING"

    srv._build_sessions = _counted_build

    # First start request
    ok, _ = srv.start(confirmed=True)
    assert ok
    assert build_count == 1

    # Second start request while running
    ok2, reasons = srv.start(confirmed=True)
    assert not ok2
    assert "already running" in reasons[0]
    assert build_count == 1, "Duplicate build must never occur"


# ── Scenario 4: Repeated START clicks -> only one active worker ──
def test_scenario_4_repeated_start_clicks_single_worker(tmp_path: Path, monkeypatch):
    srv, _ = _setup_service(tmp_path)
    monkeypatch.setattr(headless, "_live_service", lambda *_, **__: srv)

    # 1st click
    act = {"action": "start", "confirmed": True}
    res1 = headless._live_action(str(tmp_path), str(tmp_path), act)
    assert res1.get("session_status") == "RUNNING"
    assert res1.get("action_note") == "strategy started"

    # 2nd click: headless returns idempotent success
    res2 = headless._live_action(str(tmp_path), str(tmp_path), act)
    assert res2.get("session_status") == "RUNNING"
    assert res2.get("action_ok") is True
    assert res2.get("action_note") == "strategy already running"
    assert len(srv._sessions) == 1, "Must maintain exactly one session"


# ── Scenario 5: Backend disconnected -> clear error, no false RUNNING status ──
def test_scenario_5_backend_disconnected_clear_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(headless, "_live_service", lambda *_, **__: None)
    act = {"action": "start", "confirmed": True}
    res = headless._live_action(str(tmp_path), str(tmp_path), act)
    assert res.get("status") == "STOPPED"
    assert "trading backend unavailable" in res.get("error", "")
    assert res.get("session_status") != "RUNNING"


# ── Scenario 6: Worker already running -> START behaves safely ──
def test_scenario_6_worker_already_running_safe(tmp_path: Path, monkeypatch):
    srv, _ = _setup_service(tmp_path)
    monkeypatch.setattr(headless, "_live_service", lambda *_, **__: srv)

    srv.start(confirmed=True)
    assert srv.status == "RUNNING"

    # Applying START when already running
    act = {"action": "start", "confirmed": True}
    res = headless._live_action(str(tmp_path), str(tmp_path), act)
    assert res.get("session_status") == "RUNNING"
    assert res.get("action_ok") is True
    assert res.get("action_note") == "strategy already running"


# ── Scenario 7: BLOCKED readiness -> START remains blocked ──
def test_scenario_7_blocked_readiness_remains_blocked(tmp_path: Path):
    srv, _ = _setup_service(tmp_path)
    # Simulate missing data blocker
    srv._repository.get_candles_timeframe = MagicMock(return_value=[])
    srv.invalidate_validation_cache()

    ok, blockers = srv.start(confirmed=True)
    assert not ok
    assert srv.status == "STOPPED"
    assert any("no market data" in b for b in blockers)


# ── Scenario 8: FYERS disconnected -> no live order execution ──
def test_scenario_8_fyers_disconnected_no_execution(tmp_path: Path):
    srv, _ = _setup_service(tmp_path)
    # Simulate broker venue probe failure surfacing from the build step
    from app.services.live_trading_service import LiveConfigError

    def _failing_build(progress_cb=None, cancel_event=None, gen=None):  # noqa: ARG001
        raise LiveConfigError("fyers broker adapter is not connected")

    srv._build_sessions = _failing_build  # type: ignore[assignment]

    ok, blockers = srv.start(confirmed=True)
    assert not ok
    assert srv.status == "ERROR"
    assert any("not connected" in b for b in blockers)
    assert len(srv._sessions) == 0, "No live trading sessions or orders allowed"


# ── Scenario 9: Restart gateway -> correct state synchronization ──
def test_scenario_9_gateway_restart_state_sync(tmp_path: Path):
    srv, _ = _setup_service(tmp_path)
    srv.start(confirmed=True)
    assert srv.status == "RUNNING"

    # Gateway builds snapshot after restart
    snap = srv.snapshot()
    proj = build_snapshot(snap)
    assert proj["strategy"]["status"] == "RUNNING"
    assert proj["worker"]["status"] == "RUNNING"
    assert proj["worker"]["running"] is True
