"""Broker-feed bridge proofs — quotes in, closed candles out, never twice."""

from __future__ import annotations

from execution import BrokerFeedProvider
from execution.events import CandleEvent


class ScriptedFace:
    """Duck-typed UBL MarketDataFace double (no broker import in execution)."""

    def __init__(self) -> None:
        self.quotes: list[dict] = []
        self.subscribed: tuple[str, ...] = ()
        self.connected = False
        self.health_ok = True

    def connect(self) -> None:
        self.connected = True

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        self.connect()
        self.subscribe(symbols)

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        self.connect()  # face contract: subscription implies transport
        self.subscribed = tuple(dict.fromkeys((*self.subscribed, *symbols)))

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        dropped = set(symbols)
        self.subscribed = tuple(s for s in self.subscribed if s not in dropped)

    def poll(self) -> tuple[dict, ...]:
        if not self.connected:
            return ()
        out, self.quotes = tuple(self.quotes), []
        return out

    def health(self) -> tuple[bool, str]:
        if not self.connected:
            return False, "not connected"
        return (True, "streaming") if self.health_ok else (False, "face reports down")

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False
        self.subscribed = ()


def _quote(symbol: str, price: float, stamp: str, volume: int = 0) -> dict:
    return {"symbol": symbol, "price": price, "timestamp": stamp, "volume": volume}


def test_buckets_close_exactly_once_in_order() -> None:
    face = ScriptedFace()
    provider = BrokerFeedProvider(face, "15m")
    provider.open(("AAA", "BBB"), "15m")
    face.quotes = [
        _quote("BBB", 50.0, "2026-01-05 09:30:00", 100),
        _quote("AAA", 100.0, "2026-01-05 09:15:00", 10),
        _quote("AAA", 101.0, "2026-01-05 09:20:00", 25),
    ]
    assert provider.poll() == ()  # buckets still forming
    face.quotes = [_quote("AAA", 102.0, "2026-01-05 09:30:00", 40)]
    (candle,) = provider.poll()
    assert isinstance(candle, CandleEvent)
    assert (candle.symbol, candle.timestamp) == ("AAA", "2026-01-05 09:15:00")
    assert (candle.open, candle.high, candle.low, candle.close) == (100.0, 101.0, 100.0, 101.0)
    assert candle.volume == 15  # day-volume deltas, not absolute
    assert candle.is_closed
    assert provider.poll() == ()
    assert provider.last_emitted == {"AAA": "2026-01-05 09:15:00"}


def test_session_anchor_alignment() -> None:
    """30m buckets floor from 09:15 (session), not midnight (09:00/09:30)."""
    face = ScriptedFace()
    provider = BrokerFeedProvider(face, "30m", session_anchor="09:15")
    provider.open(("AAA",), "30m")
    face.quotes = [_quote("AAA", 100.0, "2026-01-05 09:20:00", 5)]
    assert provider.poll() == ()
    face.quotes = [_quote("AAA", 101.0, "2026-01-05 09:50:00", 9)]
    (candle,) = provider.poll()
    assert isinstance(candle, CandleEvent)
    assert candle.timestamp == "2026-01-05 09:15:00"


def test_disconnect_reconnect_never_reemits() -> None:
    face = ScriptedFace()
    provider = BrokerFeedProvider(face, "15m")
    provider.open(("AAA",), "15m")
    face.quotes = [_quote("AAA", 100.0, "2026-01-05 09:15:00", 7)]
    assert provider.poll() == ()
    provider.disconnect()
    assert provider.poll() == ()
    assert provider.health()[0] is False
    provider.reconnect()
    assert provider.health()[0] is True
    # the forming bucket survived: no phantom close, no duplicate later
    face.quotes = [
        _quote("AAA", 100.5, "2026-01-05 09:20:00", 11),
        _quote("AAA", 101.0, "2026-01-05 09:31:00", 15),
    ]
    (candle,) = provider.poll()
    assert isinstance(candle, CandleEvent)
    assert (candle.timestamp, candle.volume) == ("2026-01-05 09:15:00", 4)
    assert provider.poll() == ()


def test_malformed_quotes_skipped() -> None:
    face = ScriptedFace()
    provider = BrokerFeedProvider(face, "15m")
    provider.open(("AAA",), "15m")
    face.quotes = [
        {"symbol": "AAA", "price": -5.0, "timestamp": "2026-01-05 09:15:00"},
        {"symbol": "ZZZ", "price": 10.0, "timestamp": "2026-01-05 09:15:00"},
        {"symbol": "AAA", "price": 10.0, "timestamp": "bad-stamp"},
        "not-a-dict",  # type: ignore[list-item]
    ]
    assert provider.poll() == ()
    assert provider.health()[0] is True
