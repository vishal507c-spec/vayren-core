"""RiskEngine tests — every gate denies independently, approvals need all green."""

import time

from risk.engine import RiskEngine
from risk.kill_switch import KillSwitch
from risk.models import RiskPolicy, RiskRequest


def _request(**overrides) -> RiskRequest:
    now = time.time()
    values = {
        "intent_id": "intent-1",
        "strategy_id": "sma-crossover",
        "symbol": "RELIANCE",
        "side": "BUY",
        "quantity": 10.0,
        "price": 100.0,
        "timestamp": "2026-01-06T09:30:00+00:00",
        "position_qty": 0.0,
        "day_pnl": 0.0,
        "strategy_day_pnl": 0.0,
        "equity": 1_000_000.0,
        "available_capital": 500_000.0,
        "spread_pct": 0.02,
        "data_age_seconds": 5.0,
        "broker_healthy": True,
        "orders_today": 0,
        "last_order_epoch": None,
        "now_epoch": now,
    }
    values.update(overrides)
    return RiskRequest(**values)  # type: ignore[arg-type]


def _engine(**policy_overrides) -> RiskEngine:
    return RiskEngine(RiskPolicy(**policy_overrides))


def test_clean_request_approved_with_all_checks_recorded() -> None:
    decision = _engine().evaluate(_request())
    assert decision.approved
    assert decision.reasons == ()
    assert len(decision.checks) >= 15
    assert all(check.passed for check in decision.checks)


def test_kill_switch_denies_everything() -> None:
    kill = KillSwitch()
    kill.engage("operator halt")
    decision = RiskEngine(RiskPolicy(), kill).evaluate(_request())
    assert not decision.approved
    assert any("kill" in reason for reason in decision.reasons)


def test_broker_unhealthy_denies() -> None:
    decision = _engine().evaluate(_request(broker_healthy=False))
    assert not decision.approved


def test_stale_data_denies() -> None:
    engine = _engine(require_fresh_data_seconds=60.0)
    assert not engine.evaluate(_request(data_age_seconds=3600.0)).approved
    assert not engine.evaluate(_request(data_age_seconds=None)).approved
    assert engine.evaluate(_request(data_age_seconds=5.0)).approved


def test_spread_limit_denies() -> None:
    engine = _engine(spread_limit_pct=0.05)
    assert not engine.evaluate(_request(spread_pct=0.5)).approved
    assert engine.evaluate(_request(spread_pct=0.02)).approved


def test_duplicate_intent_denies_second_time() -> None:
    engine = _engine()
    assert engine.evaluate(_request(intent_id="dup-1")).approved
    second = engine.evaluate(_request(intent_id="dup-1"))
    assert not second.approved
    assert any("dup-1" in reason for reason in second.reasons)


def test_position_and_notional_limits() -> None:
    engine = _engine(max_position_qty=100.0, max_order_qty=50.0, max_notional=10_000.0)
    assert not engine.evaluate(_request(quantity=60.0)).approved
    assert not engine.evaluate(_request(quantity=50.0, price=1000.0)).approved
    assert engine.evaluate(_request(quantity=10.0, price=100.0)).approved
    # existing long 95 + buy 10 breaches position cap
    assert not engine.evaluate(_request(quantity=10.0, position_qty=95.0)).approved


def test_loss_limits_breach() -> None:
    engine = _engine(daily_loss_limit=1000.0, strategy_loss_limit=500.0)
    assert not engine.evaluate(_request(day_pnl=-1500.0)).approved
    assert not engine.evaluate(_request(strategy_day_pnl=-600.0)).approved
    assert engine.evaluate(_request(day_pnl=-100.0, strategy_day_pnl=-100.0)).approved


def test_session_window_and_clock() -> None:
    engine = _engine(session_start="09:15", session_end="15:30")
    assert engine.evaluate(_request(timestamp="2026-01-06T10:00:00+00:00")).approved
    assert not engine.evaluate(_request(timestamp="2026-01-06T18:00:00+00:00")).approved
    # far-future timestamp is a clock anomaly
    assert not engine.evaluate(_request(timestamp="2999-01-01T00:00:00+00:00")).approved
    assert not engine.evaluate(_request(timestamp="not-a-time")).approved


def test_cooldown_and_rate_and_capital() -> None:
    now = time.time()
    engine = _engine(cooldown_seconds=60.0, max_orders_per_day=2)
    assert not engine.evaluate(_request(last_order_epoch=now - 10.0, now_epoch=now)).approved
    assert engine.evaluate(_request(last_order_epoch=now - 120.0, now_epoch=now)).approved
    assert not engine.evaluate(_request(orders_today=2)).approved
    assert not engine.evaluate(_request(available_capital=0.0)).approved


def test_engine_error_fails_closed() -> None:
    engine = _engine()
    broken = RiskRequest(
        intent_id="x",
        strategy_id="s",
        symbol="RELIANCE",
        side="BUY",
        quantity=float("nan"),
        price=100.0,
        timestamp="2026-01-06T10:00:00+00:00",
    )
    decision = engine.evaluate(broken)
    assert not decision.approved


def test_instrument_allowlist() -> None:
    engine = _engine(allowed_symbols=("TCS",))
    assert not engine.evaluate(_request(symbol="RELIANCE")).approved
    assert engine.evaluate(_request(symbol="TCS")).approved
