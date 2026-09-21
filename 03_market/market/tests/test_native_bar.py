"""Parity: Rust candle-change rule vs the retired Python property."""

from __future__ import annotations

import random

from market.native_bar import return_pct


def _ref_pct(open: float, close: float) -> float:
    if open == 0:
        return 0.0
    return ((close - open) / open) * 100.0


def test_flat_open_answers_zero_instead_of_dividing() -> None:
    assert return_pct(0.0, 0.0) == 0.0
    assert return_pct(0.0, 900.0) == 0.0


def test_change_is_signed_and_scaled() -> None:
    assert return_pct(100.0, 105.0) == 5.0
    assert return_pct(100.0, 92.5) == -7.5
    assert return_pct(100.0, 100.0) == 0.0


def test_change_rule_fuzz() -> None:
    rng = random.Random(20260920)
    for _ in range(400):
        open = rng.choice([0.0, rng.uniform(0.01, 5000.0)])
        close = rng.uniform(0.0, 5000.0)
        assert return_pct(open, close) == _ref_pct(open, close), (open, close)
