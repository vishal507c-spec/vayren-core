"""Versioned golden test cases for every migrated behavior.

Python is the reference implementation until parity is independently
verified; the golden expectations are frozen here and versioned. A Rust
implementation may never redefine the expected result — mismatches are
reported, not absorbed.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

from .config import GOLDEN_DIR, GOLDEN_SEED, ROOT

GOLDEN_VERSION = 1


@dataclass(frozen=True)
class GoldenSet:
    unit_id: str
    category: str
    cases: tuple


def _rng(stream: str) -> random.Random:
    seed = hash((GOLDEN_SEED, stream)) % (2**31)
    return random.Random(seed)


def order_lifecycle_cases() -> GoldenSet:
    happy = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (3, 7), (7, 8), (3, 9), (9, 10)]
    illegal = [(0, 5), (5, 0), (8, 5), (-5, 3), (3, 99), (12, 0), (5, 12)]
    boundary = [(i, 12) for i in range(8)] + [(12, i) for i in range(13)]
    cases = tuple(
        [
            {"from": a, "to": b, "kind": k}
            for k, group in (
                ("normal", happy),
                ("invalid", illegal),
                ("boundary", boundary),
            )
            for a, b in group
        ]
    )
    return GoldenSet("execution.order_lifecycle", "lifecycle", cases)


def drawdown_cases() -> GoldenSet:
    rng = _rng("drawdown")
    cases: list[dict] = [
        {"equities": [], "kind": "empty"},
        {"equities": [100.0, 110.0, 120.0], "kind": "normal"},
        {"equities": [100.0, 80.0, 90.0], "kind": "boundary"},
        {"equities": [0.0, 0.0], "kind": "boundary"},
        {"equities": [50.0], "kind": "normal"},
        {"equities": [-10.0, -30.0, -5.0], "kind": "unusual"},
    ]
    for _ in range(8):
        n = rng.randint(2, 30)
        cases.append(
            {
                "equities": [rng.uniform(-50.0, 150.0) for _ in range(n)],
                "kind": "large" if n > 15 else "normal",
            }
        )
    return GoldenSet("backtest.metrics.drawdown", "numeric", tuple(cases))


def equity_cases() -> GoldenSet:
    rng = _rng("equity")
    cases: list[dict] = [
        {"initial": 100.0, "pnls": [], "kind": "empty"},
        {"initial": 100.0, "pnls": [10.0, -30.0, 5.0], "kind": "normal"},
        {"initial": 1000.0, "pnls": [0.0, 0.0], "kind": "boundary"},
        {"initial": 0.0, "pnls": [5.0, -5.0], "kind": "boundary"},
    ]
    for _ in range(8):
        cases.append(
            {
                "initial": rng.choice([100.0, 1000.0, 1000000.0]),
                "pnls": [rng.uniform(-500.0, 500.0) for _ in range(rng.randint(1, 20))],
                "kind": "normal",
            }
        )
    large = {
        "initial": 10000.0,
        "pnls": [rng.uniform(-300, 300) for _ in range(200)],
        "kind": "large",
    }
    cases.append(large)
    return GoldenSet("backtest.metrics.equity_curve", "numeric", tuple(cases))


def sharpe_cases() -> GoldenSet:
    rng = _rng("sharpe")
    cases: list[dict] = [
        {"pnls": [5.0], "bars": [1.0], "initial": 100.0, "kind": "boundary"},
        {"pnls": [], "bars": [], "initial": 100.0, "kind": "empty"},
        {"pnls": [0.0, 0.0], "bars": [1.0, 1.0], "initial": 100.0, "kind": "invalid"},
        {
            "pnls": [-200.0, 50.0, 50.0],
            "bars": [1.0, 1.0, 1.0],
            "initial": 100.0,
            "kind": "failure",
        },
        {"pnls": [10.0, -5.0, 8.0], "bars": [2.0, 3.0, 1.0], "initial": 100.0, "kind": "normal"},
    ]
    for _ in range(8):
        n = rng.randint(2, 15)
        cases.append(
            {
                "pnls": [rng.uniform(-300.0, 300.0) for _ in range(n)],
                "bars": [float(rng.randint(1, 50)) for _ in range(n)],
                "initial": 10000.0,
                "kind": "normal",
            }
        )
    return GoldenSet("backtest.metrics.sharpe", "numeric", tuple(cases))


def aggregate_cases() -> GoldenSet:
    rng = _rng("aggregate")
    cases: list[dict] = [
        {"rows": 0, "step": 900, "tf": 900, "session": 33300, "kind": "empty"},
        {"rows": 10, "step": 900, "tf": 900, "session": 33300, "kind": "normal"},
        {"rows": 10, "step": 900, "tf": 1800, "session": 33300, "kind": "normal"},
        {"rows": 200, "step": 900, "tf": 86400, "session": 33300, "kind": "boundary"},
        {"rows": 200, "step": 900, "tf": 604800, "session": 33300, "kind": "boundary"},
        {"rows": 5, "step": 60, "tf": 300, "session": 0, "kind": "unusual"},
    ]
    for _ in range(6):
        cases.append(
            {
                "rows": rng.randint(1, 120),
                "step": rng.choice([60, 300, 900]),
                "tf": rng.choice([60, 300, 900, 1800, 3600, 7200, 86400, 604800]),
                "session": rng.choice([0, 33300]),
                "kind": "normal",
            }
        )
    return GoldenSet("market.timeframe.aggregate", "aggregation", tuple(cases))


def mode_cases() -> GoldenSet:
    rng = _rng("mode")
    cases: list[dict] = [
        {"values": [], "kind": "empty"},
        {"values": [42], "kind": "normal"},
        {"values": [5, 5, 3, 5, 3], "kind": "normal"},
        {"values": [7, 9, 7, 9], "kind": "boundary"},
    ]
    for _ in range(8):
        cases.append(
            {
                "values": [rng.randint(0, 12) for _ in range(rng.randint(0, 30))],
                "kind": "normal",
            }
        )
    return GoldenSet("market.timeframe.mode", "numeric", tuple(cases))


ALL_BUILDERS = (
    order_lifecycle_cases,
    drawdown_cases,
    equity_cases,
    sharpe_cases,
    aggregate_cases,
    mode_cases,
)


def all_golden_sets() -> list[GoldenSet]:
    return [builder() for builder in ALL_BUILDERS]


def persist_golden() -> list[str]:
    """Write versioned golden files; returns written paths (relative)."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for golden in all_golden_sets():
        payload = {
            "unit": golden.unit_id,
            "category": golden.category,
            "version": GOLDEN_VERSION,
            "cases": list(golden.cases),
        }
        path = GOLDEN_DIR / f"{golden.unit_id.replace('.', '_')}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path.relative_to(ROOT).as_posix())
    return written


__all__ = [
    "GOLDEN_VERSION",
    "GoldenSet",
    "all_golden_sets",
    "persist_golden",
    "order_lifecycle_cases",
    "drawdown_cases",
    "equity_cases",
    "sharpe_cases",
    "aggregate_cases",
    "mode_cases",
]
