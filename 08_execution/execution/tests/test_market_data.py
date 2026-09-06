"""Market-data tests: replay determinism, normalization guarantees."""

from execution.events import CandleEvent, HeartbeatEvent, QuoteEvent, TradeEvent
from execution.market_data.normalizer import NormalizerConfig, StreamNormalizer
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.tests.helpers import make_bars, make_candles


def _candle(symbol: str, seq: int, close: float = 100.0) -> CandleEvent:
    return CandleEvent(
        symbol=symbol,
        timestamp="2026-01-06T09:30:00+00:00",
        seq=seq,
        close=close,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        volume=100,
        timeframe="15m",
    )


def test_bars_to_candles_preserves_order_and_seq() -> None:
    candles = bars_to_candles(make_bars("TEST", 10), "15m")
    assert len(candles) == 10
    assert [c.seq for c in candles] == list(range(1, 11))
    assert all(c.is_closed and c.timeframe == "15m" for c in candles)
    assert candles[0].close == make_bars("TEST", 10)[0].close


def test_replay_provider_chunks_and_health() -> None:
    provider = ReplayProvider(make_candles(count=5), chunk_size=2)
    assert provider.name == "replay"
    assert "candle-close" in provider.capabilities
    assert provider.health()[0] is False  # not opened
    provider.open(("TEST",), "15m")
    assert len(provider.poll()) == 2
    assert len(provider.poll()) == 2
    assert len(provider.poll()) == 1
    assert provider.poll() == ()
    assert provider.exhausted
    assert provider.health() == (True, "tape exhausted")
    provider.close()
    assert provider.poll() == ()


def test_normalizer_orders_and_dedupes() -> None:
    normalizer = StreamNormalizer()
    assert normalizer.observe(_candle("A", 2), 100.0) == ()
    assert normalizer.observe(_candle("A", 2), 101.0) == ()  # duplicate
    delivered = normalizer.observe(_candle("A", 1), 102.0)
    assert [e.seq for e in delivered] == [1, 2]  # backfilled in order
    assert normalizer.observe(_candle("A", 1), 103.0) == ()  # below watermark: dup
    assert normalizer.stats.accepted == 2
    assert normalizer.stats.duplicates == 2
    assert normalizer.expected_seq("A") == 3


def test_normalizer_counts_gaps_and_evicts_without_stall() -> None:
    normalizer = StreamNormalizer(NormalizerConfig(max_reorder_buffer=2))
    assert normalizer.observe(_candle("A", 1), 100.0)
    normalizer.observe(_candle("A", 5), 101.0)  # jump: gap counted
    assert normalizer.stats.gaps == 1
    normalizer.observe(_candle("A", 6), 102.0)
    normalizer.observe(_candle("A", 7), 103.0)  # overflow: watermark jumps, stream continues
    assert normalizer.stats.evicted >= 1
    delivered = normalizer.observe(_candle("A", 8), 104.0)
    assert delivered, "stream must continue after eviction"
    assert [e.seq for e in delivered] == sorted(e.seq for e in delivered)


def test_heartbeat_and_staleness() -> None:
    normalizer = StreamNormalizer(
        NormalizerConfig(stale_after_seconds=10.0, heartbeat_timeout_seconds=5.0)
    )
    normalizer.observe(
        HeartbeatEvent(symbol="A", timestamp="2026-01-06T09:30:00+00:00", seq=1), 100.0
    )
    assert normalizer.stats.heartbeats == 1
    healthy, _ = normalizer.check_health("A", 102.0)
    assert healthy
    healthy, reason = normalizer.check_health("A", 200.0)
    assert not healthy and "heartbeat" in reason
    assert normalizer.is_stale("A")
    normalizer.observe(
        HeartbeatEvent(symbol="A", timestamp="2026-01-06T09:31:00+00:00", seq=2), 201.0
    )
    assert not normalizer.is_stale("A")


def test_stale_events_flag_without_heartbeat() -> None:
    normalizer = StreamNormalizer(NormalizerConfig(stale_after_seconds=10.0))
    normalizer.observe(_candle("B", 1), 100.0)
    healthy, reason = normalizer.check_health("B", 500.0)
    assert not healthy and "stale" in reason


def test_multi_symbol_streams_are_independent() -> None:
    normalizer = StreamNormalizer()
    assert [e.seq for e in normalizer.observe(_candle("A", 1), 100.0)] == [1]
    assert [e.seq for e in normalizer.observe(_candle("B", 1), 100.0)] == [1]
    assert normalizer.expected_seq("A") == 2
    assert normalizer.expected_seq("B") == 2


def test_quote_and_trade_events_flow_through() -> None:
    normalizer = StreamNormalizer()
    quote = QuoteEvent(symbol="A", timestamp="t", seq=1, bid=99.0, ask=101.0)
    trade = TradeEvent(symbol="A", timestamp="t", seq=2, price=100.0, quantity=5.0)
    assert normalizer.observe(quote, 1.0) == (quote,)
    assert normalizer.observe(trade, 2.0) == (trade,)
