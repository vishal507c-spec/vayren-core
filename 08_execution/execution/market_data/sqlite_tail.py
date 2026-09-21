"""SQLite-tail live provider — new closed candles from per-stock SQLite DBs.

Bridges the only always-available market-data truth (the per-stock SQLite
store, also fed by the historical downloader) to the
:class:`MarketDataProvider` protocol, so a live session can run without any
network transport, credentials or broker SDK.

Semantics (all deliberate, all fail-closed):
- ``open()`` watermarks every subscribed symbol at its current latest row.
  History is NEVER replayed — warmup bars come from the service through the
  repository, exactly like ``PaperService``.
- ``poll()`` emits rows strictly newer than the watermark, EXCEPT the
  newest row per symbol, which is still forming and is withheld until a
  newer row proves it closed. Emission is exactly-once (watermark only
  advances past emitted rows) and chronological (sorted by timestamp).
- ``health()`` reports stale data when no fresh row arrived within
  ``stale_after_s`` — the session's normalizer surfaces ``STALE_DATA``.
- ``disconnect()`` simulates a transport drop (polls go empty, health
  fails); ``reconnect()`` resumes from kept watermarks — nothing is ever
  re-emitted, so reconnects cannot duplicate orders.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from market import SymbolRepository, closed_count

from execution.events import CandleEvent, MarketEvent
from execution.market_data.native_normalizer import gap_reason, gap_stale, stale_floor
from execution.market_data.provider import MarketDataProvider

_POLL_WINDOW = 8


class SqliteTailProvider(MarketDataProvider):
    """Poll-based closed-candle tail over ``<data_dir>/<SYMBOL>.db`` files."""

    def __init__(
        self,
        data_dir: str | Path,
        timeframe: str = "15m",
        clock: Callable[[], float] | None = None,
        stale_after_s: float = 300.0,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._timeframe = timeframe
        self._clock = clock or time.time
        self._stale_after_s = stale_floor(stale_after_s)
        self._subscribed: tuple[str, ...] = ()
        self._watermarks: dict[str, str] = {}
        self._detection: dict[str, tuple[int, int] | None] = {}
        self._seq: dict[str, int] = {}
        self._last_emitted: dict[str, str] = {}
        self._last_close: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}
        self._open = False
        self._dropped = False
        self._last_error = ""

    @property
    def name(self) -> str:
        return "sqlite-tail"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("candle-close", "heartbeat", "reconnect")

    @property
    def last_emitted(self) -> dict[str, str]:
        """Latest emitted closed-candle timestamp per symbol (UI/ops)."""
        return dict(self._last_emitted)

    @property
    def last_close(self) -> dict[str, float]:
        """Latest seen close price per symbol (marks without extra reads)."""
        return dict(self._last_close)

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:
        """Subscribe and watermark at current latest rows (idempotent)."""
        self._timeframe = timeframe
        self.subscribe(symbols)
        self._open = True
        self._dropped = False

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        """Add symbols, watermarking newcomers at their latest row."""
        now = self._clock()
        for symbol in symbols:
            if symbol not in self._subscribed:
                self._subscribed = (*self._subscribed, symbol)
            if symbol not in self._watermarks:
                latest = self._latest_timestamp(symbol)
                if latest is not None:
                    self._watermarks[symbol] = latest
                self._last_seen[symbol] = now
        self._open = True

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        """Drop symbols and forget their watermarks (idempotent)."""
        dropped = set(symbols)
        self._subscribed = tuple(s for s in self._subscribed if s not in dropped)
        for symbol in dropped:
            self._watermarks.pop(symbol, None)
            self._detection.pop(symbol, None)
            self._seq.pop(symbol, None)
            self._last_emitted.pop(symbol, None)
            self._last_close.pop(symbol, None)
            self._last_seen.pop(symbol, None)

    def poll(self) -> tuple[MarketEvent, ...]:
        """Emit newly closed candles, exactly once, oldest first.

        Detection is cached per symbol (first poll only) and passed as a
        hint, so the steady-state cost per poll is one bounded window fetch
        per symbol — no per-poll sample scans, no full-history reads.
        """
        if not self._open or self._dropped:
            return ()
        events: list[CandleEvent] = []
        try:
            repository = SymbolRepository(self._data_dir)
            for symbol in self._subscribed:
                if symbol not in self._detection:
                    self._detection[symbol] = self._detect(repository, symbol)
                fresh = self._fresh_closed(repository, symbol)
                for bar in fresh:
                    self._seq[symbol] = self._seq.get(symbol, 0) + 1
                    self._last_close[symbol] = float(bar.close)
                    events.append(
                        CandleEvent(
                            symbol=bar.symbol,
                            timestamp=bar.timestamp,
                            seq=self._seq[symbol],
                            source=self.name,
                            open=bar.open,
                            high=bar.high,
                            low=bar.low,
                            close=bar.close,
                            volume=bar.volume,
                            timeframe=self._timeframe,
                            is_closed=True,
                        )
                    )
                    self._last_emitted[symbol] = bar.timestamp
                if fresh:
                    self._watermarks[symbol] = fresh[-1].timestamp
                    self._last_seen[symbol] = self._clock()
        except Exception as exc:
            self._last_error = str(exc) or "provider error"
            return tuple(events)
        self._last_error = ""
        events.sort(key=lambda e: (e.timestamp, e.symbol))
        return tuple(events)

    def health(self) -> tuple[bool, str]:
        """(healthy, reason) — transport, subscription and freshness."""
        if not self._open:
            return False, "not opened"
        if self._dropped:
            return False, "transport dropped"
        if not self._subscribed:
            return False, "no symbols subscribed"
        if self._last_error:
            return False, f"provider error: {self._last_error}"
        now = self._clock()
        stale = [
            s
            for s in self._subscribed
            if gap_stale(now, self._last_seen.get(s, now), self._stale_after_s)
        ]
        if stale:
            return False, f"{gap_reason()}: {', '.join(sorted(stale))}"
        return True, "streaming"

    def close(self) -> None:
        """Stop streaming and forget all subscription state."""
        self._open = False
        self._dropped = False
        self._subscribed = ()
        self._watermarks = {}
        self._detection = {}
        self._seq = {}
        self._last_emitted = {}
        self._last_close = {}
        self._last_seen = {}
        self._last_error = ""

    def disconnect(self) -> None:
        """Drop the transport, keeping watermarks for a clean reconnect."""
        self._dropped = True

    def reconnect(self) -> None:
        """Re-establish the transport; watermarks prevent re-emission."""
        if self._open:
            self._dropped = False
            self._last_error = ""

    # ── internals ───────────────────────────────────────────────

    def _latest_timestamp(self, symbol: str) -> str | None:
        """Latest stored row for a symbol, or None when unavailable."""
        try:
            bars = SymbolRepository(self._data_dir).get_candles(symbol, 1)
        except Exception:
            return None
        return bars[-1].timestamp if bars else None

    def _detect(self, repository: SymbolRepository, symbol: str) -> tuple[int, int] | None:
        """One-shot (base_seconds, session_start); None on unreadable stores."""
        try:
            return repository.detect(symbol)
        except Exception:
            return None

    def _closed(self, rows: list, fresh: list) -> list:
        """The kernel's cut: fresh rows, minus the newest one still forming."""
        forming = fresh[-1].timestamp == rows[-1].timestamp
        return fresh[: closed_count(len(fresh), forming)]

    def _fresh_closed(self, repository: SymbolRepository, symbol: str) -> list:
        """Rows newer than the watermark, minus the still-forming newest."""
        try:
            rows = repository.get_candles_timeframe(
                symbol, self._timeframe, _POLL_WINDOW, detection=self._detection.get(symbol)
            )
        except Exception:
            return []
        watermark = self._watermarks.get(symbol)
        if watermark is None:
            if rows:
                self._watermarks[symbol] = rows[-1].timestamp
            return []
        if not rows or rows[-1].timestamp <= watermark:
            return []
        if rows[0].timestamp > watermark:
            return self._catch_up_gap(repository, symbol, watermark)
        fresh = [bar for bar in rows if bar.timestamp > watermark]
        if not fresh:
            return []
        return self._closed(rows, fresh)

    def _catch_up_gap(self, repository: SymbolRepository, symbol: str, watermark: str) -> list:
        """Long-gap recovery: bounded fetch from the watermark, forming withheld.

        Runs only when more rows arrived than the poll window holds (long
        disconnect). Intermediate rows are emitted in order — never skipped.
        """
        try:
            rows = repository.get_candles_timeframe(
                symbol, self._timeframe, None, watermark, None, self._detection.get(symbol)
            )
        except Exception:
            return []
        fresh = [bar for bar in rows if bar.timestamp > watermark]
        if not fresh:
            return []
        return self._closed(rows, fresh)
