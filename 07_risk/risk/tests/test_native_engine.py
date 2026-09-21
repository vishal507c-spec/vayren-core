"""Risk checklist ownership: the Rust engine decides, Python only unwraps.

Pinned here because the migration moved the whole rule table — gate order,
names, reason strings and value interpolation — out of `risk/engine.py`. If the
kernel ever reorders a gate or rewords a reason, these assertions fail instead
of quietly changing what an audited denial looks like.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from risk.engine import RiskEngine
from risk.kill_switch import KillSwitch
from risk.models import RiskPolicy, RiskRequest

NOW = 1_767_700_000.0

#: The kernel's checklist, in the order the retired Python `_evaluate` pushed it.
CHECK_ORDER: tuple[str, ...] = (
    "kill_switch",
    "broker_health",
    "session",
    "clock",
    "instrument",
    "duplicate",
    "fresh_data",
    "spread",
    "cooldown",
    "order_rate",
    "sanity",
    "order_qty",
    "notional",
    "position",
    "exposure",
    "daily_loss",
    "strategy_loss",
    "capital",
)

FULL_POLICY = RiskPolicy(
    max_position_qty=1000.0,
    max_order_qty=500.0,
    max_notional=100_000.0,
    max_exposure_pct=50.0,
    daily_loss_limit=10_000.0,
    strategy_loss_limit=5_000.0,
    allowed_symbols=("RELIANCE",),
    spread_limit_pct=0.05,
    require_fresh_data_seconds=60.0,
    session_start="09:15",
    session_end="15:30",
    cooldown_seconds=60.0,
    max_orders_per_day=50,
)


def _request(**overrides: Any) -> RiskRequest:
    values: dict[str, Any] = {
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
        "last_order_epoch": NOW - 3600.0,
        "now_epoch": NOW,
    }
    values.update(overrides)
    return RiskRequest(**values)


def _decision(
    request: RiskRequest, policy: RiskPolicy = FULL_POLICY, kill: KillSwitch | None = None
) -> Any:
    return RiskEngine(policy, kill).evaluate(request)


def test_a_clean_request_clears_every_gate_in_order() -> None:
    decision = _decision(_request())
    assert decision.approved
    assert decision.reasons == ()
    assert tuple(check.name for check in decision.checks) == CHECK_ORDER
    assert all(check.passed for check in decision.checks)
    assert all(check.detail == "" for check in decision.checks)


@pytest.mark.parametrize(
    ("policy_overrides", "request_overrides", "gate", "reason"),
    [
        ({}, {"broker_healthy": False}, "broker_health", "broker unhealthy"),
        (
            {},
            {"timestamp": "2026-01-06T18:00:00+00:00", "now_epoch": 4102444800.0},
            "session",
            "outside trading session",
        ),
        (
            {},
            {"timestamp": "2026-01-06T08:00:00+00:00"},
            "session",
            "outside trading session",
        ),
        (
            {"session_start": None, "session_end": None},
            {"timestamp": "not-a-time"},
            "clock",
            "clock anomaly",
        ),
        (
            {"session_start": None, "session_end": None},
            {"timestamp": "2999-01-01T10:00:00+00:00"},
            "clock",
            "clock anomaly",
        ),
        ({}, {"symbol": "TCS"}, "instrument", "symbol not allowed: TCS"),
        ({}, {"data_age_seconds": 3600.0}, "fresh_data", "stale market data: age=3600.0"),
        ({}, {"data_age_seconds": None}, "fresh_data", "stale market data: age=None"),
        ({}, {"spread_pct": 0.5}, "spread", "spread too wide: 0.5"),
        ({}, {"spread_pct": None}, "spread", "spread too wide: None"),
        ({}, {"last_order_epoch": NOW - 10.0}, "cooldown", "in cooldown"),
        ({}, {"orders_today": 50}, "order_rate", "max orders per day reached"),
        ({}, {"quantity": 0.0}, "sanity", "non-positive quantity or price"),
        ({}, {"price": -1.0}, "sanity", "non-positive quantity or price"),
        ({}, {"quantity": 900.0}, "order_qty", "order quantity exceeds max"),
        (
            {"max_order_qty": 2000.0, "max_position_qty": 2000.0},
            {"quantity": 1500.0},
            "notional",
            "notional exceeds max",
        ),
        ({}, {"position_qty": 995.0}, "position", "position limit exceeded"),
        ({"max_position_qty": 5000.0}, {"equity": 1000.0}, "exposure", "exposure limit exceeded"),
        ({}, {"day_pnl": -20_000.0}, "daily_loss", "daily loss limit breached"),
        ({}, {"strategy_day_pnl": -6000.0}, "strategy_loss", "strategy loss limit breached"),
        ({}, {"available_capital": 0.0}, "capital", "no available capital"),
    ],
)
def test_each_gate_denies_with_its_own_reason(
    policy_overrides: dict[str, Any],
    request_overrides: dict[str, Any],
    gate: str,
    reason: str,
) -> None:
    policy = dataclasses.replace(FULL_POLICY, **policy_overrides)
    decision = _decision(_request(**request_overrides), policy)
    assert not decision.approved
    failed = {check.name: check.detail for check in decision.checks if not check.passed}
    assert failed == {gate: reason}
    assert decision.reasons == (reason,)


def test_absent_limits_record_the_historical_disabled_details() -> None:
    policy = RiskPolicy(allowed_symbols=(), require_fresh_data_seconds=None)
    decision = _decision(_request(), policy)
    details = {check.name: (check.passed, check.detail) for check in decision.checks}
    assert details["instrument"] == (True, "universe unrestricted")
    assert details["fresh_data"] == (True, "staleness gate disabled")
    assert details["spread"] == (True, "spread gate disabled")
    assert details["cooldown"] == (True, "")
    assert details["order_rate"] == (True, "")
    assert decision.approved


def test_duplicate_memory_lives_in_the_kernel() -> None:
    engine = RiskEngine(FULL_POLICY)
    assert engine.evaluate(_request(intent_id="dup-1")).approved
    second = engine.evaluate(_request(intent_id="dup-1"))
    assert not second.approved
    assert {c.name: c.detail for c in second.checks}["duplicate"] == (
        "intent already decided: dup-1"
    )
    # A denial is never remembered, so the same intent can still be approved
    # later once the blocking condition clears.
    denied = engine.evaluate(_request(intent_id="dup-2", quantity=-1.0))
    assert not denied.approved
    assert engine.evaluate(_request(intent_id="dup-2")).approved


def test_kill_switch_denial_travels_through_the_kernel() -> None:
    kill = KillSwitch()
    kill.engage("operator halt")
    decision = _decision(_request(), FULL_POLICY, kill)
    assert not decision.approved
    assert decision.reasons == ("kill switch engaged",)


def test_a_bridge_fault_fails_closed_as_the_engine_check() -> None:
    decision = _decision(_request(symbol=123))  # type: ignore[arg-type]
    assert not decision.approved
    assert [check.name for check in decision.checks] == ["engine"]
    assert decision.reasons[0].startswith("risk engine error — fail closed: ")


def test_a_released_handle_denies_instead_of_raising() -> None:
    engine = RiskEngine(FULL_POLICY)
    engine._kernel.close()  # noqa: SLF001
    engine._kernel.close()  # noqa: SLF001  idempotent, like the kill switch
    decision = engine.evaluate(_request())
    assert not decision.approved
    assert decision.reasons[0].startswith("risk engine error — fail closed: ")
    assert _decision(_request()).approved
