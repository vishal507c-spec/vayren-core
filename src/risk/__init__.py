"""Risk domain — fail-closed pre-order gates and kill switches.

Depends on: core.

The 18-gate checklist, its verdicts, the reason strings and the latch table
are RUST-owned (`vayren-core` `risk_engine` + `kill_switch`), reached through
the `risk.native_engine` / `risk.native_kill_switch` bridges. What lives here
is the public boundary only: frozen value objects (`models`), the kill-switch
facade that owns the JSON file (`kill_switch`) and the engine wrapper that
turns any bridge fault into a DENIAL (`engine`) — because an uncertain
verdict must never place an order. No risk policy is reimplemented in Python.
"""

from risk.engine import RiskEngine
from risk.kill_switch import KillSwitch, KillSwitchState
from risk.models import RiskCheck, RiskDecision, RiskPolicy, RiskRequest
from risk.native_kill_switch import levels
from risk.native_session import clock_sane, within_session
from risk.sizing import (
    BROKER_CAPITAL_UNAVAILABLE,
    CAPITAL_STALE_AFTER_SECONDS,
    LEVERAGE_MULTIPLIER,
    PER_TRADE_RISK_PCT,
    PLANNED_RISK_EXCEEDED,
    READY,
    RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE,
    STALE_CAPITAL,
    ZERO_RISK_PER_SHARE,
    BrokerCapital,
    SizingVerdict,
    size_position,
    validate_planned_quantity,
)

__all__ = [
    "RiskPolicy",
    "RiskRequest",
    "RiskCheck",
    "RiskDecision",
    "RiskEngine",
    "KillSwitch",
    "KillSwitchState",
    "levels",
    "within_session",
    "clock_sane",
    "BrokerCapital",
    "SizingVerdict",
    "size_position",
    "validate_planned_quantity",
    "LEVERAGE_MULTIPLIER",
    "PER_TRADE_RISK_PCT",
    "CAPITAL_STALE_AFTER_SECONDS",
    "READY",
    "BROKER_CAPITAL_UNAVAILABLE",
    "STALE_CAPITAL",
    "ZERO_RISK_PER_SHARE",
    "RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE",
    "PLANNED_RISK_EXCEEDED",
]
