"""Parity engine: real differential execution, never silent PASS.

When the native library is unavailable every outcome is INCONCLUSIVE (the
gate treats that as unverified, not passing). When present, the migrated
kernels must match their frozen Python oracles.
"""

from __future__ import annotations

from scripts.migration import golden, parity


def test_golden_sets_cover_required_categories() -> None:
    sets = {g.unit_id: g for g in golden.all_golden_sets()}
    assert "execution.order_lifecycle" in sets
    assert "backtest.metrics.drawdown" in sets
    assert "market.timeframe.aggregate" in sets
    for unit_id, golden_set in sets.items():
        kinds = {case["kind"] for case in golden_set.cases}
        assert {"normal", "boundary"} <= kinds, unit_id
        assert len(golden_set.cases) >= 6, unit_id


def test_order_lifecycle_parity() -> None:
    outcome = parity.run_parity("execution.order_lifecycle")
    assert outcome.verdict in ("PASS", "FAIL", "INCONCLUSIVE")
    if outcome.verdict == "INCONCLUSIVE":
        assert outcome.detail
    else:
        assert outcome.cases > 150
        assert outcome.verdict == "PASS", outcome.mismatches[:3]


def test_metrics_parity() -> None:
    for unit in (
        "backtest.metrics.drawdown",
        "backtest.metrics.equity_curve",
        "backtest.metrics.sharpe",
        "market.timeframe.mode",
    ):
        outcome = parity.run_parity(unit)
        assert outcome.verdict in ("PASS", "FAIL", "INCONCLUSIVE"), unit
        if outcome.verdict != "INCONCLUSIVE":
            assert outcome.verdict == "PASS", (unit, outcome.mismatches[:3])


def test_aggregate_parity() -> None:
    outcome = parity.run_parity("market.timeframe.aggregate")
    assert outcome.verdict in ("PASS", "FAIL", "INCONCLUSIVE")
    if outcome.verdict != "INCONCLUSIVE":
        assert outcome.verdict == "PASS", outcome.mismatches[:3]


def test_unknown_unit_is_inconclusive_not_pass() -> None:
    outcome = parity.run_parity("no.such.unit")
    assert outcome.verdict == "INCONCLUSIVE"
