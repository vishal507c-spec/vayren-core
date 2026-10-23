"""Market-data fan-out: one upstream stream, independent consumer cursors.

These tests reproduce the 2026-10-22 incident shape without a broker: a
shared face whose destructive ``poll()`` was drained by 22 sessions in
sequence, so only the first symbol ever saw a quote. The fan-out must make
polling order irrelevant, preserve duplicate/out-of-order arrivals for the
downstream deduper, and account for overflow instead of losing it silently.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from broker.market_data_fanout import MarketDataFanout, _symbol_matches


class _Upstream:
    """Duck-typed market-data face with a destructive poll (the old shape)."""

    name = "fake-venue"

    def __init__(self) -> None:
        self._published: list[dict[str, Any]] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.connect_calls = 0
        self.reconnect_calls = 0
        self.disconnect_calls = 0
        self.close_calls = 0
        self.healthy = True
        self.poll_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def reconnect(self) -> None:
        self.reconnect_calls += 1

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        self.subscribed.extend(symbols)

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        self.unsubscribed.extend(symbols)

    def poll(self) -> tuple[dict[str, Any], ...]:
        self.poll_calls += 1
        quotes = tuple(self._published)
        self._published.clear()
        return quotes

    def health(self) -> tuple[bool, str]:
        return self.healthy, "upstream ok"

    def close(self) -> None:
        self.close_calls += 1

    def publish(self, *quotes: dict[str, Any]) -> None:
        self._published.extend(quotes)


def _quote(symbol: str, price: float, seq: int = 0) -> dict[str, Any]:
    return {
        "symbol": f"NSE:{symbol}-EQ",
        "ltp": price,
        "last_traded_time": 1_700_000_000 + seq,
    }


def _wire(
    fanout: MarketDataFanout, upstream: _Upstream, symbols: tuple[str, ...]
) -> dict[str, Any]:
    consumers = {symbol: fanout.subscription(symbol, (f"NSE:{symbol}",)) for symbol in symbols}
    for consumer in consumers.values():
        consumer.connect()
        consumer.subscribe((f"NSE:{consumer.consumer_id}",))
    assert set(upstream.subscribed) == {f"NSE:{symbol}" for symbol in symbols}
    return consumers


def test_22_symbols_share_one_upstream_and_poll_in_any_order() -> None:
    symbols = tuple(f"SYM{index:02d}" for index in range(22))
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    consumers = _wire(fanout, upstream, symbols)

    for index, symbol in enumerate(symbols):
        upstream.publish(_quote(symbol, 100.0 + index, index))

    # The incident's poll order: session 0 drains everything first. Here each
    # consumer must still receive exactly its own symbol, no matter the order.
    for symbol in reversed(symbols):
        received = consumers[symbol].poll()
        assert [quote["symbol"] for quote in received] == [f"NSE:{symbol}-EQ"]
        assert consumers[symbol].poll() == ()

    assert upstream.connect_calls == 1, "one connection for 22 consumers"
    stats = fanout.stats()
    assert stats["published"] == 22
    assert stats["overflow_dropped"] == 0
    assert all(item["delivered"] == 1 for item in stats["consumers"].values())
    assert all(item["dropped"] == 0 for item in stats["consumers"].values())


def test_polling_one_consumer_twice_never_steals_from_another() -> None:
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    first = fanout.subscription("A", ("NSE:A",))
    second = fanout.subscription("B", ("NSE:B",))
    first.connect()
    first.subscribe(("NSE:A",))
    second.subscribe(("NSE:B",))

    upstream.publish(_quote("A", 10.0), _quote("B", 20.0))
    assert len(first.poll()) == 1
    assert first.poll() == ()
    # The fast consumer's second poll must not consume B's event.
    assert [quote["symbol"] for quote in second.poll()] == ["NSE:B-EQ"]
    assert second.poll() == ()


def test_duplicate_and_out_of_order_quotes_are_delivered_verbatim() -> None:
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    consumer = fanout.subscription("A", ("NSE:A",))
    consumer.subscribe(("NSE:A",))

    duplicate = _quote("A", 10.0, 1)
    upstream.publish(duplicate, duplicate, _quote("A", 11.0, 2))
    received = consumer.poll()
    # Duplicate suppression belongs to the candle builder/session, not the
    # transport fan-out; every arrival is preserved in order.
    assert received == (duplicate, duplicate, _quote("A", 11.0, 2))


def test_slow_consumer_overflows_visibly_while_fast_consumer_loses_nothing() -> None:
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream, max_buffer=3)
    fast = fanout.subscription("FAST", ("NSE:FAST",))
    slow = fanout.subscription("SLOW", ("NSE:SLOW",))
    fast.subscribe(("NSE:FAST",))
    slow.subscribe(("NSE:SLOW",))

    for index in range(5):
        upstream.publish(_quote("FAST", 100.0 + index, index))
        # The fast consumer keeps up on every publication; the slow one
        # never polls until after the burst.
        assert len(fast.poll()) == 1
    stats = fanout.stats()
    assert stats["overflow_dropped"] == 2
    assert stats["buffer_depth"] == 3
    assert stats["consumers"]["FAST"]["dropped"] == 0

    upstream.publish(_quote("SLOW", 50.0, 99))
    slow_quotes = slow.poll()
    assert [quote["symbol"] for quote in slow_quotes] == ["NSE:SLOW-EQ"]
    assert fanout.stats()["consumers"]["SLOW"]["dropped"] == 3


def test_disconnect_is_per_consumer_and_reconnect_keeps_cursors() -> None:
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    left = fanout.subscription("LEFT", ("NSE:LEFT",))
    right = fanout.subscription("RIGHT", ("NSE:RIGHT",))
    left.subscribe(("NSE:LEFT",))
    right.subscribe(("NSE:RIGHT",))

    left.disconnect()
    upstream.publish(_quote("LEFT", 1.0), _quote("RIGHT", 2.0))
    assert left.poll() == ()
    assert [quote["symbol"] for quote in right.poll()] == ["NSE:RIGHT-EQ"]

    left.reconnect()
    upstream.publish(_quote("LEFT", 3.0))
    assert [quote["symbol"] for quote in left.poll()] == ["NSE:LEFT-EQ"]
    assert upstream.reconnect_calls == 1
    assert upstream.close_calls == 0, "one consumer's reconnect never closes the shared socket"


def test_last_consumer_close_closes_an_owned_upstream_exactly_once() -> None:
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream, close_upstream_when_empty=True)
    left = fanout.subscription("LEFT", ("NSE:LEFT",))
    right = fanout.subscription("RIGHT", ("NSE:RIGHT",))
    left.connect()
    right.connect()
    left.close()
    assert upstream.close_calls == 0
    right.close()
    assert upstream.close_calls == 1
    right.close()
    assert upstream.close_calls == 1
    assert fanout.stats()["closed"] is True


def test_shared_upstream_is_disconnected_not_closed_and_stays_reusable() -> None:
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    consumer = fanout.subscription("A", ("NSE:A",))
    consumer.connect()
    consumer.close()

    assert upstream.close_calls == 0, "a venue-owned StaticPlugin face must survive teardown"
    assert upstream.disconnect_calls == 1
    assert upstream.unsubscribed == ["NSE:A"]
    assert fanout.stats()["closed"] is True

    # The next live run receives the same face from StaticPlugin and connects.
    again = MarketDataFanout(upstream)
    second = again.subscription("A", ("NSE:A",))
    second.connect()
    assert upstream.connect_calls == 2


def test_unsubscribe_only_releases_symbols_no_consumer_needs() -> None:
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    left = fanout.subscription("LEFT", ("NSE:SHARED",))
    right = fanout.subscription("RIGHT", ("NSE:SHARED",))
    left.subscribe(("NSE:SHARED",))
    right.subscribe(("NSE:SHARED",))
    left.unsubscribe(("NSE:SHARED",))
    assert upstream.unsubscribed == []
    right.unsubscribe(("NSE:SHARED",))
    assert upstream.unsubscribed == ["NSE:SHARED"]


def test_upstream_poll_failure_is_counted_and_not_fatal() -> None:
    class _Broken(_Upstream):
        def poll(self) -> tuple[dict[str, Any], ...]:
            raise RuntimeError("socket exploded")

    upstream = _Broken()
    fanout = MarketDataFanout(upstream)
    consumer = fanout.subscription("A", ("NSE:A",))
    consumer.subscribe(("NSE:A",))
    assert consumer.poll() == ()
    stats = fanout.stats()
    assert stats["pump_errors"] == 1
    assert "socket exploded" in stats["last_error"]


def test_concurrent_consumer_polling_is_thread_safe() -> None:
    symbols = tuple(f"SYM{index:02d}" for index in range(8))
    upstream = _Upstream()
    fanout = MarketDataFanout(upstream)
    consumers = _wire(fanout, upstream, symbols)
    for index in range(50):
        for symbol in symbols:
            upstream.publish(_quote(symbol, 100.0 + index, index))

    def drain(consumer: Any) -> list[dict[str, Any]]:
        received: list[dict[str, Any]] = []
        for _ in range(10):
            received.extend(consumer.poll())
        return received

    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        results = list(pool.map(drain, (consumers[s] for s in symbols)))

    for symbol, received in zip(symbols, results, strict=True):
        assert len(received) == 50, "every consumer sees every own-symbol quote exactly once"
        assert {quote["symbol"] for quote in received} == {f"NSE:{symbol}-EQ"}
    stats = fanout.stats()
    assert all(item["delivered"] == 50 for item in stats["consumers"].values())
    assert all(item["dropped"] == 0 for item in stats["consumers"].values())


def test_connect_failure_is_propagated_and_retryable() -> None:
    class _Refusing(_Upstream):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def connect(self) -> None:
            self.attempts += 1
            raise RuntimeError("credentials rejected")

    upstream = _Refusing()
    fanout = MarketDataFanout(upstream)
    consumer = fanout.subscription("A", ("NSE:A",))

    with pytest.raises(RuntimeError, match="credentials rejected"):
        consumer.connect()
    with pytest.raises(RuntimeError, match="credentials rejected"):
        consumer.connect()
    assert upstream.attempts == 2, "a failed connect must not be cached as connected"


def test_reconnect_failure_is_propagated_and_retryable() -> None:
    class _Refusing(_Upstream):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def reconnect(self) -> None:
            self.attempts += 1
            raise RuntimeError("socket refused")

    upstream = _Refusing()
    fanout = MarketDataFanout(upstream)
    consumer = fanout.subscription("A", ("NSE:A",))
    consumer.connect()

    with pytest.raises(RuntimeError, match="socket refused"):
        consumer.reconnect()
    with pytest.raises(RuntimeError, match="socket refused"):
        consumer.reconnect()
    assert upstream.attempts == 2, "a failed reconnect must not start the coalescing window"
    assert fanout.stats()["last_error"].startswith("upstream reconnect failed")


def test_eq_suffix_symbol_matching() -> None:
    assert _symbol_matches("NSE:SBIN", "NSE:SBIN-EQ")
    assert _symbol_matches("NSE:SBIN-EQ", "NSE:SBIN")
    assert _symbol_matches("NSE:SBIN", "NSE:SBIN")
    assert not _symbol_matches("NSE:SBIN", "NSE:INFY-EQ")
