"""Risk component manifest."""

from core import (
    CapabilityDecl,
    CapabilityId,
    ComponentContract,
    ComponentId,
    ComponentManifest,
    ComponentVersion,
)


def risk_manifest() -> ComponentManifest:
    """Return the manifest of the risk component."""
    return ComponentManifest(
        identity=ComponentId("risk"),
        version=ComponentVersion.parse("1.0.0"),
        type="engine",
        capabilities=(
            CapabilityDecl(
                id=CapabilityId("risk.evaluate"),
                description="fail-closed pre-order gate: policy checks over a risk request",
                inputs=("request: RiskRequest",),
                outputs=("decision: RiskDecision",),
            ),
            CapabilityDecl(
                id=CapabilityId("risk.kill_switch"),
                description="latched emergency stop persisted across restarts",
                inputs=("level, reason",),
                outputs=("halted: bool",),
            ),
        ),
        inputs=("request: RiskRequest",),
        outputs=("decision: RiskDecision",),
        dependencies=(ComponentId("core"),),
        events_consumed=(),
        events_produced=(),
        resource_requirements=("memory",),
        side_effects=("writes-kill-switch-file",),
        contract=ComponentContract(
            component=ComponentId("risk"),
            version=ComponentVersion.parse("1.0.0"),
            invariants=("fail-closed: uncertain means denied", "denials always carry reasons"),
        ),
    )
