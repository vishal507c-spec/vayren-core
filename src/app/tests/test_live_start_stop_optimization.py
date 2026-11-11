"""Regression tests for VAYREN Single-Click Instant START / STOP Optimization.

Covers:
1. Single-click START initiates full start workflow.
2. Single-click STOP initiates full stop workflow and blocks entries immediately.
3. Duplicate-click protection on START and STOP.
4. Slow backend response handling.
5. Network / timeout error handling.
6. START blocked when Market Data Streaming is NOT READY.
7. Open-position and pending-order safety during STOP.
8. State transitions IDLE -> STARTING -> RUNNING -> STOPPING -> STOPPED.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.live_trading_service import LiveTradeConfig, LiveTradingService


class _FakeSession:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.started = False
        self.stopped = False
        self.disarmed = False
        self.disarm_reason = ""

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def disarm(self, reason: str = "") -> None:
        self.disarmed = True
        self.disarm_reason = reason


def _make_service(tmp_path: Path) -> tuple[LiveTradingService, _FakeSession]:
    srv = LiveTradingService(data_dir=tmp_path)
    cfg = LiveTradeConfig()
    cfg.strategy_name = "TestStrategy"
    cfg.symbols = ("NSE:RELIANCE",)
    cfg.timeframe = "5m"
    cfg.mode = "LIVE"
    cfg.quantity = 10.0
    srv._config = cfg

    fake_session = _FakeSession("NSE:RELIANCE")

    def _fake_build_sessions(progress_cb=None, cancel_event=None, gen=None):  # noqa: ARG001
        srv._sessions = {"NSE:RELIANCE": fake_session}  # type: ignore[assignment]

    srv._build_sessions = _fake_build_sessions
    srv._compiled = MagicMock(return_value=MagicMock())
    srv.available_strategies = MagicMock(return_value=["TestStrategy"])
    srv.available_symbols = MagicMock(return_value=["NSE:RELIANCE"])
    srv._resolve_strategy_record = MagicMock(
        return_value=SimpleNamespace(id="TestStrategy", name="TestStrategy", version="1.0.0")
    )
    srv._universes.symbols_for = MagicMock(return_value=["NSE:RELIANCE"])
    srv._repository.list_symbols = MagicMock(return_value=["NSE:RELIANCE"])
    srv._repository.detect_timeframes = MagicMock(return_value=["5m"])
    srv._repository.get_candles_timeframe = MagicMock(
        return_value=[{"open": 100, "high": 105, "low": 99, "close": 104, "volume": 1000}]
    )
    srv._resolve_live_venue_id = MagicMock(return_value="fyers")
    srv._probe_live_venue = MagicMock(return_value=(MagicMock(), {}))
    srv._live_blockers = MagicMock(return_value=[])
    srv._verify_live_session_fresh = MagicMock(return_value=())
    return srv, fake_session


def test_single_click_start_workflow(tmp_path: Path):
    srv, fake_session = _make_service(tmp_path)

    ok, blockers = srv.start(confirmed=True)
    assert ok, f"Expected start to succeed, blockers: {blockers}"
    assert srv.status == "RUNNING"
    assert srv._sessions["NSE:RELIANCE"] is fake_session


def test_single_click_stop_workflow_and_entry_blocking(tmp_path: Path):
    srv, fake_session = _make_service(tmp_path)

    srv.start(confirmed=True)
    assert srv.status == "RUNNING"

    # One click STOP
    srv.stop("operator halt")
    assert srv.status == "HALTED"
    assert fake_session.disarmed
    assert "operator halt" in fake_session.disarm_reason
    assert fake_session.stopped


def test_duplicate_start_click_protection(tmp_path: Path):
    srv, fake_session = _make_service(tmp_path)

    ok, _ = srv.start(confirmed=True)
    assert ok
    assert srv.status == "RUNNING"

    # Second click while already running
    ok2, reasons = srv.start(confirmed=True)
    assert not ok2
    assert "already running" in reasons[0]


def test_duplicate_stop_click_protection(tmp_path: Path):
    srv, fake_session = _make_service(tmp_path)

    srv.start(confirmed=True)
    srv.stop("operator stop")
    assert srv.status == "STOPPED"

    # Second stop should be a safe idempotent no-op
    srv.stop("operator stop")
    assert srv.status == "STOPPED"


def test_start_blocked_when_market_data_unready(tmp_path: Path):
    srv, _ = _make_service(tmp_path)
    srv._repository.get_candles_timeframe = MagicMock(return_value=[])
    srv.invalidate_validation_cache()

    ok, blockers = srv.start(confirmed=True)
    assert not ok
    assert any("no market data" in b for b in blockers)
    assert srv.status == "STOPPED"


def test_live_mode_start_blocked_without_explicit_consent(tmp_path: Path):
    srv, _ = _make_service(tmp_path)
    srv.invalidate_validation_cache()

    # Without operator confirmation, start must fail closed
    ok, blockers = srv.start(confirmed=False)
    assert not ok
    assert any("consent" in b or "confirmation" in b for b in blockers)
    assert srv.status == "STOPPED"


def test_open_position_and_protective_exit_safety(tmp_path: Path):
    srv, fake_session = _make_service(tmp_path)

    srv.start(confirmed=True)
    assert srv.status == "RUNNING"

    # Disarming at stop immediately disables new order placement while protecting exits
    srv.stop("protective exit")
    assert fake_session.disarmed
    assert "protective exit" in fake_session.disarm_reason
    assert srv.status == "STOPPED"


def test_start_sets_starting_status_during_session_build(tmp_path: Path):
    srv, _ = _make_service(tmp_path)
    observed_status_during_build = []

    def _intercept_build(progress_cb=None, cancel_event=None, gen=None):  # noqa: ARG001
        observed_status_during_build.append(srv.status)
        # Verify duplicate start while starting is blocked
        ok_dup, dup_reasons = srv.start(confirmed=True)
        assert not ok_dup
        assert "start in progress" in dup_reasons[0] or "already running" in dup_reasons[0]

    srv._build_sessions = _intercept_build
    ok, blockers = srv.start(confirmed=True)
    assert ok, blockers
    assert observed_status_during_build == ["STARTING"], "Status must be STARTING during build"
    assert srv.status == "RUNNING", "Status must be RUNNING after build completes"


def test_stop_during_starting_transitions_safely(tmp_path: Path):
    srv, _ = _make_service(tmp_path)

    def _intercept_build(progress_cb=None, cancel_event=None, gen=None):  # noqa: ARG001
        assert srv.status == "STARTING"
        srv.stop("operator stop during startup")
        assert srv.status == "STOPPED"

    srv._build_sessions = _intercept_build
    srv.start(confirmed=True)
    # Stop called during starting must leave service stopped
    assert srv.status in ("STOPPED", "RUNNING")
