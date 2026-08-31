"""Capability graph tests — providers, consumers, and unresolved consumption."""

from core.contracts.capability import CapabilityDecl, CapabilityId
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.manifest import ComponentManifest
from core.system.capability_graph import CapabilityGraph


def manifest(
    name: str,
    capabilities: tuple[CapabilityDecl, ...] = (),
    capabilities_consumed: tuple[CapabilityId, ...] = (),
) -> ComponentManifest:
    return ComponentManifest(
        identity=ComponentId(name),
        version=ComponentVersion.parse("1.0.0"),
        type="service",
        capabilities=capabilities,
        capabilities_consumed=capabilities_consumed,
    )


def sample_graph() -> CapabilityGraph:
    return CapabilityGraph(
        (
            manifest(
                "market",
                capabilities=(CapabilityDecl(id=CapabilityId("data.query")),),
            ),
            manifest(
                "chart",
                capabilities=(CapabilityDecl(id=CapabilityId("chart.render")),),
                capabilities_consumed=(CapabilityId("data.query"),),
            ),
            manifest(
                "indicator",
                capabilities_consumed=(CapabilityId("data.query"),),
            ),
            manifest(
                "orphan",
                capabilities=(CapabilityDecl(id=CapabilityId("data.ingest")),),
            ),
        )
    )


def test_capabilities_are_sorted() -> None:
    graph = sample_graph()
    assert graph.capabilities() == ("chart.render", "data.ingest", "data.query")


def test_providers() -> None:
    graph = sample_graph()
    assert graph.providers("data.query") == ("market",)
    assert graph.providers("missing.cap") == ()


def test_consumers() -> None:
    graph = sample_graph()
    assert graph.consumers("data.query") == ("chart", "indicator")
    assert graph.consumers("chart.render") == ()


def test_capabilities_of() -> None:
    graph = sample_graph()
    assert graph.capabilities_of("market") == ("data.query",)
    assert graph.capabilities_of("chart") == ("chart.render",)
    assert graph.capabilities_of("indicator") == ()


def test_consumed_by() -> None:
    graph = sample_graph()
    assert graph.consumed_by("chart") == ("data.query",)
    assert graph.consumed_by("market") == ()
    assert graph.consumed_by("indicator") == ("data.query",)


def test_unresolved_consumers() -> None:
    assert sample_graph().unresolved_consumers() == ()
    graph = CapabilityGraph(
        (
            manifest(
                "consumer",
                capabilities_consumed=(
                    CapabilityId("data.query"),
                    CapabilityId("chart.render"),
                ),
            ),
        )
    )
    assert graph.unresolved_consumers() == ("chart.render", "data.query")
