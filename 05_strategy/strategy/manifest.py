"""Strategy component manifest — the real strategy platform, described factually."""

from core.contracts.capability import (
    BehavioralRules,
    CapabilityContract,
    CapabilityDecl,
    CapabilityId,
)
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.contract import ComponentContract
from core.contracts.manifest import ComponentManifest


def strategy_manifest() -> ComponentManifest:
    """Return the manifest of the real strategy component."""
    return ComponentManifest(
        identity=ComponentId("strategy"),
        version=ComponentVersion.parse("1.0.0"),
        type="domain",
        capabilities=(
            CapabilityDecl(
                id=CapabilityId("strategy.registry"),
                description="register and resolve strategy kinds and definitions",
                inputs=("definition: StrategyDefinition",),
                outputs=("definitions: tuple[StrategyDefinition, ...]",),
                contract=CapabilityContract(
                    capability=CapabilityId("strategy.registry"),
                    inputs=("definition: StrategyDefinition",),
                    outputs=("definitions: tuple[StrategyDefinition, ...]",),
                    rules=BehavioralRules(
                        guarantees=(
                            "explicit registration only",
                            "parameters validated against kind specs",
                        ),
                        failure_modes=("unknown kind rejected", "duplicate id rejected"),
                    ),
                ),
            ),
            CapabilityDecl(
                id=CapabilityId("strategy.runtime"),
                description="execute strategy logic bar by bar into signals",
                inputs=("bars: tuple[Bar, ...]", "params: StrategyParameters"),
                outputs=("signals: tuple[Signal, ...]",),
            ),
        ),
        inputs=("bars: tuple[Bar, ...]",),
        outputs=("signals: tuple[Signal, ...]",),
        dependencies=(ComponentId("core"), ComponentId("market")),
        events_consumed=(),
        events_produced=("StrategiesListed", "StrategySelected", "PaperTradeRequested", "LabReset"),
        resource_requirements=("memory",),
        side_effects=("publishes-events",),
        contract=ComponentContract(
            component=ComponentId("strategy"),
            version=ComponentVersion.parse("1.0.0"),
            invariants=("no fabricated signals", "logic state isolated per run"),
        ),
    )
