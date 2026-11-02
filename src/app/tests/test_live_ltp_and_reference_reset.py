from datetime import date, timedelta, timezone

from broker.market_data_fanout import _symbol_matches
from execution.market_data.broker_feed import BrokerFeedProvider
from strategy.obr_source import OBR_C1C4_SOURCE

IST = timezone(timedelta(hours=5, minutes=30))

# Compile embedded OBR source
ns = {}
exec(OBR_C1C4_SOURCE, ns)
OBREngine = ns["OBREngine"]
StrategyConfig = ns["StrategyConfig"]
OBRBar = ns["Bar"]


def test_symbol_matching_variations():
    """Verify robust matching between exchange-prefixed, bare, and -EQ symbol formats."""
    assert _symbol_matches("NSE:KAYNES", "KAYNES")
    assert _symbol_matches("NSE:KAYNES", "KAYNES-EQ")
    assert _symbol_matches("NSE:KAYNES", "NSE:KAYNES")
    assert _symbol_matches("NSE:KAYNES", "NSE:KAYNES-EQ")
    # Different symbol must NOT match
    assert not _symbol_matches("NSE:KAYNES", "NSE:DREDGECORP")
    assert not _symbol_matches("NSE:KAYNES", "DREDGECORP-EQ")
    assert not _symbol_matches("NSE:KAYNES", "INFY")


def test_broker_feed_provider_always_records_ltp_mark():
    """Verify every incoming tick updates _last_close and _last_tick_time."""

    class MockFace:
        def __init__(self):
            self.ticks = []

        def poll(self):
            t = list(self.ticks)
            self.ticks.clear()
            return t

        def health(self):
            return True, "ok"

        def subscribe(self, syms):
            pass

        def unsubscribe(self, syms):
            pass

    mock_face = MockFace()
    feed = BrokerFeedProvider(mock_face, timeframe="30m")
    feed.open(("NSE:KAYNES", "NSE:DREDGECORP"), "30m")

    # Ingest tick for KAYNES only
    mock_face.ticks.append(
        {
            "symbol": "NSE:KAYNES-EQ",
            "price": 3280.50,
            "timestamp": "2026-11-02 09:20:00",
            "volume": 100,
        }
    )
    feed.poll()
    # KAYNES must update
    assert feed.last_close.get("NSE:KAYNES") == 3280.50
    assert feed.last_tick_time.get("NSE:KAYNES") == "2026-11-02 09:20:00"
    # DREDGECORP must remain empty (no false update)
    assert "NSE:DREDGECORP" not in feed.last_close
    assert "NSE:DREDGECORP" not in feed.last_tick_time

    # Ingest tick 2 with new price for KAYNES
    mock_face.ticks.append(
        {
            "symbol": "KAYNES",
            "price": 3295.00,
            "timestamp": "2026-11-02 09:25:00",
            "volume": 200,
        }
    )
    feed.poll()
    assert feed.last_close.get("NSE:KAYNES") == 3295.00
    assert feed.last_tick_time.get("NSE:KAYNES") == "2026-11-02 09:25:00"


def test_obr_daily_reference_reset_on_new_day():
    """Verify that OBREngine resets refHigh, refLow, and refSet on day boundary."""
    cfg = StrategyConfig(refIndex=3, extendBars=5)
    engine = OBREngine(cfg)

    # Feed Day 1 bars: C1, C2, C3 (ref), C4 (break)
    day1 = date(2026, 10, 30)
    engine.prev_date_key = date(2026, 10, 29)
    b0 = OBRBar(0, "2026-10-30 09:15:00", 1000.0, 1010.0, 995.0, 1005.0, 10000.0, day1)
    b1 = OBRBar(1, "2026-10-30 09:45:00", 1005.0, 1015.0, 1000.0, 1012.0, 12000.0, day1)
    b2 = OBRBar(2, "2026-10-30 10:15:00", 1012.0, 1020.0, 1005.0, 1015.0, 15000.0, day1)  # REF
    b3 = OBRBar(3, "2026-10-30 10:45:00", 1015.0, 1025.0, 1010.0, 1022.0, 20000.0, day1)  # BREAK
    for b in (b0, b1, b2, b3):
        engine.process_bar(b)

    assert engine.refSet is True
    assert engine.refHigh == 1020.0
    assert engine.refLow == 1005.0
    assert engine.firstBreakDone is True

    # Now Day 2 arrives: C1 of Monday 2026-11-02
    day2 = date(2026, 11, 2)
    b4 = OBRBar(4, "2026-11-02 09:15:00", 1022.0, 1028.0, 1018.0, 1025.0, 11000.0, day2)
    engine.process_bar(b4)

    # On new day bar, previous day reference levels MUST be reset to None!
    assert engine.refSet is False
    assert engine.refHigh is None
    assert engine.refLow is None
    assert engine.firstBreakDone is False
    assert engine.candleCount == 0

    # Also test explicit reset_for_new_day
    engine.refHigh = 9999.0
    engine.refSet = True
    engine.reset_for_new_day(day2)
    assert engine.refSet is False
    assert engine.refHigh is None
    assert engine.refLow is None
    assert engine.firstBreakDone is False


def test_reference_levels_only_after_c3_complete():
    """Verify that today's reference levels remain None until Candle 3 completes."""
    cfg = StrategyConfig(refIndex=3, extendBars=5)
    engine = OBREngine(cfg)

    today = date(2026, 11, 2)
    engine.prev_date_key = date(2026, 11, 1)

    # C1 (09:15-09:45)
    b0 = OBRBar(0, "2026-11-02 09:15:00", 1000.0, 1010.0, 995.0, 1005.0, 10000.0, today)
    engine.process_bar(b0)
    assert engine.candleCount == 0
    assert engine.refSet is False
    assert engine.refHigh is None

    # C2 (09:45-10:15)
    b1 = OBRBar(1, "2026-11-02 09:45:00", 1005.0, 1015.0, 1000.0, 1012.0, 12000.0, today)
    engine.process_bar(b1)
    assert engine.candleCount == 1
    assert engine.refSet is False
    assert engine.refHigh is None

    # C3 (10:15-10:45)
    b2 = OBRBar(2, "2026-11-02 10:15:00", 1012.0, 1020.0, 1005.0, 1015.0, 15000.0, today)
    engine.process_bar(b2)
    assert engine.candleCount == 2
    # At candle 3, reference levels are captured!
    assert engine.refSet is True
    assert engine.refHigh == 1020.0
    assert engine.refLow == 1005.0
