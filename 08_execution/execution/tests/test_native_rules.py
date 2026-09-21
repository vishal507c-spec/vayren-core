"""Parity: Rust execution rule kernels vs the retired Python copies.

Each reference below is a verbatim copy of logic that used to decide live
behaviour inside Python (`session.py` sizing, `planner.py` validation,
`modes.py` flag packing). The kernel now owns those answers; the copies stay
here as oracles so drift fails a test instead of changing an order size.
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

from execution.modes import ModeGates
from execution.native_execution import (
    native_default_quantity,
    native_flags_to_mask,
    native_mask_to_flags,
    native_multiplier_problem,
    native_narrow_multiplier,
)


def _ref_quantity(capital: float, price: float, max_qty: float) -> float:
    """Old ``LiveSession._default_quantity``, verbatim."""
    if price <= 0:
        return 0.0
    affordable = capital / price
    return max(0.0, min(affordable, max_qty))


def _ref_narrow(raw: object) -> float:
    """Old ``session._size_multiplier``, verbatim."""
    if isinstance(raw, bool):
        return 1.0
    if isinstance(raw, (int, float)) and 0.0 < raw <= 1.0:
        return float(raw)
    return 1.0


def _ref_mask(flags: tuple[bool, ...]) -> int:
    """Bit i is field i of ``ModeGates``, in declaration order."""
    return sum(bit << index for index, bit in enumerate(flags))


def test_default_quantity_matches_the_retired_sizing_rule() -> None:
    edges = [
        (100_000.0, 250.0, 1000.0),
        (100_000.0, 250.0, 100.0),
        (0.0, 250.0, 100.0),
        (-5.0, 250.0, 100.0),
        (100_000.0, 0.0, 100.0),
        (100_000.0, -1.0, 100.0),
        (100_000.0, 250.0, -4.0),
        (1.0, 500.0, 10.0),
    ]
    for capital, price, max_qty in edges:
        assert native_default_quantity(capital, price, max_qty) == _ref_quantity(
            capital, price, max_qty
        ), (capital, price, max_qty)

    rng = random.Random(20260920)
    for _ in range(400):
        capital = rng.uniform(-1_000.0, 5_000_000.0)
        price = rng.uniform(-10.0, 4_000.0)
        max_qty = rng.uniform(-1.0, 5_000.0)
        assert native_default_quantity(capital, price, max_qty) == _ref_quantity(
            capital, price, max_qty
        ), (capital, price, max_qty)


def test_advisory_multiplier_narrows_like_the_retired_rule() -> None:
    samples: list[object] = [
        True,
        False,
        1,
        0,
        -1,
        1.0,
        0.25,
        1e-9,
        1.0000001,
        2.0,
        0.0,
        -0.5,
        math.nan,
        math.inf,
        "0.5",
        None,
    ]
    for raw in samples:
        assert native_narrow_multiplier(raw) == _ref_narrow(raw), raw
    rng = random.Random(7)
    for _ in range(200):
        raw = rng.choice([rng.uniform(-2.0, 3.0), rng.randint(-3, 3)])
        assert native_narrow_multiplier(raw) == _ref_narrow(raw), raw


def test_the_kernel_states_the_multiplier_rule_once() -> None:
    assert native_multiplier_problem(1.0) == ""
    assert native_multiplier_problem(0.5) == ""
    for bad in [0.0, -0.5, 1.5, math.nan]:
        assert native_multiplier_problem(bad) == "size_multiplier must be in (0, 1]", bad


def test_preferences_reject_what_the_kernel_calls_unusable() -> None:
    from execution.planner import ExecutionPreferences

    with pytest.raises(ValueError, match="size_multiplier"):
        ExecutionPreferences(size_multiplier=1.5)
    assert ExecutionPreferences(size_multiplier=0.5).size_multiplier == 0.5


def test_gate_flags_pack_and_unpack_in_the_kernels_bit_order() -> None:
    for flags in itertools.product((False, True), repeat=5):
        gates = ModeGates(*flags)
        assert gates.mask == _ref_mask(flags), flags
        assert native_flags_to_mask(flags) == _ref_mask(flags), flags
        assert native_mask_to_flags(gates.mask) == flags, flags
