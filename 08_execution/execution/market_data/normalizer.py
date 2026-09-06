"""Stream normalizer — sequence, duplicates, order, staleness, heartbeat.

Every provider is untrusted at the transport edge: sequence gaps, duplicate
deliveries, out-of-order arrivals and silent stalls are normalized HERE so
downstream stages (strategy, risk, execution) see a clean ordered stream.
Dropped/duplicates are counted, never silently swallowed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from execution.events import HeartbeatEvent, MarketEvent


@dataclass
class StreamStats:
    accepted: int = 0
    duplicates: int = 0
    # Arrivals jumping ahead of the expected seq (predecessors missing at
    # arrival; they may still fill from the buffer).
    gaps: int = 0
    # Buffer-overflow drops: genuine data loss, counted loudly.
    evicted: int = 0
    heartbeats: int = 0
    stale_flags: int = 0


@dataclass
class NormalizerConfig:
    max_reorder_buffer: int = 128
    stale_after_seconds: float = 60.0
    heartbeat_timeout_seconds: float = 30.0


class StreamNormalizer:
    """Per-symbol ordered gate with duplicate suppression and health signals."""

    def __init__(self, config: NormalizerConfig | None = None) -> None:
        self._config = config if config is not None else NormalizerConfig()
        self._last_seq: dict[str, int] = {}
        self._buffer: dict[str, deque[MarketEvent]] = {}
        self._last_event_epoch: dict[str, float] = {}
        self._last_heartbeat_epoch: dict[str, float] = {}
        self.stats = StreamStats()
        self._stale: set[str] = set()

    def observe(self, event: MarketEvent, now_epoch: float) -> tuple[MarketEvent, ...]:
        """Accept one raw event; return newly deliverable events in order."""
        if isinstance(event, HeartbeatEvent):
            self.stats.heartbeats += 1
            self._last_heartbeat_epoch[event.symbol] = now_epoch
            self._stale.discard(event.symbol)
            return ()
        key = f"{event.symbol}"
        last = self._last_seq.get(key, 0)
        if event.seq <= last:
            self.stats.duplicates += 1
            return ()
        buf = self._buffer.setdefault(key, deque())
        if event.seq > last + 1 + len(buf):
            self.stats.gaps += 1
        buf.append(event)
        if len(buf) > self._config.max_reorder_buffer:
            lowest = min(buf, key=lambda e: e.seq)
            if lowest.seq == self._last_seq.get(key, 0) + 1:
                # Dropping the next deliverable: skip exactly one lost event.
                buf.remove(lowest)
                self._last_seq[key] = lowest.seq
            else:
                # Unfillable hole below `lowest`: jump the watermark to it,
                # keeping `lowest` buffered for delivery. Late arrivals below
                # the jumped watermark count as duplicates from here on.
                self._last_seq[key] = lowest.seq - 1
            self.stats.evicted += 1
        ready: list[MarketEvent] = []
        expected = self._last_seq.get(key, 0) + 1
        ordered = sorted(buf, key=lambda e: e.seq)
        for candidate in ordered:
            if candidate.seq == expected:
                ready.append(candidate)
                expected += 1
            elif candidate.seq < expected:
                self.stats.duplicates += 1
            else:
                break
        for delivered in ready:
            buf.remove(delivered)
        if ready:
            self._last_seq[key] = ready[-1].seq
            self._last_event_epoch[key] = now_epoch
            self.stats.accepted += len(ready)
        return tuple(ready)

    def check_health(self, symbol: str, now_epoch: float) -> tuple[bool, str]:
        """(healthy, reason): heartbeat gaps and event stalls flag stale."""
        last_hb = self._last_heartbeat_epoch.get(symbol)
        if last_hb is not None and now_epoch - last_hb > self._config.heartbeat_timeout_seconds:
            self.stats.stale_flags += 1
            self._stale.add(symbol)
            return False, "heartbeat timeout"
        last_event = self._last_event_epoch.get(symbol)
        if last_event is not None and now_epoch - last_event > self._config.stale_after_seconds:
            self.stats.stale_flags += 1
            self._stale.add(symbol)
            return False, "stale market data"
        return True, "ok"

    def is_stale(self, symbol: str) -> bool:
        return symbol in self._stale

    def expected_seq(self, symbol: str) -> int:
        return self._last_seq.get(symbol, 0) + 1


@dataclass
class NormalizedBatch:
    events: tuple[MarketEvent, ...] = ()
    stats: StreamStats = field(default_factory=StreamStats)
