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
import math
import time
from collections.abc import Callable
from typing import Any

from execution.events import CandleEvent, MarketEvent
from execution.market_data.native_normalizer import (
    candle_stale_after,
    gap_reason,
    gap_stale,
    stale_floor,
)
from execution.market_data.provider import MarketDataProvider
from market import Bucket, bucket_start, fold_tick, session_anchor_seconds, timeframe_seconds

# One open bucket: its start stamp and the kernel's OHLCV fold.
_Open = tuple[str, Bucket]


class BrokerFeedProvider(MarketDataProvider):
    """Candle builder over an injected broker quote face."""

    def __init__(
        self,
        face: Any,
        timeframe: str = "15m",
        session_anchor: str = "09:15",
        clock: Callable[[], float] | None = None,
        stale_after_s: float | None = None,
        history_face: Any | None = None,
    ) -> None:
        self._face = face
        self._timeframe = timeframe
        self._anchor_s = session_anchor_seconds(session_anchor)
        self._clock = clock or time.time
        # Candle-close feeds are not tick feeds: 300s would flag every 15m+
        # timeframe as stale between legitimate candles. Rust derives the
        # window from the timeframe unless the caller pins one explicitly.
        self._stale_after_override = stale_after_s is not None
        self._stale_after_s = (
            stale_floor(stale_after_s)
            if stale_after_s is not None
            else candle_stale_after(float(self._size_s() or 60))
        )
        self._subscribed: tuple[str, ...] = ()
        self._open = False
        self._dropped = False
        self._seq: dict[str, int] = {}
        self._buckets: dict[str, _Open] = {}
        self._emitted: dict[str, str] = {}
        self._last_seen: dict[str, float] = {}
        self._last_dayvol: dict[str, int] = {}
        self._last_close: dict[str, float] = {}
        self._last_tick_time: dict[str, str] = {}
        self._last_error = ""
        self._history_face = history_face
        self._backfill_events: list[CandleEvent] = []

    @property
    def name(self) -> str:
        return "broker-feed"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("candle-close", "heartbeat", "reconnect")

    @property
    def last_emitted(self) -> dict[str, str]:
        return dict(self._emitted)

    @property
    def last_close(self) -> dict[str, float]:
        """Latest seen price per symbol (marks without extra reads)."""
        return dict(self._last_close)

    @property
    def last_tick_time(self) -> dict[str, str]:
        """Latest seen tick timestamp per symbol."""
        return dict(self._last_tick_time)

    def set_last_emitted(self, symbol: str, timestamp: str) -> None:
        """Seed the emitted watermark from startup historical recovery."""
        self._emitted[symbol] = timestamp

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:
        self._timeframe = timeframe
        if not self._stale_after_override:
            self._stale_after_s = candle_stale_after(float(self._size_s() or 60))
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
            self._last_close.pop(symbol, None)
            self._last_tick_time.pop(symbol, None)
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
        if self._backfill_events:
            events.extend(self._backfill_events)
            self._backfill_events.clear()
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
            s
            for s in self._subscribed
            if gap_stale(now, self._last_seen.get(s, now), self._stale_after_s)
        ]
        if stale:
            return False, f"{gap_reason()}: {', '.join(sorted(stale))}"
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
        self._last_close = {}
        self._last_tick_time = {}
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
            if self._subscribed:
                try:
                    self._face.subscribe(tuple(self._subscribed))
                except Exception as exc:
                    self._last_error = f"feed resubscribe failed: {exc}"
            self._handle_reconnect_backfill()

    def _handle_reconnect_backfill(self) -> None:
        if self._history_face is None:
            return
        from datetime import datetime

        from app.services.session_recovery import VENUE_TZ, backfill_candle_gap

        now_dt = datetime.fromtimestamp(self._clock(), VENUE_TZ)
        for symbol in self._subscribed:
            # Interrupted partial bucket is invalidated so old ticks don't pollute new intervals
            self._buckets.pop(symbol, None)
            last_ts = self._emitted.get(symbol)
            if not last_ts:
                continue
            try:
                recovered = backfill_candle_gap(
                    self._history_face, symbol, self._timeframe, last_ts, now_dt=now_dt
                )
                for ev in recovered:
                    self._seq[symbol] = self._seq.get(symbol, 0) + 1
                    event_with_seq = CandleEvent(
                        symbol=ev.symbol,
                        timestamp=ev.timestamp,
                        seq=self._seq[symbol],
                        source=self.name,
                        open=ev.open,
                        high=ev.high,
                        low=ev.low,
                        close=ev.close,
                        volume=ev.volume,
                        timeframe=self._timeframe,
                        is_closed=True,
                    )
                    self._backfill_events.append(event_with_seq)
                    self._emitted[symbol] = ev.timestamp
            except Exception as exc:
                self._last_error = f"reconnect backfill failed for {symbol}: {exc}"

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
        if symbol not in self._subscribed:
            if symbol.endswith("-EQ") and symbol[:-3] in self._subscribed:
                symbol = symbol[:-3]
            elif f"{symbol}-EQ" in self._subscribed:
                symbol = f"{symbol}-EQ"
            else:
                return None
        if len(stamp) < 19:
            return None
        try:
            price = float(quote.get("price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        self._last_close[symbol] = price
        if stamp:
            self._last_tick_time[symbol] = stamp
        try:
            dayvol = int(quote.get("volume", 0) or 0)
        except (TypeError, ValueError):
            dayvol = 0
        if not math.isfinite(dayvol) or dayvol < 0:
            dayvol = 0
        size_s = self._size_s()
        if not size_s:
            return None
        bucket = bucket_start(stamp, size_s, self._anchor_s)
        held = self._buckets.get(symbol)
        fresh = held is None or held[0] != bucket
        emitted: CandleEvent | None = None
        if (
            held is not None
            and fresh
            and (symbol not in self._emitted or held[0] > self._emitted[symbol])
        ):
            emitted = self._close_bucket(symbol, held)
        previous = held[1] if held is not None and not fresh else None
        self._buckets[symbol] = (
            bucket,
            fold_tick(previous, price, dayvol, self._last_dayvol.get(symbol)),
        )
        self._last_dayvol[symbol] = dayvol
        self._last_seen[symbol] = self._clock()
        return emitted

    def _close_bucket(self, symbol: str, held: _Open) -> CandleEvent:
        start, (open_, high, low, close, volume) = held
        self._seq[symbol] = self._seq.get(symbol, 0) + 1
        self._emitted[symbol] = start
        return CandleEvent(
            symbol=symbol,
            timestamp=start,
            seq=self._seq[symbol],
            source=self.name,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=int(volume),
            timeframe=self._timeframe,
            is_closed=True,
        )
