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
]
