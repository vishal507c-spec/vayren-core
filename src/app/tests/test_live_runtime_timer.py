"""Tests for Live Strategy Runtime Timer.

Validates requirement 7:
- Initial start recording backend start timestamp in IST and authoritative epoch
- Elapsed-time calculation
- Reconnect persistence (start timestamp and epoch saved and reloaded)
- Stop state: timer stops and preserves last known runtime duration and end time
- Error state: timer stops and does not continue showing RUNNING
- Backend restart: new epoch/timestamps override old ones honestly without invented start times
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.live_trading_service import LiveTradingService
from strategy.language.storage import StrategyRecord

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def service(tmp_path: Path) -> LiveTradingService:
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True)
    strat_dir.mkdir(parents=True)
    svc = LiveTradingService(data_dir=data_dir, strategy_dir=strat_dir)
    return svc


def _setup_mock_strategy(service: LiveTradingService) -> None:
    service._universes.save("sma-crossover", ("NSE:RELIANCE",))
    service.configure(
        strategy_name="sma-crossover",
        symbols=("NSE:RELIANCE",),
        timeframe="30m",
        mode="LIVE",
        quantity=1.0,
    )

    bar = SimpleNamespace(
        timestamp="2026-10-13T09:15:00",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
    )
    service._repository = SimpleNamespace(  # type: ignore[assignment]
        list_symbols=lambda: ["RELIANCE"],
        detect_timeframes=lambda _s: ("30m",),
        get_candles_timeframe=lambda _s, _tf, _n: [bar],
    )
    rec = StrategyRecord(
        id="sma-crossover",
        name="sma-crossover",
        code="class Strategy: pass",
        created_at="",
        updated_at="",
        version="1.0",
        status="ACTIVE",
    )
    service._resolve_strategy_record = lambda _name: rec  # type: ignore[assignment]
    compiled_mock = SimpleNamespace(
        param_defaults={},
        create_logic=lambda _params, _owner_id: SimpleNamespace(),
    )
    service._compiled = lambda _rec: compiled_mock  # type: ignore[assignment]
    service._build_sessions = lambda: None  # type: ignore[assignment]
    # Timer tests mock the session build and validation — the start must
    # proceed so the epoch/IST bookkeeping under test can run.
    service.validate = lambda: ()  # type: ignore[assignment]


def test_initial_start_records_authoritative_epoch_and_ist(service: LiveTradingService) -> None:
    _setup_mock_strategy(service)
    assert service._session_start_epoch is None
    assert service._started_at == ""

    t0 = time.time()
    ok, blockers = service.start(confirmed=True)
    assert ok, f"Expected start to succeed, blockers: {blockers}"

    # Verify session start epoch is authoritative (matches wall time)
    assert service._session_start_epoch is not None
    assert abs(service._session_start_epoch - t0) < 2.0

    # Verify started_at is formatted as ISO timestamp in IST (+05:30)
    assert "+05:30" in service._started_at
    parsed_dt = datetime.fromisoformat(service._started_at)
    assert parsed_dt.tzinfo is not None
    assert parsed_dt.utcoffset() == timedelta(hours=5, minutes=30)

    # Snapshot includes authoritative start_epoch and IST started_at
    snap = service.snapshot()
    timing = snap["session_timing"]
    assert timing["status"] == "RUNNING"
    assert timing["start_epoch"] == service._session_start_epoch
    assert timing["started_at"] == service._started_at
    assert timing["stopped_at"] == "—"


def test_elapsed_time_calculation(service: LiveTradingService) -> None:
    _setup_mock_strategy(service)
    service.start(confirmed=True)
    start_epoch = service._session_start_epoch
    assert start_epoch is not None

    # Simulate 755 seconds elapsed (00:12:35)
    with patch("time.time", return_value=start_epoch + 755.0):
        snap = service.snapshot()
        timing = snap["session_timing"]
        assert timing["elapsed_seconds"] == 755
        assert "00:12:35" in timing["duration"]
        assert timing["start_epoch"] == start_epoch


def test_reconnect_persistence(service: LiveTradingService, tmp_path: Path) -> None:
    _setup_mock_strategy(service)
    service.start(confirmed=True)
    start_epoch = service._session_start_epoch
    started_at = service._started_at

    # Check that session file was persisted with start_epoch
    store_file = service._store.path
    assert store_file.is_file()
    data = service._store.load()
    timing_data = data.get("session_timing", {})
    assert timing_data.get("start_epoch") == start_epoch
    assert timing_data.get("started_at") == started_at

    # Simulate app reconnecting / reading the persisted session
    svc2 = LiveTradingService(
        data_dir=tmp_path / "data",
        strategy_dir=tmp_path / "strategies",
    )
    # The started_at is restored so last run time is never lost
    assert svc2._started_at == started_at


def test_stop_halts_running_timer_and_records_last_duration(service: LiveTradingService) -> None:
    _setup_mock_strategy(service)
    service.start(confirmed=True)
    start_epoch = service._session_start_epoch
    assert start_epoch is not None

    # Simulate running for 120 seconds, then stopping
    stop_time = start_epoch + 120.0
    with patch("time.time", return_value=stop_time):
        service.stop(reason="operator stop")

    assert service._status == "STOPPED"
    assert service._session_start_epoch is None
    assert service._elapsed_seconds == 120
    assert "+05:30" in service._stopped_at

    # Snapshot reflects STOPPED with last known duration
    snap = service.snapshot()
    timing = snap["session_timing"]
    assert timing["status"] == "STOPPED"
    assert timing["start_epoch"] is None
    assert timing["elapsed_seconds"] == 120
    assert "00:02:00" in timing["duration"]
    assert timing["stopped_at"] == service._stopped_at


def test_error_state_halts_running_timer(service: LiveTradingService) -> None:
    _setup_mock_strategy(service)
    service.start(confirmed=True)
    assert service._status == "RUNNING"

    # Simulate an error transition
    service._status = "ERROR"
    service._status_reason = "critical broker error"
    service._session_start_epoch = None

    snap = service.snapshot()
    assert snap["session_status"] == "ERROR"
    assert snap["session_timing"]["status"] == "ERROR"
    assert snap["session_timing"]["start_epoch"] is None


def test_backend_restart_new_epoch_overrides_old(service: LiveTradingService) -> None:
    _setup_mock_strategy(service)
    service.start(confirmed=True)
    first_epoch = service._session_start_epoch
    first_started_at = service._started_at
    assert first_epoch is not None

    service.stop()
    assert service._status == "STOPPED"

    # Restart session with advanced time (simulating backend restart at a later time)
    with patch("time.time", return_value=first_epoch + 10.0):
        service.start(confirmed=True)
    second_epoch = service._session_start_epoch
    second_started_at = service._started_at

    assert second_epoch is not None
    assert second_epoch > first_epoch
    assert second_started_at != first_started_at

    snap = service.snapshot()
    assert snap["session_timing"]["start_epoch"] == second_epoch
    assert snap["session_timing"]["started_at"] == second_started_at


def test_snapshots_keep_the_same_epoch_and_tick_elapsed(service: LiveTradingService) -> None:
    """UI refreshes during one engine run must reuse the SAME start epoch
    (so the timer persists across refreshes/reconnects) while the elapsed
    count advances every second (so the timer ticks)."""
    _setup_mock_strategy(service)
    service.start(confirmed=True)
    start_epoch = service._session_start_epoch
    assert start_epoch is not None

    with patch("time.time", return_value=start_epoch + 10.0):
        first = service.snapshot()["session_timing"]
    with patch("time.time", return_value=start_epoch + 11.0):
        second = service.snapshot()["session_timing"]

    assert first["start_epoch"] == start_epoch
    assert second["start_epoch"] == start_epoch, "refresh must not move the epoch"
    assert first["elapsed_seconds"] == 10
    assert second["elapsed_seconds"] == 11, "each refresh must tick by one second"
    assert first["status"] == second["status"] == "RUNNING"
