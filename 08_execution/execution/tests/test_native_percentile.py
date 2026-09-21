"""Parity: Rust latency percentile vs the frozen Python ranking rule."""

from __future__ import annotations

import random

from execution.native_execution import native_percentile


def _ref_pct(ordered: list[float], pct: float) -> float:
    if not ordered:
        return 0.0
    rank = min(len(ordered) - 1, max(0, int(pct / 100.0 * len(ordered))))
    return ordered[rank]


def test_empty_sample_set_ages_out_at_zero() -> None:
    assert native_percentile([], 50) == 0.0


def test_rank_never_runs_past_the_last_sample() -> None:
    samples = [1.0, 2.0, 3.0, 4.0]
    assert native_percentile(samples, 50) == 3.0
    assert native_percentile(samples, 99) == 4.0
    assert native_percentile(samples, 100) == 4.0


def test_percentile_fuzz() -> None:
    rng = random.Random(20260924)
    for _ in range(400):
        samples = sorted(rng.uniform(0.0, 5.0) for _ in range(rng.randint(0, 40)))
        for pct in (0, 1, 25, 50, 75, 95, 99, 100, 120):
            assert native_percentile(samples, pct) == _ref_pct(samples, float(pct)), (
                pct,
                samples[-5:],
            )
