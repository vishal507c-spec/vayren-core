"""Parity pin for the sandbox venue's fill economics.

``SandboxBroker`` used to carry its own copy of the paper fill rule table
(reference ± slippage, limit clamp, cash affordability, commission on
notional, capital fold). ``_ref_fill`` freezes that retired math as the
oracle: the venue now answers from the same Rust simulated-fill kernel
``PaperBroker`` uses, so paper ↔ sandbox parity is structural, not copied.
"""

from __future__ import annotations

import itertools

from execution.broker.sandbox import SandboxBroker
from execution.models.order import OrderPlan

TS = "2026-01-05T09:30:00+00:00"


def _ref_fill(
    side: str,
    order_type: str,
    limit_price: float | None,
    remaining: float,
    capital: float,
    want_qty: float,
    reference_price: float,
    slippage_pct: float,
    commission_pct: float,
) -> tuple[float, float, float, float, bool] | None:
    """The retired sandbox rule table, byte for byte."""
    if reference_price <= 0:
        return None
    slip = reference_price * (slippage_pct / 100.0)
    if order_type == "LIMIT" and limit_price is not None:
        if side == "BUY":
            fill_price = min(limit_price, reference_price + slip)
        else:
            fill_price = max(limit_price, reference_price - slip)
    elif side == "BUY":
        fill_price = reference_price + slip
    else:
        fill_price = reference_price - slip
    if fill_price <= 0:
        return None
    if side == "BUY":
        affordable = capital / fill_price if fill_price > 0 else 0.0
        fill_qty = min(remaining, want_qty, affordable)
    else:
        fill_qty = min(remaining, want_qty)
    if fill_qty <= 0:
        return None
    notional = fill_price * fill_qty
    commission = notional * (commission_pct / 100.0)
    if side == "BUY":
        new_capital = capital - (notional + commission)
    else:
        new_capital = capital + (notional - commission)
    return fill_price, fill_qty, commission, new_capital, fill_qty < remaining


def _venue_fill(
    side: str,
    order_type: str,
    limit_price: float | None,
    remaining: float,
    capital: float,
    want_qty: float,
    reference_price: float,
    slippage_pct: float,
    commission_pct: float,
) -> tuple[float, float, float, float, bool] | None:
    broker = SandboxBroker(
        capital=capital, slippage_pct=slippage_pct, commission_pct=commission_pct
    )
    plan = OrderPlan(
        intent_id="i1",
        symbol="T",
        side=side,
        quantity=remaining,
        order_type=order_type,
        limit_price=limit_price,
    )
    info = {"broker_order_id": "SANDBOX-1", "plan": plan, "state": "SUBMITTED", "filled_qty": 0.0}
    fill = broker._fill(info, "c1", want_qty, reference_price, TS)  # noqa: SLF001
    if fill is None:
        return None
    return fill.fill_price, fill.fill_qty, fill.commission, broker.capital, fill.partial


GRID = list(
    itertools.product(
        ("BUY", "SELL"),
        ("MARKET", "LIMIT"),
        (None, 90.0, 100.0, 110.0),
        (0.0, -1.0, 5.0, 100.0, 1000.0),
        (0.0, 1.0, 5.0, 999.0),
        (5.0, 20.0),
        ((0.0, 0.0), (0.02, 0.03), (5.0, 1.0)),
    )
)


def test_the_venue_matches_the_retired_rule_table() -> None:
    for side, order_type, limit_price, reference_price, want_qty, remaining, (
        slip,
        comm,
    ) in GRID:
        # The venue's own constructor rejects non-positive capital, so the
        # grid stays inside reachable states.
        for capital in (1.0, 100.0, 100_000.0):
            expected = _ref_fill(
                side,
                order_type,
                limit_price,
                remaining,
                capital,
                want_qty,
                reference_price,
                slip,
                comm,
            )
            actual = _venue_fill(
                side,
                order_type,
                limit_price,
                remaining,
                capital,
                want_qty,
                reference_price,
                slip,
                comm,
            )
            assert actual == expected, (
                side,
                order_type,
                limit_price,
                reference_price,
                want_qty,
                remaining,
                capital,
                slip,
                comm,
            )


def test_scripted_partial_fills_fold_capital_through_the_kernel() -> None:
    broker = SandboxBroker(capital=1000.0, slippage_pct=0.0, commission_pct=0.0)
    broker.connect()
    plan = OrderPlan(intent_id="i1", symbol="T", side="BUY", quantity=10.0)
    broker.place_order(plan, "c1")
    broker.set_fill_policy("c1", "partial:4")
    fill = broker.settle("c1", 50.0, TS)
    assert fill is not None
    assert (fill.fill_qty, fill.fill_price, fill.partial) == (4.0, 50.0, True)
    assert broker.capital == 800.0
    # `full` asks for the plan quantity; only the 6 units still unfilled fit.
    broker.set_fill_policy("c1", "full")
    second = broker.settle("c1", 50.0, TS)
    assert second is not None
    assert (second.fill_qty, second.partial) == (6.0, False)
    assert broker.capital == 500.0
