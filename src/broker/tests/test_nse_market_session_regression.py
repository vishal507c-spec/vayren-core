"""Comprehensive Regression Test Suite for NSE Market Session Detection & Pipeline.

Tests all 9 critical scenarios:
1. NSE open at 09:15 IST.
2. Normal trading session during the day.
3. NSE close at 15:30 IST.
4. Weekends.
5. Official NSE holidays.
6. Special trading sessions (e.g. Diwali Muhurat trading).
7. Server running in UTC.
8. Stale WebSocket / disconnected market feed.
9. Frontend refresh and backend restart.
+ Safety Gate verification: Order execution blocked when market closed or unverified.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.services.live_trading_service import LiveTradingService
from broker.providers.fyers.live_market_data import (
    get_nse_market_session,
    is_nse_market_open,
)
from data.settings import NSE_HOLIDAYS, NSE_SPECIAL_SESSIONS
from execution.models.intent import ExecutionIntent
from execution.models.order import OrderPlan
from execution.modes import ExecutionMode, LiveArm
from execution.runtime.lifecycle import LifecycleState
from execution.runtime.session import LiveSession

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


class TestNseMarketSessionRegression:
    """Covers the 9 required test scenarios for NSE market session handling."""

    # Scenario 1: NSE open at exactly 09:15:00 IST
    def test_scenario_1_nse_open_at_0915_ist(self):
        # Monday 9 November 2026, 09:15:00 IST
        dt = datetime.datetime(2026, 11, 9, 9, 15, 0, tzinfo=IST)
        epoch = dt.timestamp()

        session_info = get_nse_market_session(epoch)
        assert session_info["session"] == "OPEN"
        assert session_info["is_open"] is True
        assert is_nse_market_open(epoch) is True
        assert "Regular Trading Session" in session_info["description"]

    # Scenario 2: Normal trading session during the day
    def test_scenario_2_normal_trading_session_during_day(self):
        # Monday 9 November 2026, 11:06:23 IST (the exact bug reproduction time)
        dt = datetime.datetime(2026, 11, 9, 11, 6, 23, tzinfo=IST)
        epoch = dt.timestamp()

        session_info = get_nse_market_session(epoch)
        assert session_info["session"] == "OPEN"
        assert session_info["is_open"] is True
        assert is_nse_market_open(epoch) is True
        assert "Regular Trading Session" in session_info["description"]

        # Afternoon trading session, 14:15:00 IST
        dt_afternoon = datetime.datetime(2026, 11, 9, 14, 15, 0, tzinfo=IST)
        assert is_nse_market_open(dt_afternoon.timestamp()) is True

    # Scenario 3: NSE close at 15:30 IST
    def test_scenario_3_nse_close_at_1530_ist(self):
        # Exactly 15:30:00 IST (at or after 15:30 is closed)
        dt_at_close = datetime.datetime(2026, 11, 9, 15, 30, 0, tzinfo=IST)
        epoch_at_close = dt_at_close.timestamp()

        session_info_at = get_nse_market_session(epoch_at_close)
        assert session_info_at["session"] == "CLOSED"
        assert session_info_at["is_open"] is False
        assert is_nse_market_open(epoch_at_close) is False
        assert "closed at 15:30 ist" in session_info_at["description"].lower()

        # Post market evening: 17:00:00 IST
        dt_evening = datetime.datetime(2026, 11, 9, 17, 0, 0, tzinfo=IST)
        session_info_ev = get_nse_market_session(dt_evening.timestamp())
        assert session_info_ev["session"] == "CLOSED"
        assert session_info_ev["is_open"] is False

    # Scenario 4: Weekends
    def test_scenario_4_weekends(self):
        # Saturday, 7 November 2026, 12:00:00 IST
        sat = datetime.datetime(2026, 11, 7, 12, 0, 0, tzinfo=IST)
        sat_info = get_nse_market_session(sat.timestamp())
        assert sat_info["session"] == "CLOSED"
        assert sat_info["is_open"] is False
        assert "Weekend: Saturday" in sat_info["description"]
        assert is_nse_market_open(sat.timestamp()) is False

        # Sunday, 15 November 2026, 12:00:00 IST (a regular Sunday without special session)
        sun = datetime.datetime(2026, 11, 15, 12, 0, 0, tzinfo=IST)
        sun_info = get_nse_market_session(sun.timestamp())
        assert sun_info["session"] == "CLOSED"
        assert sun_info["is_open"] is False
        assert "Weekend: Sunday" in sun_info["description"]
        assert is_nse_market_open(sun.timestamp()) is False

    # Scenario 5: Official NSE holidays
    def test_scenario_5_official_nse_holidays(self):
        # 1. Verify 9 November 2026 is NOT a holiday
        assert "2026-11-09" not in NSE_HOLIDAYS

        # 2. Tuesday, 10 November 2026 (Diwali Balipratipada) IS a holiday
        assert "2026-11-10" in NSE_HOLIDAYS
        tue_holiday = datetime.datetime(2026, 11, 10, 11, 0, 0, tzinfo=IST)
        hol_info = get_nse_market_session(tue_holiday.timestamp())
        assert hol_info["session"] == "HOLIDAY"
        assert hol_info["is_open"] is False
        assert "Exchange Holiday" in hol_info["description"]
        assert is_nse_market_open(tue_holiday.timestamp()) is False

        # 3. Tuesday, 24 November 2026 (Guru Nanak Jayanti) IS a holiday
        assert "2026-11-24" in NSE_HOLIDAYS
        guru_nanak = datetime.datetime(2026, 11, 24, 10, 30, 0, tzinfo=IST)
        assert is_nse_market_open(guru_nanak.timestamp()) is False
        assert get_nse_market_session(guru_nanak.timestamp())["session"] == "HOLIDAY"

        # 4. Republic Day: 2026-01-26
        rep_day = datetime.datetime(2026, 1, 26, 11, 0, 0, tzinfo=IST)
        assert is_nse_market_open(rep_day.timestamp()) is False
        assert get_nse_market_session(rep_day.timestamp())["session"] == "HOLIDAY"

    # Scenario 6: Special trading sessions
    def test_scenario_6_special_trading_sessions(self):
        # Sunday 8 November 2026 - Diwali Laxmi Pujan Muhurat Trading: 18:00 - 19:15 IST
        assert "2026-11-08" in NSE_SPECIAL_SESSIONS

        # A: During Muhurat session (18:30 IST) -> OPEN
        muhurat_open = datetime.datetime(2026, 11, 8, 18, 30, 0, tzinfo=IST)
        info_open = get_nse_market_session(muhurat_open.timestamp())
        assert info_open["session"] == "OPEN"
        assert info_open["is_open"] is True
        assert is_nse_market_open(muhurat_open.timestamp()) is True
        assert "Muhurat Trading" in info_open["description"]

        # B: Before Muhurat session on the same Sunday (15:00 IST) -> CLOSED
        muhurat_before = datetime.datetime(2026, 11, 8, 15, 0, 0, tzinfo=IST)
        info_before = get_nse_market_session(muhurat_before.timestamp())
        assert info_before["session"] == "CLOSED"
        assert info_before["is_open"] is False
        assert is_nse_market_open(muhurat_before.timestamp()) is False

        # C: After Muhurat session on the same Sunday (19:30 IST) -> CLOSED
        muhurat_after = datetime.datetime(2026, 11, 8, 19, 30, 0, tzinfo=IST)
        info_after = get_nse_market_session(muhurat_after.timestamp())
        assert info_after["session"] == "CLOSED"
        assert info_after["is_open"] is False
        assert is_nse_market_open(muhurat_after.timestamp()) is False

    # Scenario 7: Server running in UTC
    def test_scenario_7_server_running_in_utc(self):
        # When a server runs in UTC, the datetime is 2026-11-09 05:36:23 UTC
        # This matches 2026-11-09 11:06:23 IST (+05:30).
        utc_dt = datetime.datetime(2026, 11, 9, 5, 36, 23, tzinfo=UTC)
        epoch = utc_dt.timestamp()

        # The function must handle epoch irrespective of server local timezone
        session_info = get_nse_market_session(epoch)
        assert session_info["session"] == "OPEN"
        assert session_info["is_open"] is True
        assert is_nse_market_open(epoch) is True

        # Pre-open time in UTC: 03:35:00 UTC = 09:05:00 IST (between 09:00 and 09:15)
        utc_premarket = datetime.datetime(2026, 11, 9, 3, 35, 0, tzinfo=UTC)
        pre_info = get_nse_market_session(utc_premarket.timestamp())
        assert pre_info["session"] == "PRE_OPEN"
        assert pre_info["is_open"] is False
        assert "Pre-Open" in pre_info["description"]

        # UTC timestamp outside trading hours: 2026-11-09 12:00:00 UTC = 17:30:00 IST
        utc_post = datetime.datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
        assert is_nse_market_open(utc_post.timestamp()) is False

    # Scenario 8: Stale WebSocket / disconnected market feed
    def test_scenario_8_stale_websocket_disconnected_feed(self, tmp_path: Path):
        service = LiveTradingService(data_dir=tmp_path)

        # A: When WS disconnected during market hours
        service._broker_view = MagicMock(
            return_value={
                "id": "fyers",
                "display": "FYERS",
                "connected": False,
                "status": "DISCONNECTED",
                "reason": "link lost",
                "account_id": "FY12345",
                "configured": True,
            }
        )
        snap_disconnected = service.snapshot()
        assert snap_disconnected["websocket"]["status"] == "DISCONNECTED"
        assert snap_disconnected["websocket"]["market_session"] == "OPEN"

        # B: Connected but feed is not live -> STALE
        service._broker_view = MagicMock(
            return_value={
                "id": "fyers",
                "display": "FYERS",
                "connected": True,
                "status": "CONNECTED",
                "reason": "",
                "account_id": "FY12345",
                "configured": True,
            }
        )
        service._status = "RUNNING"
        service._worker_started = True
        service._timer = MagicMock()
        service._timer.is_running = True
        mock_sess_stale = MagicMock()
        mock_sess_stale.market_data_feed = "none"
        service._session = mock_sess_stale
        service._universe_quotes = MagicMock(return_value=[])

        snap_stale = service.snapshot()
        assert snap_stale["websocket"]["status"] == "CONNECTED"
        assert snap_stale["market_data"]["status"] == "STALE"

        # C: Fresh live ticks during market hours
        quotes = [{"symbol": "NSE:SBIN-EQ", "price_source": "live_feed", "close": 820.0}]
        service._universe_quotes = MagicMock(return_value=quotes)
        service._feed_kind = "live"
        mock_sess_live = MagicMock()
        mock_sess_live.market_data_feed = "live"
        service._session = mock_sess_live
        service._mark_times = {"NSE:SBIN-EQ": "11:06:23"}

        snap_fresh = service.snapshot()
        assert snap_fresh["websocket"]["status"] == "LIVE"
        assert snap_fresh["market_data"]["status"] == "STREAMING"
        assert "Regular Trading Session" in snap_fresh["websocket"]["session_desc"]

    # Scenario 9: Frontend refresh and backend restart
    def test_scenario_9_frontend_refresh_and_backend_restart(self, tmp_path: Path):
        # Simulated sequence of requests as frontend connects, disconnects, reloads
        service1 = LiveTradingService(data_dir=tmp_path)
        service1._broker_view = MagicMock(
            return_value={
                "id": "fyers",
                "display": "FYERS",
                "connected": True,
                "status": "CONNECTED",
                "reason": "",
                "account_id": "FY12345",
                "configured": True,
            }
        )
        service1._status = "RUNNING"
        service1._worker_started = True
        service1._timer = MagicMock()
        service1._timer.is_running = True
        service1._feed_kind = "live"
        service1._universe_quotes = MagicMock(
            return_value=[{"symbol": "NSE:RELIANCE-EQ", "price_source": "live_feed"}]
        )
        mock_session1 = MagicMock()
        mock_session1.market_data_feed = "live"
        service1._session = mock_session1

        snap1 = service1.snapshot()
        assert snap1["websocket"]["status"] == "LIVE"
        assert snap1["websocket"]["market_session"] == "OPEN"

        # Backend restart: create fresh service instance with new data_dir
        service2 = LiveTradingService(data_dir=tmp_path)
        service2._broker_view = MagicMock(
            return_value={
                "id": "fyers",
                "display": "FYERS",
                "connected": True,
                "status": "CONNECTED",
                "reason": "",
                "account_id": "FY12345",
                "configured": True,
            }
        )
        service2._status = "RUNNING"
        service2._worker_started = True
        service2._timer = MagicMock()
        service2._timer.is_running = True
        service2._feed_kind = "live"
        service2._universe_quotes = MagicMock(
            return_value=[{"symbol": "NSE:RELIANCE-EQ", "price_source": "live_feed"}]
        )
        mock_session2 = MagicMock()
        mock_session2.market_data_feed = "live"
        service2._session = mock_session2

        snap2 = service2.snapshot()
        assert snap2["websocket"]["status"] == "LIVE"
        assert snap2["websocket"]["market_session"] == "OPEN"
        assert snap2["websocket"]["session_desc"] == snap1["websocket"]["session_desc"]

    # Safety Gate verification: Order execution blocked when market closed or unverified
    def test_safety_gate_pre_order_check(self):
        # Set up a mock session with LIVE mode and ARMED state
        mock_broker = MagicMock()
        mock_broker.health.return_value = (True, "healthy")
        mock_broker.order_stream_health = None

        mock_session = MagicMock()
        mock_session._mode = ExecutionMode.LIVE
        mock_session._armed = LiveArm.ARMED
        mock_session._lifecycle = MagicMock()
        mock_session._lifecycle.state = LifecycleState.RUNNING
        mock_session._broker = mock_broker
        mock_session._normalizer = MagicMock()
        mock_session._normalizer.is_stale.return_value = False
        mock_session._reconciliation = MagicMock()
        mock_session._reconciliation.blocks_live = False
        mock_session._engine = MagicMock()
        mock_session._engine.by_intent.return_value = None
        mock_session._engine.open_orders.return_value = []

        intent = ExecutionIntent(
            intent_id="int-1",
            strategy_id="strat-1",
            strategy_version="v1",
            signal_id="sig-1",
            timestamp=datetime.datetime.now(IST),
            event_seq=1,
            symbol="NSE:SBIN-EQ",
            side="BUY",
            target_position_qty=10.0,
            quantity=10.0,
        )
        plan = OrderPlan(
            intent_id="int-1",
            symbol="NSE:SBIN-EQ",
            side="BUY",
            quantity=10.0,
            order_type="MARKET",
        )
        mock_session._sized_risk = {intent.intent_id: MagicMock()}
        mock_broker.order_margin = MagicMock(return_value=1000.0)
        mock_broker.funds = MagicMock(return_value={"available": 50000.0})
        mock_broker.capital = MagicMock(return_value=MagicMock(available_margin=50000.0))

        # 1. Closed market clock (post 15:30 IST) -> BLOCKED
        closed_epoch = datetime.datetime(2026, 11, 9, 16, 0, 0, tzinfo=IST).timestamp()
        mock_broker.now_epoch = MagicMock(return_value=closed_epoch)
        blocked_result = LiveSession._pre_order_checks(mock_session, intent, plan)
        assert blocked_result is not None
        assert blocked_result[0] == "ORDER_BLOCKED"
        assert "NSE market is not open" in blocked_result[1]

        # 2. Holiday clock (Diwali Balipratipada 10 Nov 2026) -> BLOCKED
        holiday_epoch = datetime.datetime(2026, 11, 10, 11, 0, 0, tzinfo=IST).timestamp()
        mock_broker.now_epoch = MagicMock(return_value=holiday_epoch)
        blocked_holiday = LiveSession._pre_order_checks(mock_session, intent, plan)
        assert blocked_holiday is not None
        assert blocked_holiday[0] == "ORDER_BLOCKED"
        assert "NSE market is not open" in blocked_holiday[1]

        # 3. Open market clock (9 Nov 2026 11:06 IST) -> Passes market open check
        open_epoch = datetime.datetime(2026, 11, 9, 11, 6, 23, tzinfo=IST).timestamp()
        mock_broker.now_epoch = MagicMock(return_value=open_epoch)

        pass_result = LiveSession._pre_order_checks(mock_session, intent, plan)
        assert pass_result is None
