"""Replay provider — deterministic event source for paper/replay/testing.

Replays a fixed tape of normalized events (or market ``Bar`` rows converted
to closed candles), so strategy behavior is reproducible and the paper path
needs no network, no credentials and no broker SDK.
"""

from __future__ import annotations

from market import Bar

from execution.events import CandleEvent, MarketEvent
from execution.market_data.provider import MarketDataProvider


def bars_to_candles(
    bars: tuple[Bar, ...], timeframe: str, source: str = "replay"
) -> tuple[CandleEvent, ...]:
    """Convert ascending Bars into closed CandleEvents with per-symbol seq."""
    out: list[CandleEvent] = []
    for seq, bar in enumerate(bars, start=1):
        out.append(
            CandleEvent(
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                seq=seq,
                source=source,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                timeframe=timeframe,
                is_closed=True,
            )
        )
    return tuple(out)


class ReplayProvider(MarketDataProvider):
    """Synchronous tape player. ``poll()`` drains in fixed-size chunks."""

    def __init__(self, events: tuple[MarketEvent, ...], chunk_size: int = 1) -> None:
        self._events = tuple(events)
        self._chunk_size = max(1, int(chunk_size))
        self._cursor = 0
        self._open = False
        self._subscribed: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return "replay"

    @property
    def capabilities(self) -> tuple[str, ...]:
        kinds = {type(event).__name__ for event in self._events}
        caps = []
        if "CandleEvent" in kinds:
            caps.append("candle-close")
        if "TradeEvent" in kinds:
            caps.append("trade")
        if "QuoteEvent" in kinds:
            caps.append("quote")
        if "OrderBookEvent" in kinds:
            caps.append("order-book")
        caps.append("heartbeat")
        return tuple(caps)

    def connect(self) -> None:
        """Prepare the tape transport without subscribing (FINAL §F)."""
        self._open = True

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        self._open = True
        self._subscribed = tuple(symbols)

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        """Add symbols to the replay subscription set (idempotent, M8 §9)."""
        self._subscribed = tuple(dict.fromkeys((*self._subscribed, *symbols)))
        self._open = True

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        """Remove symbols from the replay subscription set (idempotent)."""
        dropped = set(symbols)
        self._subscribed = tuple(s for s in self._subscribed if s not in dropped)

    def poll(self) -> tuple[MarketEvent, ...]:
        if not self._open:
            return ()
        chunk = self._events[self._cursor : self._cursor + self._chunk_size]
        self._cursor += len(chunk)
        return chunk

    def health(self) -> tuple[bool, str]:
        if not self._open:
            return False, "not opened"
        if self._cursor >= len(self._events):
            return True, "tape exhausted"
        return True, "streaming"

    def close(self) -> None:
        self._open = False

    def disconnect(self) -> None:
        """Drop transport, keeping subscription memory for reconnect."""
        self._open = False

    def reconnect(self) -> None:
        """Re-establish transport, resuming the subscription set and tape."""
        self._open = True

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self._events)
