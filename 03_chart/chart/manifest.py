"""Chart component manifest — the real chart component, described factually.

The manifest describes what already exists: chart model building, rendering,
and windows. It never changes runtime behavior; the ComponentRegistry and
SystemModel read it at startup for discovery and AI-readable architecture
context.
"""

from core.contracts.capability import (
    BehavioralRules,
    CapabilityContract,
    CapabilityDecl,
    CapabilityId,
)
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.manifest import ComponentManifest


def chart_manifest() -> ComponentManifest:
    """Return the manifest of the real chart component."""
    return ComponentManifest(
        identity=ComponentId("chart"),
        version=ComponentVersion.parse("1.0.0"),
        type="presentation",
        capabilities=(
            CapabilityDecl(
                id=CapabilityId("chart.render"),
                description="turn bars into a chart model",
                inputs=("bars: tuple[Bar, ...]",),
                outputs=("model: ChartModel",),
                contract=CapabilityContract(
                    capability=CapabilityId("chart.render"),
                    inputs=("bars: tuple[Bar, ...]",),
                    outputs=("model: ChartModel",),
                    rules=BehavioralRules(guarantees=("model bars ascending",)),
                ),
            ),
        ),
        capabilities_consumed=(CapabilityId("data.query.candles"),),
        inputs=("bars: tuple[Bar, ...]",),
        outputs=("model: ChartModel",),
        dependencies=(ComponentId("core"), ComponentId("market")),
        events_consumed=("DataLoaded",),
        events_produced=("ChartReady", "WindowRendered"),
        resource_requirements=("qt", "display"),
        side_effects=("paints-window", "publishes-events"),
    )
