"""Parity: Rust reconciliation kernels vs the frozen Python rules.

The references below are verbatim copies of the pre-migration
`portfolio/reconcile.py` decisions (tolerance compare, sorted symbol union,
parse-or-zero broker quantity, symmetric-difference side labelling,
UNKNOWN-equity mismatch, SAFE/WARNING/BLOCKED combination), kept in the TEST
as oracles. Reconciliation gates LIVE, so field-by-field equality —
including how a float renders — is checked, not just the verdict.
"""

from __future__ import annotations

import random

from execution.models.position import Position
from execution.portfolio.reconcile import (
    ReconciliationReport,
    reconcile_funds,
    reconcile_orders,
    reconcile_positions,
    verdict_of,
)

_SYMBOLS = ["AAA", "BBB", "", "Z@1", "n se p", "TAT&CO"]


def _positions(pairs: list[tuple[str, float]]) -> tuple[Position, ...]:
    return tuple(Position(symbol=symbol, quantity=quantity) for symbol, quantity in pairs)


def _ref_qty_of(position: dict) -> float:
    try:
        return float(position.get("quantity", 0.0))
    except Exception:
        return 0.0


def _ref_positions(
    local: list[tuple[str, float]], broker: list[dict], tolerance: float
) -> list[tuple[str, str, str, str]]:
    broker_qty = {str(p.get("symbol", "")): _ref_qty_of(p) for p in broker}
    local_qty = dict(local)
    out: list[tuple[str, str, str, str]] = []
    for symbol in sorted(set(local_qty) | set(broker_qty)):
        mine = local_qty.get(symbol, 0.0)
        theirs = broker_qty.get(symbol, 0.0)
        if abs(mine - theirs) > tolerance:
            out.append(("position", symbol, f"{mine}", f"{theirs}"))
    return out


def _ref_orders(local: tuple[str, ...], broker: tuple[str, ...]) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    for cid in sorted(set(local) ^ set(broker)):
        side = "local-only" if cid in local else "broker-only"
        out.append(("order", cid, side, side))
    return out


def _ref_money_of(payload: dict) -> float | None:
    try:
        value = payload.get("equity")
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _ref_funds(local_equity: float, broker_funds: dict, tolerance: float) -> list[tuple[str, ...]]:
    broker_equity = _ref_money_of(broker_funds)
    if broker_equity is None:
        return [("funds", "equity", f"{local_equity}", "unknown")]
    if abs(local_equity - broker_equity) > tolerance:
        return [("funds", "equity", f"{local_equity}", f"{broker_equity}")]
    return []


def _ref_status(evaluated: bool, total: int) -> str:
    if not evaluated:
        return "WARNING"
    return "BLOCKED" if total else "SAFE"


def _records(report: ReconciliationReport) -> list[tuple[str, str, str, str]]:
    return [(m.kind, m.symbol_or_id, m.local, m.broker) for m in report.mismatches]


def test_positions_mismatch_records_match_the_old_rule_field_for_field():
    local = [("REL", 10.0), ("TAT", -2.5), ("INF", 0.0)]
    broker = [
        {"symbol": "REL", "quantity": 10.0},
        {"symbol": "TAT", "quantity": "not-a-number"},
        {"symbol": "NEW", "quantity": 4.0},
    ]
    for tolerance in (1e-9, 0.5, 1e18):
        report = reconcile_positions(_positions(local), broker, tolerance)
        assert _records(report) == _ref_positions(local, broker, tolerance)
        assert report.matched == (not report.mismatches)


def test_payload_shape_variants_reach_the_kernel_unchanged():
    broker = [
        {"symbol": "A"},  # missing quantity -> zero
        {"symbol": "B", "quantity": None},
        {"symbol": "C", "quantity": [1, 2]},
        {"symbol": "D", "quantity": "3.5"},
        {"symbol": "E", "quantity": 1e300},
        {"symbol": "F", "quantity": -0.0},
        {"quantity": 7.0},  # missing symbol -> ""
    ]
    local = [(str(index * 3 - 4), float(index) / 3) for index in range(5)]
    report = reconcile_positions(_positions(local), broker, 1e-9)
    assert _records(report) == _ref_positions(local, broker, 1e-9)


def test_orders_label_both_sides_of_the_difference():
    local = ("c1", "c3", "c4")
    broker = ("c0", "c3", "c5")
    report = reconcile_orders(local, broker)
    assert _records(report) == _ref_orders(local, broker)
    assert _records(report) == [
        ("order", "c0", "broker-only", "broker-only"),
        ("order", "c1", "local-only", "local-only"),
        ("order", "c4", "local-only", "local-only"),
        ("order", "c5", "broker-only", "broker-only"),
    ]
    assert reconcile_orders((), ()).matched
    assert reconcile_orders(("",), ()).mismatches[0].symbol_or_id == ""


def test_funds_unknown_is_never_a_match():
    for payload in ({}, {"equity": None}, {"equity": "n/a"}, {"equity": {}}):
        report = reconcile_funds(100.0, payload)
        assert _records(report) == _ref_funds(100.0, payload, 1e-9)
        assert not report.matched and report.blocks_live
    assert reconcile_funds(100.0, {"equity": "100.0"}).matched
    assert reconcile_funds(100.0, {"equity": 100.0 + 1e-12}).matched


def test_verdict_status_comes_from_the_kernel():
    clean = reconcile_positions((), [])
    dirty = reconcile_positions(_positions([("X", 5.0)]), [])
    assert verdict_of((clean,)).status.value == "SAFE"
    assert not verdict_of((clean,)).blocks_live
    assert verdict_of((dirty,)).status.value == "BLOCKED"
    assert verdict_of((dirty,)).blocks_live
    unevaluated = verdict_of((), evaluated=False)
    assert unevaluated.status.value == "WARNING" and unevaluated.blocks_live
    assert unevaluated.reasons == ("reconciliation not evaluated",)
    assert verdict_of((dirty,)).reasons == ("position X: local=5.0 broker=0.0",)


def test_reconciliation_fuzz():
    rng = random.Random(20260920)
    for _ in range(250):
        local = [
            (rng.choice(_SYMBOLS), rng.choice([0.0, -0.0, round(rng.uniform(-500, 500), 9)]))
            for _ in range(rng.randint(0, 5))
        ]
        broker = [
            {
                "symbol": rng.choice(_SYMBOLS),
                "quantity": rng.choice(
                    [round(rng.uniform(-500, 500), 9), "7.5", "junk", None, {}, 0, 1e-320]
                ),
            }
            for _ in range(rng.randint(0, 5))
        ]
        tolerance = rng.choice([1e-9, 0.001, 1e6])
        report = reconcile_positions(_positions(local), broker, tolerance)
        assert _records(report) == _ref_positions(local, broker, tolerance)

        local_ids = tuple(f"c{n}" for n in rng.sample(range(12), rng.randint(0, 6)))
        broker_ids = tuple(f"c{n}" for n in rng.sample(range(12), rng.randint(0, 6)))
        orders = reconcile_orders(local_ids, broker_ids)
        assert _records(orders) == _ref_orders(local_ids, broker_ids)

        equity = rng.choice([0.0, round(rng.uniform(1_000, 500_000), 2), 1e21])
        payload = rng.choice([{}, {"equity": None}, {"equity": equity}, {"equity": equity + 0.5}])
        funds = reconcile_funds(equity, payload, 1e-9)
        assert _records(funds) == _ref_funds(equity, payload, 1e-9)

        total = len(report.mismatches) + len(orders.mismatches) + len(funds.mismatches)
        evaluated = rng.random() < 0.5
        verdict = verdict_of((report, orders, funds), evaluated=evaluated)
        assert verdict.status.value == _ref_status(evaluated, total)
        assert verdict.blocks_live == (_ref_status(evaluated, total) != "SAFE")
