"""Parity pin for the Rust risk-policy sanity kernel.

``_ref_reasons`` is the retired Python rule table, frozen here as the oracle:
the kernel must name exactly the same reasons in the same order for every
policy shape. Comparisons live in Rust; this file holds both sides of the
answer so a drift in either shows up as a failed assertion.
"""

from __future__ import annotations

import itertools
import math

from risk import RiskPolicy

from execution.broker.gates import risk_configuration_valid

#: The kernel's denial vocabulary (``live_readiness::RISK_CONFIG_MESSAGES``),
#: pinned here so a rename or reorder on either side fails this test.
ALL_REASONS: tuple[str, ...] = (
    "max_position_qty must be positive",
    "max_order_qty must be positive",
    "max_order_qty exceeds max_position_qty",
    "max_notional must be positive when set",
    "max_exposure_pct must be positive when set",
    "daily_loss_limit must be positive when set",
    "strategy_loss_limit must be positive when set",
    "cooldown_seconds must not be negative",
    "max_orders_per_day must be positive when set",
    "require_fresh_data_seconds must be positive when set",
)


def _ref_reasons(policy: RiskPolicy) -> tuple[str, ...]:
    """The pre-migration Python authority, byte for byte."""
    reasons: list[str] = []
    if policy.max_position_qty <= 0:
        reasons.append("max_position_qty must be positive")
    if policy.max_order_qty <= 0:
        reasons.append("max_order_qty must be positive")
    if policy.max_order_qty > policy.max_position_qty:
        reasons.append("max_order_qty exceeds max_position_qty")
    for name in ("max_notional", "max_exposure_pct", "daily_loss_limit", "strategy_loss_limit"):
        value = getattr(policy, name, None)
        if value is not None and value <= 0:
            reasons.append(f"{name} must be positive when set")
    if policy.cooldown_seconds < 0:
        reasons.append("cooldown_seconds must not be negative")
    if policy.max_orders_per_day is not None and policy.max_orders_per_day <= 0:
        reasons.append("max_orders_per_day must be positive when set")
    if policy.require_fresh_data_seconds is not None and policy.require_fresh_data_seconds <= 0:
        reasons.append("require_fresh_data_seconds must be positive when set")
    return tuple(reasons)


def _assert_policy(policy: RiskPolicy) -> None:
    expected = _ref_reasons(policy)
    assert risk_configuration_valid(policy) == (not expected, expected)


def test_optional_limits_never_authorise_a_reason_when_absent() -> None:
    for combo in itertools.product((None, 0.0, -5.0, 1.0), repeat=5):
        _assert_policy(
            RiskPolicy(
                max_notional=combo[0],
                max_exposure_pct=combo[1],
                daily_loss_limit=combo[2],
                strategy_loss_limit=combo[3],
                require_fresh_data_seconds=combo[4],
            )
        )


def test_quantity_cooldown_and_daily_order_rules() -> None:
    for position, order, cooldown, per_day in itertools.product(
        (0.0, -5.0, 10.0, 1000.0),
        (0.0, -1.0, 50.0, 9999.0),
        (0.0, -1.0, 30.0),
        (None, 0, -2, 5),
    ):
        _assert_policy(
            RiskPolicy(
                max_position_qty=position,
                max_order_qty=order,
                cooldown_seconds=cooldown,
                max_orders_per_day=per_day,
            )
        )


def test_default_policy_and_nan_stay_valid_like_python() -> None:
    _assert_policy(RiskPolicy())
    # Python's `nan <= 0` and `nan > x` are both False, so NaN fails no check.
    _assert_policy(
        RiskPolicy(
            max_position_qty=math.nan,
            max_order_qty=math.nan,
            max_notional=math.nan,
            max_exposure_pct=math.nan,
            daily_loss_limit=math.nan,
            strategy_loss_limit=math.nan,
            cooldown_seconds=math.nan,
            max_orders_per_day=None,
            require_fresh_data_seconds=math.nan,
        )
    )


def test_every_reason_appears_in_kernel_order() -> None:
    ok, reasons = risk_configuration_valid(
        RiskPolicy(
            max_position_qty=-10.0,
            max_order_qty=-5.0,
            max_notional=-1.0,
            max_exposure_pct=-1.0,
            daily_loss_limit=-1.0,
            strategy_loss_limit=-1.0,
            cooldown_seconds=-1.0,
            max_orders_per_day=0,
            require_fresh_data_seconds=-1.0,
        )
    )
    assert not ok
    assert reasons == ALL_REASONS


def test_one_failing_comparison_arrives_alone() -> None:
    assert risk_configuration_valid(RiskPolicy(max_position_qty=1000.0, max_order_qty=9999.0)) == (
        False,
        ("max_order_qty exceeds max_position_qty",),
    )
