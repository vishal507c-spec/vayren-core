"""Risk domain — fail-closed pre-order gates and kill switches.

Depends on: core.
"""

from risk.engine import RiskEngine
from risk.kill_switch import KillSwitch, KillSwitchState
from risk.manifest import risk_manifest
from risk.models import RiskCheck, RiskDecision, RiskPolicy, RiskRequest
from risk.session import SessionRules, clock_sane, within_session

__all__ = [
    "RiskPolicy",
    "RiskRequest",
    "RiskCheck",
    "RiskDecision",
    "RiskEngine",
    "KillSwitch",
    "KillSwitchState",
    "SessionRules",
    "within_session",
    "clock_sane",
    "risk_manifest",
]
