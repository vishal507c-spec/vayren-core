"""Parity: the Rust stream gate against the retired Python rule copy.

The `_RefNormalizer` below is a verbatim test-only oracle of the logic that
used to run in production inside `execution/market_data/normalizer.py`. The
kernel in `rust/vayren-core/src/normalizer.rs` must answer identically for
every arrival — deliveries, retirements, counters and the caller's buffer
contents — because those decisions now gate the live market-data stream.
"""

from __future__ import annotations

import random

from execution.events import CandleEvent, HeartbeatEvent
from execution.market_data.native_normalizer import (
    gap_reason,
    gap_stale,
    stale_floor,
    watermark,
)
from execution.market_data.normalizer import NormalizerConfig, StreamNormalizer


def _candle(symbol: str, seq: int) -> CandleEvent:
    return CandleEvent(
        symbol=symbol,
        timestamp="2026-01-06T09:30:00+00:00",
        seq=seq,
        close=100.0,
        open=99.0,
        high=101.0,
        low=98.0,
        volume=10,
        timeframe="15m",
    )


class _RefNormalizer:
    """The rule as Python wrote it before the kernel owned it."""

    def __init__(self, max_reorder_buffer: int) -> None:
        self._max = max_reorder_buffer
        self._last: dict[str, int] = {}
        self._buffer: dict[str, list[int]] = {}
        self.accepted = 0
        self.duplicates = 0
        self.gaps = 0
        self.evicted = 0

    def observe(self, symbol: str, seq: int) -> list[int]:
        last = self._last.get(symbol, 0)
        if seq <= last:
            self.duplicates += 1
            return []
        buf = self._buffer.setdefault(symbol, [])
        if seq > last + 1 + len(buf):
            self.gaps += 1
        buf.append(seq)
        if len(buf) > self._max:
            lowest = min(buf)
            if lowest == self._last.get(symbol, 0) + 1:
                buf.remove(lowest)
                self._last[symbol] = lowest
            else:
                self._last[symbol] = lowest - 1
            self.evicted += 1
        ready: list[int] = []
        expected = self._last.get(symbol, 0) + 1
        for candidate in sorted(buf):
            if candidate == expected:
                ready.append(candidate)
                expected += 1
            elif candidate < expected:
                self.duplicates += 1
            else:
                break
        for delivered in ready:
            buf.remove(delivered)
        if ready:
            self._last[symbol] = ready[-1]
            self.accepted += len(ready)
        return ready

    def expected_seq(self, symbol: str) -> int:
        return self._last.get(symbol, 0) + 1

    def buffered(self, symbol: str) -> list[int]:
        return list(self._buffer.get(symbol, ()))


def test_fuzz_matches_the_retired_rule_arrival_by_arrival() -> None:
    rng = random.Random(20260920)
    for max_buffer in (1, 2, 3, 8):
        normalizer = StreamNormalizer(NormalizerConfig(max_reorder_buffer=max_buffer))
        reference = _RefNormalizer(max_buffer)
        watermarks = {"A": 0, "B": 0}
        for _ in range(250):
            symbol = rng.choice(("A", "B"))
            seq = max(1, watermarks[symbol] + rng.randint(-2, 4))
            watermarks[symbol] = max(watermarks[symbol], seq)
            delivered = normalizer.observe(_candle(symbol, seq), float(seq))
            expected = reference.observe(symbol, seq)
            assert [event.seq for event in delivered] == expected, (max_buffer, symbol, seq)
            assert normalizer.stats.accepted == reference.accepted
            assert normalizer.stats.duplicates == reference.duplicates
            assert normalizer.stats.gaps == reference.gaps
            assert normalizer.stats.evicted == reference.evicted
            for tracked in ("A", "B"):
                assert normalizer.expected_seq(tracked) == reference.expected_seq(tracked)
            # Nothing may leak in the caller's pending map either.
            assert len(normalizer._pending) == sum(
                len(reference.buffered(one)) for one in ("A", "B")
            )


def test_heartbeats_count_in_the_kernel_and_clear_their_own_staleness() -> None:
    normalizer = StreamNormalizer(
        NormalizerConfig(stale_after_seconds=10.0, heartbeat_timeout_seconds=5.0)
    )
    assert normalizer.observe(HeartbeatEvent(symbol="A", timestamp="t", seq=1), 100.0) == ()
    assert normalizer.stats.heartbeats == 1
    assert normalizer.check_health("A", 104.0) == (True, "ok")
    assert normalizer.check_health("A", 106.0) == (False, "heartbeat timeout")
    assert normalizer.stats.stale_flags == 1
    assert normalizer.is_stale("A") is True
    assert normalizer.observe(HeartbeatEvent(symbol="A", timestamp="t", seq=2), 107.0) == ()
    assert normalizer.is_stale("A") is False
    assert normalizer.check_health("A", 108.0) == (True, "ok")


def test_stalled_events_flag_stale_market_data() -> None:
    normalizer = StreamNormalizer(NormalizerConfig(stale_after_seconds=10.0))
    normalizer.observe(_candle("B", 1), 100.0)
    assert normalizer.check_health("B", 111.0) == (False, "stale market data")
    assert normalizer.stats.stale_flags == 1
    assert normalizer.is_stale("B") is True


def test_an_unusable_gate_config_raises_instead_of_silently_ordering() -> None:
    import pytest
    from core.native.loader import NativeBridgeError

    with pytest.raises(NativeBridgeError):
        StreamNormalizer(NormalizerConfig(max_reorder_buffer=0))
    with pytest.raises(NativeBridgeError):
        StreamNormalizer(NormalizerConfig(stale_after_seconds=float("nan")))


# ── the stateless stream rules the providers used to carry ─────────────────


def _ref_gap_stale(now_epoch: float, last_seen: float, stale_after: float) -> bool:
    """Old provider health rule: strictly older than the window."""
    return now_epoch - last_seen > stale_after


def _ref_watermark(last_seq: int, incoming_seq: int) -> int:
    """Old ``strategy_runtime`` rule: never reuse or skip a sequence."""
    return max(last_seq + 1, incoming_seq)


def test_the_staleness_window_keeps_its_one_second_floor() -> None:
    assert stale_floor(0.0) == 1.0
    assert stale_floor(-30.0) == 1.0
    assert stale_floor(0.5) == 1.0
    assert stale_floor(300.0) == 300.0


def test_the_gap_verdict_and_its_wording_come_from_the_kernel() -> None:
    assert gap_reason() == "stale market data"
    for now_epoch, last_seen, window in [
        (100.0, 100.0, 1.0),
        (101.0, 100.0, 1.0),
        (101.5, 100.0, 1.0),
        (99.0, 100.0, 1.0),
        (0.0, 0.0, 300.0),
    ]:
        assert gap_stale(now_epoch, last_seen, window) == _ref_gap_stale(
            now_epoch, last_seen, window
        ), (now_epoch, last_seen, window)


def test_the_watermark_only_moves_forward() -> None:
    for last_seq in [-1, 0, 1, 7, 2**62]:
        for incoming in [0, 1, last_seq, last_seq + 1, last_seq + 9]:
            assert watermark(last_seq, incoming) == _ref_watermark(last_seq, incoming), (
                last_seq,
                incoming,
            )
    assert watermark(2**63 - 1, 0) == 2**63 - 1
    assert watermark(5, 5) == 6
    assert watermark(5, 4) == 6
    assert watermark(5, 99) == 99
