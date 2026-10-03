"""Stream normalizer — the Python-shaped half of the transport-edge gate.

Every verdict comes from Rust: sequence watermarking, duplicate suppression,
reorder-buffer overflow, delivery order, staleness thresholds and all six
counters are decided by ``rust/vayren-core/src/normalizer.rs`` and reach this
module through :mod:`execution.market_data.native_normalizer`. What stays here
is what cannot cross an FFI boundary — the market event objects themselves,
keyed by the token the kernel hands back — plus the malformed-arrival guard at
the provider edge. Dropped/duplicates are counted by the kernel, never
silently swallowed.
"""

from __future__ import annotations

from dataclasses import astuple, dataclass, field

from execution.events import HeartbeatEvent, MarketEvent
from execution.market_data.native_normalizer import NativeStreamGate, NativeStreamStats
from execution.market_data.provider import MarketDataError


@dataclass
class StreamStats:
    """Mirror of the kernel's counters (the caller-facing shape)."""

    accepted: int = 0
    duplicates: int = 0
    gaps: int = 0
    evicted: int = 0
    heartbeats: int = 0
    stale_flags: int = 0


@dataclass
class NormalizerConfig:
    max_reorder_buffer: int = 128
    stale_after_seconds: float = 60.0
    heartbeat_timeout_seconds: float = 30.0


class StreamNormalizer:
    """Per-symbol ordered gate; the gate itself is Rust state."""

    def __init__(self, config: NormalizerConfig | None = None) -> None:
        settings = config if config is not None else NormalizerConfig()
        self._gate = NativeStreamGate(
            settings.max_reorder_buffer,
            settings.stale_after_seconds,
            settings.heartbeat_timeout_seconds,
        )
        self._pending: dict[int, MarketEvent] = {}
        self._token = 0
        self.stats = StreamStats()

    def observe(self, event: MarketEvent, now_epoch: float) -> tuple[MarketEvent, ...]:
        """Accept one raw event; return newly deliverable events in order.

        Malformed (non-MarketEvent) arrivals fail closed with
        ``MarketDataError`` — raw SDK objects must never reach the stream.
        """
        if not isinstance(event, MarketEvent):
            raise MarketDataError(
                f"malformed market event: {type(event).__name__} is not a MarketEvent"
            )
        if isinstance(event, HeartbeatEvent):
            delivered, dropped, stats = self._gate.observe_heartbeat(event.symbol, now_epoch)
        else:
            self._token += 1
            self._pending[self._token] = event
            delivered, dropped, stats = self._gate.observe_event(
                event.symbol, event.seq, self._token, now_epoch
            )
        ready = tuple(self._pending[token] for token in delivered)
        for token in (*delivered, *dropped):
            del self._pending[token]
        self._refresh(stats)
        return ready

    def check_health(self, symbol: str, now_epoch: float) -> tuple[bool, str]:
        """(healthy, reason): the kernel decides both, this method relays them."""
        healthy, reason, stats = self._gate.check_health(symbol, now_epoch)
        self._refresh(stats)
        return healthy, reason

    def is_stale(self, symbol: str) -> bool:
        return self._gate.is_stale(symbol)

    def expected_seq(self, symbol: str) -> int:
        return self._gate.expected_seq(symbol)

    def _refresh(self, stats: NativeStreamStats) -> None:
        self.stats = StreamStats(*astuple(stats))


@dataclass
class NormalizedBatch:
    events: tuple[MarketEvent, ...] = ()
    stats: StreamStats = field(default_factory=StreamStats)
