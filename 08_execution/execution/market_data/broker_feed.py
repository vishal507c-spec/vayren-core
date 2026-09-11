"""Broker-feed provider — venue push quotes → closed timeframe candles.

Bridges any UBL ``MarketDataFace``-shaped feed (duck-typed, injected — this
module never imports broker code) into the :class:`MarketDataProvider`
protocol the session drives. Quote dicts ``{symbol, price, timestamp[,
volume]}`` accumulate into timeframe buckets; only CLOSED buckets emit,
exactly once, oldest first.

Bucket alignment is session-anchor aware (default NSE ``09:15``): intraday
buckets floor from the anchor, so live buckets align with the
session-anchored history the repository serves. Daily+ buckets floor on
calendar days. Volume accumulates from per-quote day-volume deltas when
the feed reports it; feeds without volume yield zero-volume buckets
(surfaced honestly — strategies with volume filters will idle).

This is a genuine broker feed path (reconnect-safe: watermarks and the
partial bucket survive drops), but it is only as real-time as the
underlying face's poll cadence — never confused with tick streaming.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

from market import timeframe_seconds

from execution.events import CandleEvent, MarketEvent
from execution.market_data.provider import MarketDataProvider


def _bucket_start(timestamp: str, size_s: int, anchor_s: int) -> str:
    """Floor an ISO timestamp to its bucket start (same-day arithmetic)."""
    day, clock = timestamp[:10], timestamp[11:19]
    parts = clock.split(":")
    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if size_s >= 86_400:
        return f"{day} 00:00:00"
    if seconds < anchor_s:
        anchor_s -= 86_400
    index = (seconds - anchor_s) // size_s
    start = anchor_s + index * size_s
    if start < 0:
        start += 86_400
    hh, rem = divmod(start % 86_400, 3600)
    mm, ss = divmod(rem, 60)
    return f"{day} {hh:02d}:{mm:02d}:{ss:02d}"


def _parse_anchor(anchor: str) -> int:
    try:
        hh, mm = anchor.split(":")[:2]
        return int(hh) * 3600 + int(mm) * 60
    except (ValueError, AttributeError):
        return 9 * 3600 + 15 * 60


class BrokerFeedProvider(MarketDataProvider):
    """Candle builder over an injected broker quote face."""

    def __init__(
        self,
        face: Any,
        timeframe: str = "15m",
        session_anchor: str = "09:15",
        clock: Callable[[], float] | None = None,
        stale_after_s: float = 300.0,
    ) -> None:
        self._face = face
        self._timeframe = timeframe
        self._anchor_s = _parse_anchor(session_anchor)
        self._clock = clock or time.time
        self._stale_after_s = max(1.0, float(stale_after_s))
        self._subscribed: tuple[str, ...] = ()
        self._open = False
        self._dropped = False
        self._seq: dict[str, int] = {}
        self._buckets: dict[str, dict[str, Any]] = {}
        self._emitted: dict[str, str] = {}
        self._last_seen: dict[str, float] = {}
        self._last_dayvol: dict[str, int] = {}
        self._last_error = ""

    @property
    def name(self) -> str:
        return "broker-feed"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("candle-close", "heartbeat", "reconnect")

    @property
    def last_emitted(self) -> dict[str, str]:
        return dict(self._emitted)

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:
        self._timeframe = timeframe
        connect = getattr(self._face, "connect", None)
        if callable(connect):
            try:
                connect()
            except Exception as exc:
                self._last_error = f"feed connect failed: {exc}"
                return
        self.subscribe(symbols)
        self._open = True
        self._dropped = False

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        now = self._clock()
        fresh = tuple(s for s in symbols if s not in self._subscribed)
        self._subscribed = tuple(dict.fromkeys((*self._subscribed, *symbols)))
        for symbol in fresh:
            self._last_seen[symbol] = now
        try:
            self._face.subscribe(tuple(fresh))
        except Exception as exc:
            self._last_error = f"feed subscribe failed: {exc}"
            return
        self._open = True
        self._last_error = ""

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        dropped = set(symbols)
        self._subscribed = tuple(s for s in self._subscribed if s not in dropped)
        for symbol in dropped:
            self._buckets.pop(symbol, None)
            self._seq.pop(symbol, None)
            self._emitted.pop(symbol, None)
            self._last_seen.pop(symbol, None)
            self._last_dayvol.pop(symbol, None)
        try:
            self._face.unsubscribe(tuple(dropped))
        except Exception as exc:
            self._last_error = f"feed unsubscribe failed: {exc}"

    def poll(self) -> tuple[MarketEvent, ...]:
        if not self._open or self._dropped:
            return ()
        try:
            quotes = self._face.poll() or ()
        except Exception as exc:
            self._last_error = f"feed poll failed: {exc}"
            return ()
        self._last_error = ""
        events: list[CandleEvent] = []
        for quote in quotes:
            event = self._ingest(quote)
            if event is not None:
                events.append(event)
        events.sort(key=lambda e: (e.timestamp, e.symbol))
        return tuple(events)

    def health(self) -> tuple[bool, str]:
        if not self._open:
            return False, "not opened"
        if self._dropped:
            return False, "transport dropped"
        try:
            healthy, reason = self._face.health()
        except Exception as exc:
            return False, f"feed health failed: {exc}"
        if not healthy:
            return False, reason
        if not self._subscribed:
            return False, "no symbols subscribed"
        if self._last_error:
            return False, self._last_error
        now = self._clock()
        stale = [
            s for s in self._subscribed if now - self._last_seen.get(s, now) > self._stale_after_s
        ]
        if stale:
            return False, f"stale market data: {', '.join(sorted(stale))}"
        return True, "broker feed streaming"

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._face.close()
        self._open = False
        self._dropped = False
        self._subscribed = ()
        self._buckets = {}
        self._seq = {}
        self._emitted = {}
        self._last_seen = {}
        self._last_dayvol = {}
        self._last_error = ""

    def disconnect(self) -> None:
        """Drop transport; buckets and watermarks survive for reconnect."""
        self._dropped = True
        try:
            self._face.disconnect()
        except Exception as exc:
            self._last_error = f"feed disconnect failed: {exc}"

    def reconnect(self) -> None:
        try:
            self._face.reconnect()
        except Exception as exc:
            self._last_error = f"feed reconnect failed: {exc}"
            return
        if self._open:
            self._dropped = False
            self._last_error = ""

    # ── internals ───────────────────────────────────────────────

    def _size_s(self) -> int | None:
        try:
            return timeframe_seconds(self._timeframe)
        except Exception:
            return None

    def _ingest(self, quote: Any) -> CandleEvent | None:
        if not isinstance(quote, dict):
            return None
        symbol = str(quote.get("symbol", "") or "")
        stamp = str(quote.get("timestamp", "") or "")[:19]
        if symbol not in self._subscribed or len(stamp) < 19:
            return None
        try:
            price = float(quote.get("price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        try:
            dayvol = int(quote.get("volume", 0) or 0)
        except (TypeError, ValueError):
            dayvol = 0
        size_s = self._size_s()
        if not size_s:
            return None
        bucket = _bucket_start(stamp, size_s, self._anchor_s)
        current = self._buckets.get(symbol)
        if current is None or current["start"] != bucket:
            emitted = None
            if current is not None and (
                symbol not in self._emitted or current["start"] > self._emitted[symbol]
            ):
                emitted = self._close_bucket(symbol, current)
            delta = max(0, dayvol - self._last_dayvol.get(symbol, dayvol))
            self._buckets[symbol] = {
                "start": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": delta,
            }
            self._last_dayvol[symbol] = dayvol
            self._last_seen[symbol] = self._clock()
            return emitted
        current["high"] = max(current["high"], price)
        current["low"] = min(current["low"], price)
        current["close"] = price
        delta = max(0, dayvol - self._last_dayvol.get(symbol, dayvol))
        current["volume"] += delta
        self._last_dayvol[symbol] = dayvol
        self._last_seen[symbol] = self._clock()
        return None

    def _close_bucket(self, symbol: str, bucket: dict[str, Any]) -> CandleEvent:
        self._seq[symbol] = self._seq.get(symbol, 0) + 1
        self._emitted[symbol] = bucket["start"]
        return CandleEvent(
            symbol=symbol,
            timestamp=bucket["start"],
            seq=self._seq[symbol],
            source=self.name,
            open=float(bucket["open"]),
            high=float(bucket["high"]),
            low=float(bucket["low"]),
            close=float(bucket["close"]),
            volume=int(bucket["volume"]),
            timeframe=self._timeframe,
            is_closed=True,
        )
