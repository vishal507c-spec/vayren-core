"""FYERS market-data adapter: per-consumer cursors over one WebSocket.

The adapter used to expose a single ``queue.Queue`` drained by whichever
session polled first. These tests pin the replacement contract: every
registered consumer sees every retained arrival exactly once, in arrival
order, regardless of who polls first, and overflow is counted.
"""

from __future__ import annotations

from broker.providers.fyers.live_market_data import FyersLiveMarketData


def _tick(symbol: str, price: float, second: int = 0) -> dict[str, object]:
    return {
        "symbol": f"NSE:{symbol}-EQ",
        "ltp": price,
        "last_traded_time": 1_700_000_000 + second,
    }


def test_22_consumers_each_see_every_quote_once_in_any_order() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456")
    symbols = tuple(f"SYM{index:02d}" for index in range(22))
    for symbol in symbols:
        md.register_consumer(symbol)
    for index, symbol in enumerate(symbols):
        md._on_message(_tick(symbol, 100.0 + index, index))

    for symbol in reversed(symbols):
        received = md.poll(symbol)
        assert len(received) == 22, "one consumer must never drain another's events"
        assert [quote["symbol"] for quote in received] == [f"NSE:{item}-EQ" for item in symbols]
        assert md.poll(symbol) == (), "each consumer's cursor advances independently"
    md.close()


def test_polling_without_registration_preserves_legacy_single_consumer_shape() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456")
    md._on_message(_tick("SBIN", 800.0, 1))
    md._on_message(_tick("INFY", 1500.0, 2))
    assert [quote["symbol"] for quote in md.poll()] == ["NSE:SBIN-EQ", "NSE:INFY-EQ"]
    assert md.poll() == ()
    md.close()


def test_bounded_history_reports_overflow_and_consumer_lag() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456", max_queue_size=2)
    md.register_consumer("slow")
    md._on_message(_tick("A", 1.0, 1))
    md._on_message(_tick("B", 2.0, 2))
    md._on_message(_tick("C", 3.0, 3))

    assert md.overflow_dropped == 1
    assert md.buffered_quotes == 2
    received = md.poll("slow")
    assert len(received) == 2  # only the retained tail is deliverable
    stats = md.consumer_stats()["slow"]
    assert stats["delivered"] == 2
    assert stats["dropped"] >= 1
    md.unregister_consumer("slow")
    assert "slow" not in md.consumer_stats()
    md.close()


def test_close_is_safe_and_clears_cursors() -> None:
    md = FyersLiveMarketData(app_id="APP123", access_token="TOKEN456")
    md.register_consumer("A")
    md._on_message(_tick("A", 1.0))
    md.close()
    assert md.poll("A") == ()
    assert md.consumer_stats() == {}
    assert md.buffered_quotes == 0
