"""System-wide bug hunt regression tests.

Verifies fixes for:
1. FYERS tick latency calculation from exchange timestamp.
2. LTP mark refresh without freeze for active sessions.
3. Market data price source dynamic truth (live_feed vs candle_store).
4. OBR C1C4 universe fallback to canonical 22 symbols.
5. OBR C1C4 stop loss resolution without INVALID_STOP_PRICE rejection.
6. True signal side logging in journal and watchlist (no hardcoded SELL).
7. Blocked signals counted in rejected signals and orders count.
8. Remote order stream degradation when order_ws leg fails.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.services.live_trading_service import LiveTradingService
from broker.providers.fyers.live_market_data import FyersLiveMarketData
from execution.models.contract import StrategyRuntimeContract
from execution.models.intent import StrategySignal
from execution.runtime.session import LiveSession, StrategyContext
from execution.runtime.strategy_runtime import StrategyLifecycle
from remote.snapshot import build_snapshot
from strategy.registry import OBR_C1C4_SYMBOLS, get_strategy_registry


def test_fyers_tick_latency_calculation() -> None:
    """Verify that normalize_tick provides exchange_timestamp and computes real latency."""
    feed = FyersLiveMarketData(app_id="test_app", access_token="test_tok")
    now_epoch = time.time()
    raw_msg = {
        "symbol": "NSE:SBIN-EQ",
        "ltp": 820.50,
        "exch_feed_time": now_epoch - 0.045,  # 45ms ago
        "vol_traded_today": 10000,
    }
    quotes = feed.normalize_tick(raw_msg)
    assert len(quotes) == 1
    assert "exchange_timestamp" in quotes[0]
    assert quotes[0]["exchange_timestamp"] == pytest.approx(now_epoch - 0.045, abs=0.01)

    # Ingest event and verify _last_latency_ms is updated
    feed._on_message(raw_msg)
    assert feed.last_latency_ms is not None
    assert feed.last_latency_ms >= 40.0  # at least ~45ms latency calculated


def test_live_trading_service_marks_refresh_without_freeze(tmp_path) -> None:
    """Verify that _universe_quotes refreshes marks from session provider on each call."""
    service = LiveTradingService(data_dir=tmp_path)
    symbol = "NSE:KAYNES"
    mock_session = MagicMock()
    mock_provider = MagicMock()
    mock_provider.last_close = {symbol: 1250.0}
    mock_provider.last_tick_time = {symbol: "2026-10-31T10:15:00+05:30"}
    mock_session._provider = mock_provider
    mock_session.ledger.position.return_value.flat = True
    service._sessions[symbol] = mock_session

    # First call pulls 1250.0
    quotes1 = service._universe_quotes((symbol,))
    assert len(quotes1) == 1
    assert quotes1[0]["ltp"] == 1250.0
    assert quotes1[0]["price_source"] == "live_feed"

    # Provider updates close to 1255.0
    mock_provider.last_close[symbol] = 1255.0
    quotes2 = service._universe_quotes((symbol,))
    assert quotes2[0]["ltp"] == 1255.0  # Must not freeze at 1250.0!


def test_live_trading_market_data_price_source_truth(tmp_path) -> None:
    """Verify market_data price_source dynamically reflects live_feed vs candle_store."""
    service = LiveTradingService(data_dir=tmp_path)
    service._status = "RUNNING"
    service._marks["NSE:KAYNES"] = 1250.0
    service._mark_times["NSE:KAYNES"] = "2026-10-31T10:15:00+05:30"
    service._config.symbols = ("NSE:KAYNES",)
    service._feed_kind = "live"

    snap = service.snapshot()
    assert snap["market_data"]["price_source"] == "live_feed"
    assert snap["market_data"]["last_tick_time"] == "2026-10-31T10:15:00+05:30"
    assert snap["market_data"]["freshness_age_s"] is not None


def test_obr_c1c4_canonical_universe_save_and_retrieve(tmp_path) -> None:
    """Verify OBR C1C4 canonical 22 symbols are properly saved and retrieved."""
    service = LiveTradingService(data_dir=tmp_path)
    service._universes.save("OBR C1C4", OBR_C1C4_SYMBOLS)
    symbols = service.available_symbols("OBR C1C4")
    assert len(symbols) == 22
    assert symbols == OBR_C1C4_SYMBOLS


def test_obr_c1c4_stop_loss_resolution() -> None:
    """Verify OBR C1C4 engine slPrice resolves correctly through _resolve_signal_stop."""
    reg = get_strategy_registry()
    defn = reg.get("OBR C1C4")
    logic = reg.factory(defn.kind)(defn.params)

    # Warmup and step logic to arm C1-C4 levels
    ctx = StrategyContext(
        strategy_id="OBR C1C4",
        strategy_version="1.0",
        logic=logic,
        params=defn.params,
        contract=StrategyRuntimeContract(
            strategy_id="OBR C1C4",
            strategy_version="1.0",
            instruments=OBR_C1C4_SYMBOLS,
        ),
        lifecycle=StrategyLifecycle(),
    )
    # Simulate engine has set slPrice
    logic.engine.slPrice = 1260.0
    signal = StrategySignal(
        signal_id="sig1",
        strategy_id="OBR C1C4",
        strategy_version="1.0",
        timestamp="2026-10-31T10:45:00+05:30",
        event_seq=1,
        symbol="NSE:KAYNES",
        side="SELL",
        price=1240.0,
        stop_loss=None,  # Strategy relies on engine stop
    )

    resolved_sl = LiveSession._resolve_signal_stop(None, signal, context=ctx)
    assert resolved_sl == 1260.0


def test_signal_side_logging_and_watchlist(tmp_path) -> None:
    """Verify BUY signal is not hardcoded to SELL in activity or watchlist."""
    service = LiveTradingService(data_dir=tmp_path)
    symbol = "NSE:KAYNES"
    service._marks[symbol] = 1250.0
    mock_session = MagicMock()
    entry = MagicMock()
    entry.kind = "SIGNAL_GENERATED"
    entry.timestamp = "2026-10-31T10:45:00+05:30"
    entry.payload = {"signal_id": "sig_buy_1", "side": "BUY"}
    mock_session.journal.entries = [entry]
    mock_session.ledger.position.return_value.flat = True
    service._sessions[symbol] = mock_session

    service._drain_activity()
    assert service._last_signal_side[symbol] == "BUY"

    # In watchlist quote, signal must show BUY, not hardcoded SELL
    quotes = service._universe_quotes((symbol,))
    assert quotes[0]["signal"] == "BUY"


def test_signals_blocked_counted_in_rejected_signals(tmp_path) -> None:
    """Verify signals blocked by risk or gates are counted in signals_rejected."""
    service = LiveTradingService(data_dir=tmp_path)
    symbol = "NSE:KAYNES"
    mock_session = MagicMock()
    e1 = MagicMock(kind="SIGNAL_GENERATED", timestamp="10:45:00", payload={"side": "SELL"})
    e2 = MagicMock(kind="RISK_DENIED", timestamp="10:45:01", payload={"reasons": ["test"]})
    mock_session.journal.entries = [e1, e2]
    mock_session.ledger.position.return_value.flat = True
    service._sessions[symbol] = mock_session
    service._config.symbols = (symbol,)

    service._drain_activity()
    assert service._signals_generated == 1
    assert service._signals_blocked == 1

    snap = service.snapshot()
    strat = snap["strategy"]
    assert strat["today_signals"] == 1
    assert strat["signals_rejected"] == 1
    assert strat["orders_rejected_count"] == 1


def test_order_stream_degraded_when_order_ws_failed() -> None:
    """Verify remote snapshot flags order_stream as DEGRADED when order_ws leg fails."""
    live_snap = {
        "broker": {"connected": True, "name": "FYERS"},
        "feed": "live",
        "broker_session": {
            "legs": [
                {"name": "rest", "state": "READY"},
                {"name": "order_ws", "state": "FAILED", "reason": "socket timeout"},
            ]
        },
    }
    remote_snap = build_snapshot(live_snap)
    assert remote_snap["order_stream"]["status"] == "DEGRADED"
    assert "socket timeout" in remote_snap["order_stream"]["detail"]
