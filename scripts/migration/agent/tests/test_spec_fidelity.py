"""Spec fidelity: extracted contract matches the live engine exactly."""

from __future__ import annotations

from risk import RiskEngine, RiskPolicy, RiskRequest

from scripts.migration.agent import generator_rust as rust_gen
from scripts.migration.agent.analyzer import extract_risk_spec, pristine_source
from scripts.migration.agent.spec import eval_kernel, pack_env

PRISTINE = pristine_source("07_risk/risk/engine.py")


def _request(**overrides: object) -> RiskRequest:
    base: dict = {
        "intent_id": "spec-probe",
        "strategy_id": "s",
        "symbol": "TEST",
        "side": "BUY",
        "quantity": 10.0,
        "price": 100.0,
        "timestamp": "2026-01-05 09:15:00",
        "now_epoch": 1767580500.0,
    }
    base.update(overrides)
    return RiskRequest(**base)


def test_spec_shape() -> None:
    spec = extract_risk_spec(PRISTINE).to_dict()
    assert len(spec["checks"]) == 13
    assert len(spec["check_order"]) == 18
    assert len(spec["orchestration"]) == 5
    bits = [c["bit"] for c in spec["checks"]]
    assert bits == list(range(13))
    assert spec["checks"][0]["name"] == "broker_health"
    assert spec["checks"][1]["name"] == "fresh_data"


def test_spec_oracle_matches_live_engine() -> None:
    """Extraction fidelity: spec kernel == live engine on scalar checks."""
    spec = extract_risk_spec(PRISTINE).to_dict()
    kernel_names = [c["name"] for c in spec["checks"]]
    policies = [
        RiskPolicy(),
        RiskPolicy(
            max_notional=50000.0,
            max_exposure_pct=25.0,
            daily_loss_limit=1000.0,
            strategy_loss_limit=500.0,
            spread_limit_pct=0.2,
            require_fresh_data_seconds=60.0,
            cooldown_seconds=30.0,
            max_orders_per_day=5,
        ),
        RiskPolicy(max_position_qty=100.0, max_order_qty=50.0, allowed_symbols=("TEST",)),
    ]
    requests = [
        _request(),
        _request(side="SELL", quantity=500.0, position_qty=-400.0, equity=200000.0),
        _request(quantity=0.0, price=-1.0, available_capital=0.0),
        _request(data_age_seconds=30.0, spread_pct=0.1, orders_today=5),
        _request(last_order_epoch=1767580400.0, day_pnl=-1500.0, strategy_day_pnl=-600.0),
    ]
    for policy in policies:
        engine = RiskEngine(policy)
        for request in requests:
            decision = engine.evaluate(request)
            by_name = {c.name: c.passed for c in decision.checks}
            mask = eval_kernel(spec, pack_env(spec, policy, request))
            for bit, name in enumerate(kernel_names):
                assert by_name[name] == bool(mask & (1 << bit)), (name, policy, request)


def test_rust_generation_is_deterministic() -> None:
    spec = extract_risk_spec(PRISTINE).to_dict()
    first = rust_gen.render_kernel(spec)
    second = rust_gen.render_kernel(spec)
    assert first == second
    assert "evaluate_risk_kernel" in first
    assert "BIT_ORDER_QTY" in first
    assert "panic" not in first
