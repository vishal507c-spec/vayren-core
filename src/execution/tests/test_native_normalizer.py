"""Contract tests for execution.market_data.native_normalizer (stream gate, reordering, stats)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from execution.market_data import native_normalizer as nn  # noqa: E402


def test_normalizer_lifecycle_and_sequence() -> None:
    gate = nn.NativeStreamGate(
        max_reorder_buffer=10,
        stale_after_seconds=5.0,
        heartbeat_timeout_seconds=2.0,
    )
    try:
        # Observe sequence 1
        delivered, dropped, stats = gate.observe_event("AAPL", seq=1, token=101, now_epoch=100.0)
        assert delivered == (101,)
        assert dropped == ()
        assert stats.accepted == 1

        # Duplicate sequence 1
        delivered2, dropped2, stats2 = gate.observe_event("AAPL", seq=1, token=102, now_epoch=100.1)
        assert delivered2 == ()
        assert dropped2 == (102,)
        assert stats2.duplicates == 1

        # Sequence 2
        delivered3, dropped3, stats3 = gate.observe_event("AAPL", seq=2, token=103, now_epoch=100.2)
        assert delivered3 == (103,)
        assert stats3.accepted == 2

        # Check health
        healthy, reason, _ = gate.check_health("AAPL", now_epoch=100.5)
        assert healthy is True

        # Stale health check (after 10s without heartbeat/event)
        stale_healthy, stale_reason, _ = gate.check_health("AAPL", now_epoch=120.0)
        assert stale_healthy is False
    finally:
        gate.close()
