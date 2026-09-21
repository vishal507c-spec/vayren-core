"""RiskEngine — mandatory fail-closed gate before ANY order.

The checklist itself is Rust (`rust/vayren-core/src/risk_engine.rs`): 18 named
gates in fixed order, their verdicts, every reason string and the
duplicate-intent memory, reached through `risk.native_engine`. What stays here
is the Python-shaped boundary: value objects plus the wrapper that turns any
bridge fault into a denial, because an uncertain verdict must never order.
"""

from __future__ import annotations

from risk.kill_switch import KillSwitch
from risk.models import RiskCheck, RiskDecision, RiskPolicy, RiskRequest
from risk.native_engine import NativeRiskEngine


class RiskEngine:
    """Stateless policy evaluation + duplicate-order memory.

    The engine itself holds no market state; the caller supplies a
    RiskRequest snapshot. Only approved intent IDs are remembered (for
    duplicate protection) and day counters the caller reports.
    """

    def __init__(self, policy: RiskPolicy, kill_switch: KillSwitch | None = None) -> None:
        self._policy = policy
        self._kill_switch = kill_switch if kill_switch is not None else KillSwitch()
        self._kernel = NativeRiskEngine(policy)

    @property
    def policy(self) -> RiskPolicy:
        return self._policy

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    def evaluate(self, request: RiskRequest) -> RiskDecision:
        """Approve only when every applicable check passes. Never raises."""
        try:
            return self._kernel.evaluate(request, self._kill_switch.is_halted())
        except Exception as exc:
            return RiskDecision(
                approved=False,
                intent_id=request.intent_id,
                reasons=(f"risk engine error — fail closed: {exc}",),
                checks=(RiskCheck(name="engine", passed=False, detail=str(exc)),),
            )
