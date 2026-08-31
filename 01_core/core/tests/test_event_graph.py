"""Event graph tests — producers, consumers, and orphan events."""

from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.manifest import ComponentManifest
from core.system.event_graph import EventGraph


def manifest(
    name: str,
    produced: tuple[str, ...] = (),
    consumed: tuple[str, ...] = (),
) -> ComponentManifest:
    return ComponentManifest(
        identity=ComponentId(name),
        version=ComponentVersion.parse("1.0.0"),
        type="service",
        events_produced=produced,
        events_consumed=consumed,
    )


def sample_graph() -> EventGraph:
    return EventGraph(
        (
            manifest("market", produced=("DataLoaded",)),
            manifest(
                "chart",
                consumed=("DataLoaded", "ChartReady"),
                produced=("ChartReady",),
            ),
            manifest("app", consumed=("ChartReady",)),
            manifest("ghost", produced=("GhostEvent",)),
        )
    )


def test_events_are_sorted_and_unique() -> None:
    assert sample_graph().events() == ("ChartReady", "DataLoaded", "GhostEvent")


def test_producers() -> None:
    graph = sample_graph()
    assert graph.producers("ChartReady") == ("chart",)
    assert graph.producers("DataLoaded") == ("market",)
    assert graph.producers("Missing") == ()


def test_consumers() -> None:
    graph = sample_graph()
    assert graph.consumers("ChartReady") == ("app", "chart")
    assert graph.consumers("GhostEvent") == ()


def test_events_of() -> None:
    graph = sample_graph()
    assert graph.events_of("chart", produced=True) == ("ChartReady",)
    assert graph.events_of("chart", produced=False) == ("ChartReady", "DataLoaded")
    assert graph.events_of("app", produced=True) == ()


def test_unproduced() -> None:
    assert sample_graph().unproduced() == ()
    graph = EventGraph((manifest("app", consumed=("AppStarted",)),))
    assert graph.unproduced() == ("AppStarted",)


def test_unconsumed() -> None:
    assert sample_graph().unconsumed() == ("GhostEvent",)
    graph = EventGraph((manifest("ghost", produced=("GhostEvent",)),))
    assert graph.unconsumed() == ("GhostEvent",)
