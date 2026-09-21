"""Parity: Rust position verdict kernel vs the frozen Python rules.

The references below are verbatim copies of the pre-migration
`models/position.py` rules (`quantity == 0.0` for flat, the `LONG`/`SHORT`
side of `portfolio/ledger.py`, the mark product), kept in the TEST as
oracles. The ledger's sign decides whether a position is booked at all, so
every sign corner — zero, negative zero, tiny magnitudes — is compared.
"""

from __future__ import annotations

import random

from execution.models.order import Fill
from execution.models.position import Position
from execution.native_execution import native_position_state, native_position_unrealized
from execution.portfolio.ledger import PositionLedger


def _fill(symbol: str, side: str, qty: float, price: float) -> Fill:
    return Fill(
        client_order_id="c1",
        broker_order_id=None,
        symbol=symbol,
        side=side,
        fill_qty=qty,
        fill_price=price,
        commission=0.0,
        timestamp="2026-09-20T09:30:00",
    )


def _ref_flat(quantity: float) -> bool:
    return quantity == 0.0


def _ref_side(quantity: float) -> str:
    return "LONG" if quantity > 0 else "SHORT"


def _ref_unrealized(quantity: float, avg_price: float, mark_price: float) -> float:
    if _ref_flat(quantity):
        return 0.0
    return (mark_price - avg_price) * quantity


def test_flat_leg_reports_no_side():
    flat, side = native_position_state(0.0)
    assert flat and side is None
    assert Position(symbol="X").flat
    assert Position(symbol="X").unrealized(999.0) == 0.0


def test_signed_legs_name_their_side():
    assert native_position_state(5.0) == (False, "LONG")
    assert native_position_state(-5.0) == (False, "SHORT")


def test_negative_zero_is_flat_not_short():
    """`-0.0 > 0` is False, so a naive sign test would call the leg SHORT."""
    assert native_position_state(-0.0) == (True, None)
    assert native_position_unrealized(-0.0, 100.0, 120.0) == 0.0


def test_short_leg_marks_below_entry():
    pos = Position(symbol="X", quantity=-10.0, avg_price=100.0)
    assert pos.unrealized(90.0) == 100.0
    assert pos.unrealized(110.0) == -100.0


def test_ledger_strategy_state_uses_the_kernel_side():
    ledger = PositionLedger(starting_capital=100_000.0)
    assert ledger.strategy_state_for("X") == (0.0, None, None)
    ledger.apply_fill(_fill("X", "SELL", 3.0, 50.0))
    assert ledger.strategy_state_for("X") == (-3.0, "SHORT", 50.0)
    ledger.apply_fill(_fill("X", "BUY", 3.0, 50.0))
    assert ledger.strategy_state_for("X") == (0.0, None, None)


def test_position_verdicts_match_the_old_rules_on_boundaries():
    for quantity in (0.0, -0.0, 1e-12, -1e-12, 1.0, -1.0, 1e18, -1e18):
        flat, side = native_position_state(quantity)
        assert flat == _ref_flat(quantity)
        assert side == (None if flat else _ref_side(quantity))


def test_position_verdict_fuzz():
    rng = random.Random(20260920)
    for _ in range(600):
        quantity = rng.choice([0.0, -0.0]) if rng.random() < 0.1 else rng.uniform(-500.0, 500.0)
        avg = rng.uniform(0.01, 5000.0)
        mark = rng.uniform(0.01, 5000.0)
        flat, side = native_position_state(quantity)
        assert flat == _ref_flat(quantity)
        if flat:
            assert side is None
        else:
            assert side == _ref_side(quantity)
        assert native_position_unrealized(quantity, avg, mark) == _ref_unrealized(
            quantity, avg, mark
        )
