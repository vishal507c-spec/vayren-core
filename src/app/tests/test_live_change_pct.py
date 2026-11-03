"""Unit and integration tests for Today's Change % (CHG%) calculation in Strategy Watchlist.

Expected Calculation:
Change % = ((Current LTP - Previous Trading Day Close) / Previous Trading Day Close) * 100

Verifies:
1. Retrieval of the authoritative previous trading day's official close from SQLite
   (handling weekends & holidays).
2. Calculation handles positive (+5.00%), negative (-5.00%), and zero (0.00%) changes accurately.
3. Missing (None) or zero previous closes safely yield None (rendered as "—" in UI).
4. FYERS tick parser extracts `prev_close` from raw venue messages.
5. BrokerFeedProvider records and exposes `prev_close` mapping.
6. LiveTradingService integrates previous close and recalculates change % dynamically on ticks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.live_trading_service import LiveTradingService
from app.services.market_data_service import MarketDataService
from broker.providers.fyers.live_market_data import FyersLiveMarketData
from execution.market_data.broker_feed import BrokerFeedProvider


def _create_mock_ohlcv_db(db_path: Path) -> None:
    """Create an SQLite market store with two trading days of 30m candles."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE ohlcv (
            candle_time TEXT PRIMARY KEY,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL
        )
        """
    )
    # Day 1: Friday 2026-10-30 (Previous Trading Day)
    # Last candle of the day: 15:15:00 with close = 1000.0
    candles = [
        ("2026-10-30 09:15:00", 990.0, 995.0, 985.0, 992.0, 5000),
        ("2026-10-30 14:45:00", 992.0, 1005.0, 990.0, 998.0, 12000),
        ("2026-10-30 15:15:00", 998.0, 1002.0, 995.0, 1000.0, 15000),
        # Day 2: Monday 2026-11-02 (Current Trading Day)
        ("2026-11-02 09:15:00", 1010.0, 1020.0, 1005.0, 1015.0, 8000),
        ("2026-11-02 09:45:00", 1015.0, 1030.0, 1012.0, 1025.0, 9000),
    ]
    con.executemany("INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)", candles)
    con.commit()
    con.close()


def test_get_previous_close_from_sqlite(tmp_path: Path) -> None:
    """Verify get_previous_close retrieves Friday's official close when querying as_of Monday."""
    data_dir = tmp_path / "data"
    _create_mock_ohlcv_db(data_dir / "KAYNES.db")

    mds = MarketDataService(data_dir=data_dir)
    # Query as of Monday 2026-11-02
    prev_close = mds.get_previous_close("KAYNES", as_of="2026-11-02")
    assert prev_close == 1000.0, f"Expected Friday's last close 1000.0, got {prev_close}"

    # Verify get_quotes uses previous close for change_pct
    quotes = mds.get_quotes(["KAYNES"])
    assert len(quotes) == 1
    q = quotes[0]
    assert q.price == 1025.0
    assert q.prev_close == 1000.0
    # Change % = ((1025 - 1000) / 1000) * 100 = +2.50%
    assert q.change_pct == 2.50


def test_change_pct_positive_negative_zero(tmp_path: Path) -> None:
    """Verify calculation for positive (+5.00%), negative (-5.00%), and zero (0.00%) change."""
    svc = LiveTradingService(data_dir=tmp_path)

    # Positive: Prev Close = 1000.0, LTP = 1050.0 -> +5.00%
    svc.set_live_price("NSE:STOCK_POS", 1050.0, prev_close=1000.0)
    quotes = svc._universe_quotes(("NSE:STOCK_POS",))
    assert quotes[0]["ltp"] == 1050.0
    assert quotes[0]["prev_close"] == 1000.0
    assert quotes[0]["change_pct"] == 5.00

    # Negative: Prev Close = 1000.0, LTP = 950.0 -> -5.00%
    svc.set_live_price("NSE:STOCK_NEG", 950.0, prev_close=1000.0)
    quotes = svc._universe_quotes(("NSE:STOCK_NEG",))
    assert quotes[0]["ltp"] == 950.0
    assert quotes[0]["prev_close"] == 1000.0
    assert quotes[0]["change_pct"] == -5.00

    # Zero: Prev Close = 1000.0, LTP = 1000.0 -> 0.00%
    svc.set_live_price("NSE:STOCK_ZERO", 1000.0, prev_close=1000.0)
    quotes = svc._universe_quotes(("NSE:STOCK_ZERO",))
    assert quotes[0]["ltp"] == 1000.0
    assert quotes[0]["prev_close"] == 1000.0
    assert quotes[0]["change_pct"] == 0.00


def test_missing_and_zero_previous_close(tmp_path: Path) -> None:
    """Missing or non-positive previous close must yield change_pct=None (rendered as '—')."""
    svc = LiveTradingService(data_dir=tmp_path)

    # No prev_close provided
    svc.set_live_price("NSE:STOCK_NO_PC", 500.0)
    quotes = svc._universe_quotes(("NSE:STOCK_NO_PC",))
    assert quotes[0]["ltp"] == 500.0
    assert quotes[0]["change_pct"] is None

    # Zero prev_close provided
    svc.set_live_price("NSE:STOCK_ZERO_PC", 500.0, prev_close=0.0)
    quotes = svc._universe_quotes(("NSE:STOCK_ZERO_PC",))
    assert quotes[0]["ltp"] == 500.0
    assert quotes[0]["change_pct"] is None


def test_fyers_tick_normalization_extracts_prev_close() -> None:
    """Verify FyersLiveMarketData extracts prev_close from various FYERS message shapes."""
    fyers_md = FyersLiveMarketData(app_id="TEST_APP_ID", access_token="test_token")

    # Shape 1: Top-level prev_close_price
    msg1 = {
        "symbol": "NSE:KAYNES-EQ",
        "ltp": 3280.0,
        "prev_close_price": 3200.0,
        "timestamp": 1730520000,
    }
    norm1 = fyers_md.normalize_tick(msg1)
    assert len(norm1) == 1
    assert norm1[0]["price"] == 3280.0
    assert norm1[0]["prev_close"] == 3200.0

    # Shape 2: Nested under 'v' dict
    msg2 = {
        "symbol": "NSE:DREDGECORP-EQ",
        "ltp": 890.0,
        "v": {"prev_close_price": 900.0},
        "timestamp": 1730520000,
    }
    norm2 = fyers_md.normalize_tick(msg2)
    assert len(norm2) == 1
    assert norm2[0]["price"] == 890.0
    assert norm2[0]["prev_close"] == 900.0


def test_broker_feed_records_and_exposes_prev_close() -> None:
    """Verify BrokerFeedProvider records prev_close and exposes it via .prev_close property."""

    class MockHistory:
        def poll(self):
            return []

        def health(self):
            return True, "ok"

        def subscribe(self, s):
            pass

        def unsubscribe(self, s):
            pass

    feed = BrokerFeedProvider(MockHistory(), timeframe="30m")
    feed.open(("NSE:KAYNES",), "30m")

    # Ingest a quote with prev_close
    feed._ingest(
        {
            "symbol": "NSE:KAYNES-EQ",
            "price": 3281.0,
            "timestamp": "2026-11-02 09:30:00",
            "volume": 500,
            "prev_close": 3200.0,
        }
    )

    assert feed.last_close.get("NSE:KAYNES") == 3281.0
    assert feed.prev_close.get("NSE:KAYNES") == 3200.0

    feed.close()
    assert len(feed.prev_close) == 0
