"""Execution component manifest."""

from core import (
    CapabilityDecl,
    CapabilityId,
    ComponentContract,
    ComponentId,
    ComponentManifest,
    ComponentVersion,
)


def execution_manifest() -> ComponentManifest:
    """Return the manifest of the live/paper execution component."""
    return ComponentManifest(
        identity=ComponentId("execution"),
        version=ComponentVersion.parse("1.0.0"),
        type="engine",
        capabilities=(
            CapabilityDecl(
                id=CapabilityId("execution.run_live"),
                description="event-driven live/paper strategy sessions with safety gates",
                inputs=("config: SessionConfig", "provider, strategy registrations"),
                outputs=("journal, positions, fills",),
            ),
            CapabilityDecl(
                id=CapabilityId("execution.inspect"),
                description="machine-readable strategy live requirements",
                inputs=("definition, logic",),
                outputs=("contract: StrategyRuntimeContract",),
            ),
            CapabilityDecl(
                id=CapabilityId("execution.replay"),
                description="recorded session replay with divergence reports",
                inputs=("events",),
                outputs=("report: ReplayReport",),
            ),
        ),
        inputs=("config: SessionConfig",),
        outputs=("journal, positions, fills",),
        dependencies=(
            ComponentId("core"),
            ComponentId("market"),
            ComponentId("strategy"),
            ComponentId("risk"),
        ),
        events_consumed=("CandleEvent", "QuoteEvent", "TradeEvent", "HeartbeatEvent"),
        events_produced=(
            "SignalGenerated",
            "RiskApproved",
            "RiskDenied",
            "OrderPlanned",
            "OrderSubmitted",
            "OrderAcknowledged",
            "OrderFill",
            "OrderRejected",
            "PositionUpdated",
            "KillSwitchEngaged",
        ),
        resource_requirements=("memory",),
        side_effects=("paper fills mutate paper capital", "writes journal files"),
        contract=ComponentContract(
            component=ComponentId("execution"),
            version=ComponentVersion.parse("1.0.0"),
            invariants=(
                "strategy never calls broker",
                "no order without risk approval",
                "live requires all five gates",
                "unknown orders reconcile, never blind-resubmit",
            ),
        ),
    )
