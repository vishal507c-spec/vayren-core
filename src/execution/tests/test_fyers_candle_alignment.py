"""Comprehensive tests for FYERS timestamp parsing and 30-minute candle alignment.

Verifies:
1. UTC to IST timestamp conversion (epoch seconds/ms/us, UTC ISO strings).
2. Exactly-once conversion (no double conversion when already in IST or naive).
3. Every 30-minute candle boundary for the NSE session (09:15, 09:45 ... 15:15 IST).
4. Candle OHLCV accuracy across multi-tick ingestion.
5. Historical vs live candle alignment (watermark seeding, timestamp matching).
6. Duplicate, late, and out-of-order tick handling (defense against bucket corruption).
7. Strategy signal gating: only closed 30m candles trigger strategy evaluation.
8. NSE market opening (09:15:00) and closing (15:30:00) boundary handling.
"""

from __future__ import annotations

from typing import Any

from execution.events import CandleEvent
from execution.market_data.broker_feed import BrokerFeedProvider, _normalize_venue_stamp
from execution.runtime.strategy_runtime import LiveStrategyDriver, StrategyContext
from market import session_anchor_seconds
from strategy import BarView, StrategyParameters


class _MockQuoteFace:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.connected = True
        self.quotes_to_poll: list[dict[str, Any]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        self.subscribed.extend(symbols)

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        for s in symbols:
            if s in self.subscribed:
                self.subscribed.remove(s)

    def poll(self) -> list[dict[str, Any]]:
        q = list(self.quotes_to_poll)
        self.quotes_to_poll.clear()
        return q

    def health(self) -> tuple[bool, str]:
        return self.connected, "mock quote face ok"

    def close(self) -> None:
        self.connected = False


# ── 1. UTC to IST Conversion & Exactly-Once Semantics ────────────────────────


def test_normalize_venue_stamp_utc_converts_to_ist() -> None:
    # 03:45:00 UTC == 09:15:00 IST (NSE market open)
    assert _normalize_venue_stamp("2026-10-30T03:45:00Z") == "2026-10-30 09:15:00"
    assert _normalize_venue_stamp("2026-10-30T03:45:00+00:00") == "2026-10-30 09:15:00"

    # 04:15:00 UTC == 09:45:00 IST (C1 close)
    assert _normalize_venue_stamp("2026-10-30T04:15:00Z") == "2026-10-30 09:45:00"

    # Unix epoch seconds (1730260500 is 2024-10-30 03:55:00 UTC == 09:25:00 IST)
    assert _normalize_venue_stamp(1730260500) == "2024-10-30 09:25:00"
    assert _normalize_venue_stamp("1730260500") == "2024-10-30 09:25:00"

    # Unix epoch milliseconds (13 digits)
    assert _normalize_venue_stamp(1730260500000) == "2024-10-30 09:25:00"
    assert _normalize_venue_stamp("1730260500000") == "2024-10-30 09:25:00"


def test_normalize_venue_stamp_avoids_double_conversion() -> None:
    # Already IST aware (+05:30 offset) must NOT shift again
    assert _normalize_venue_stamp("2026-10-30T09:15:00+05:30") == "2026-10-30 09:15:00"

    # Naive wall-clock string: already venue local time, must NOT shift
    assert _normalize_venue_stamp("2026-10-30 09:15:00") == "2026-10-30 09:15:00"
    assert _normalize_venue_stamp("2026-10-30T09:15:00") == "2026-10-30 09:15:00"

    # Idempotency: passing the output back in keeps it unchanged
    res1 = _normalize_venue_stamp("2026-10-30T03:45:00Z")
    assert res1 == "2026-10-30 09:15:00"
    res2 = _normalize_venue_stamp(res1)
    assert res2 == "2026-10-30 09:15:00"


# ── 2. Every 30-Minute Candle Boundary Tests ─────────────────────────────────


def test_every_30m_candle_boundary_nse_session() -> None:
    from market.native_aggregate import bucket_start

    anchor = session_anchor_seconds("09:15")
    size_s = 1800  # 30m

    expected_buckets = [
        # (sample_stamp, expected_bucket_start)
        ("2026-10-30 09:15:00", "2026-10-30 09:15:00"),
        ("2026-10-30 09:15:01", "2026-10-30 09:15:00"),
        ("2026-10-30 09:44:59", "2026-10-30 09:15:00"),
        ("2026-10-30 09:45:00", "2026-10-30 09:45:00"),
        ("2026-10-30 10:14:59", "2026-10-30 09:45:00"),
        ("2026-10-30 10:15:00", "2026-10-30 10:15:00"),
        ("2026-10-30 10:44:59", "2026-10-30 10:15:00"),
        ("2026-10-30 10:45:00", "2026-10-30 10:45:00"),
        ("2026-10-30 11:15:00", "2026-10-30 11:15:00"),
        ("2026-10-30 11:45:00", "2026-10-30 11:45:00"),
        ("2026-10-30 12:15:00", "2026-10-30 12:15:00"),
        ("2026-10-30 12:45:00", "2026-10-30 12:45:00"),
        ("2026-10-30 13:15:00", "2026-10-30 13:15:00"),
        ("2026-10-30 13:45:00", "2026-10-30 13:45:00"),
        ("2026-10-30 14:15:00", "2026-10-30 14:15:00"),
        ("2026-10-30 14:45:00", "2026-10-30 14:45:00"),
        ("2026-10-30 15:14:59", "2026-10-30 14:45:00"),
        ("2026-10-30 15:15:00", "2026-10-30 15:15:00"),
        ("2026-10-30 15:29:59", "2026-10-30 15:15:00"),
        ("2026-10-30 15:30:00", "2026-10-30 15:15:00"),
    ]

    for stamp, expected in expected_buckets:
        assert bucket_start(stamp, size_s, anchor) == expected


# ── 3. OHLCV Accuracy Across Multi-Tick 30m Ingestion ─────────────────────────


def test_ohlcv_accumulation_accuracy_30m() -> None:
    face = _MockQuoteFace()
    feed = BrokerFeedProvider(face, timeframe="30m", session_anchor="09:15")
    feed.open(("NSE:KAYNES-EQ",), "30m")

    # Tick 1: Opening tick at 09:15:00 IST (UTC: 03:45:00Z)
    ev1 = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 100.0,
        "volume": 1000,
        "timestamp": "2026-10-30T03:45:00Z",
    })
    assert ev1 is None  # forming candle, not closed

    # Tick 2: High push at 09:20:00 IST (UTC: 03:50:00Z)
    ev2 = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 115.0,
        "volume": 1250,
        "timestamp": "2026-10-30T03:50:00Z",
    })
    assert ev2 is None

    # Tick 3: Low drop at 09:30:00 IST (UTC: 04:00:00Z)
    ev3 = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 92.0,
        "volume": 1500,
        "timestamp": "2026-10-30T04:00:00Z",
    })
    assert ev3 is None

    # Tick 4: Close at 09:44:59 IST (UTC: 04:14:59Z)
    ev4 = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 108.0,
        "volume": 1800,
        "timestamp": "2026-10-30T04:14:59Z",
    })
    assert ev4 is None

    # Tick 5: Boundary crossover tick at 09:45:00 IST (UTC: 04:15:00Z) -> CLOSES 09:15 candle!
    ev5 = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 110.0,
        "volume": 2000,
        "timestamp": "2026-10-30T04:15:00Z",
    })
    assert ev5 is not None
    assert isinstance(ev5, CandleEvent)
    assert ev5.symbol == "NSE:KAYNES-EQ"
    assert ev5.timestamp == "2026-10-30 09:15:00"
    assert ev5.open == 100.0
    assert ev5.high == 115.0
    assert ev5.low == 92.0
    assert ev5.close == 108.0
    # Dayvol delta: (1800 - 1000) = 800 volume in 09:15 candle
    assert ev5.volume == 800
    assert ev5.timeframe == "30m"
    assert ev5.is_closed is True


# ── 4. Duplicate, Late, and Out-of-Order Ticks Handling ───────────────────────


def test_duplicate_ticks_handling() -> None:
    face = _MockQuoteFace()
    feed = BrokerFeedProvider(face, timeframe="30m", session_anchor="09:15")
    feed.open(("NSE:KAYNES-EQ",), "30m")

    # Initial tick
    feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 100.0,
        "volume": 1000,
        "timestamp": "2026-10-30T03:45:00Z",
    })

    # Exact duplicate tick: same stamp, same price, same volume
    ev_dup = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 100.0,
        "volume": 1000,
        "timestamp": "2026-10-30T03:45:00Z",
    })
    assert ev_dup is None
    held = feed._buckets.get("NSE:KAYNES-EQ")
    assert held is not None
    # OHLCV unchanged: volume delta is 0
    assert held[1] == (100.0, 100.0, 100.0, 100.0, 0.0)


def test_late_ticks_do_not_corrupt_open_bucket_or_reopen_closed() -> None:
    face = _MockQuoteFace()
    feed = BrokerFeedProvider(face, timeframe="30m", session_anchor="09:15")
    feed.open(("NSE:KAYNES-EQ",), "30m")

    # Ingest 09:15 candle ticks
    feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 100.0,
        "volume": 1000,
        "timestamp": "2026-10-30T03:45:00Z",
    })

    # Tick at 09:45:00 closes 09:15 and opens 09:45 bucket with price=105.0
    ev_closed = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 105.0,
        "volume": 1200,
        "timestamp": "2026-10-30T04:15:00Z",
    })
    assert ev_closed is not None
    assert ev_closed.timestamp == "2026-10-30 09:15:00"
    assert feed.last_emitted["NSE:KAYNES-EQ"] == "2026-10-30 09:15:00"

    # Late tick arrives from 09:30:00 (which belongs to already emitted 09:15 candle)
    ev_late = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 99.0,
        "volume": 1100,
        "timestamp": "2026-10-30T04:00:00Z",
    })
    assert ev_late is None

    # Verify open 09:45 bucket is untouched! Open is still 105.0, bucket is 09:45:00
    held = feed._buckets.get("NSE:KAYNES-EQ")
    assert held is not None
    assert held[0] == "2026-10-30 09:45:00"
    assert held[1][0] == 105.0  # Open was not overwritten by late tick


def test_out_of_order_ticks_do_not_replace_active_bucket() -> None:
    face = _MockQuoteFace()
    feed = BrokerFeedProvider(face, timeframe="30m", session_anchor="09:15")
    feed.open(("NSE:KAYNES-EQ",), "30m")

    # First tick opens 10:15 bucket
    feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 200.0,
        "volume": 5000,
        "timestamp": "2026-10-30 10:15:00",
    })
    assert feed._buckets["NSE:KAYNES-EQ"][0] == "2026-10-30 10:15:00"

    # Out-of-order tick arrives belonging to 09:45 bucket
    ev_ooo = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 195.0,
        "volume": 4800,
        "timestamp": "2026-10-30 09:55:00",
    })
    assert ev_ooo is None

    # Active bucket must remain 10:15:00
    assert feed._buckets["NSE:KAYNES-EQ"][0] == "2026-10-30 10:15:00"


# ── 5. Historical vs Live Candle Alignment Tests ──────────────────────────────


def test_historical_watermark_seed_aligns_with_live_candles() -> None:
    face = _MockQuoteFace()
    feed = BrokerFeedProvider(face, timeframe="30m", session_anchor="09:15")
    feed.open(("NSE:KAYNES-EQ",), "30m")

    # Simulate startup historical recovery seeding 09:15 candle watermark
    feed.set_last_emitted("NSE:KAYNES-EQ", "2026-10-30 09:15:00")
    assert feed.last_emitted["NSE:KAYNES-EQ"] == "2026-10-30 09:15:00"

    # Live tick at 09:45:00 arrives (in UTC: 04:15:00Z)
    ev1 = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 120.0,
        "volume": 3000,
        "timestamp": "2026-10-30T04:15:00Z",
    })
    assert ev1 is None  # Opens 09:45 forming bucket

    # Next boundary tick at 10:15:00 closes 09:45 candle
    ev2 = feed._ingest({
        "symbol": "NSE:KAYNES-EQ",
        "price": 125.0,
        "volume": 3500,
        "timestamp": "2026-10-30T04:45:00Z",
    })
    assert ev2 is not None
    # The 09:45 candle is emitted because "2026-10-30 09:45:00" > "2026-10-30 09:15:00"
    assert ev2.timestamp == "2026-10-30 09:45:00"
    assert ev2.is_closed is True
    assert feed.last_emitted["NSE:KAYNES-EQ"] == "2026-10-30 09:45:00"


# ── 6. Strategy Evaluates Strictly on Closed 30m Candles ─────────────────────


class _CountingLogic:
    def __init__(self) -> None:
        self.evaluated_count = 0

    def warmup(self) -> int:
        return 0

    def on_bar(self, _view: BarView) -> Any:
        self.evaluated_count += 1
        return None


def test_strategy_driver_evaluates_only_closed_candles() -> None:
    logic = _CountingLogic()
    ctx = StrategyContext(
        strategy_id="test_strat",
        strategy_version="1.0",
        logic=logic,
        params=StrategyParameters(),
        contract=None,  # type: ignore[arg-type]
    )
    from execution.runtime.lifecycle import LifecycleState

    ctx.lifecycle.state = LifecycleState.RUNNING
    driver = LiveStrategyDriver(ctx)

    # 1. Forming candle with is_closed=False must be rejected by strategy driver
    forming_candle = CandleEvent(
        symbol="NSE:KAYNES-EQ",
        timestamp="2026-10-30 09:15:00",
        seq=1,
        source="broker-feed",
        open=100.0,
        high=105.0,
        low=98.0,
        close=102.0,
        volume=500,
        timeframe="30m",
        is_closed=False,
    )
    sig = driver.on_candle(forming_candle)
    assert sig is None
    assert logic.evaluated_count == 0

    # 2. Closed candle with is_closed=True evaluates strategy
    closed_candle = CandleEvent(
        symbol="NSE:KAYNES-EQ",
        timestamp="2026-10-30 09:15:00",
        seq=2,
        source="broker-feed",
        open=100.0,
        high=105.0,
        low=98.0,
        close=102.0,
        volume=500,
        timeframe="30m",
        is_closed=True,
    )
    driver.on_candle(closed_candle)
    assert logic.evaluated_count == 1
