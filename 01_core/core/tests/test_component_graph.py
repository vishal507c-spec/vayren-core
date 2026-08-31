"""Component graph tests — dependencies, dependents, transitive closure, cycles."""

from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.manifest import ComponentManifest
from core.system.component_graph import ComponentGraph


def manifest(name: str, dependencies: tuple[str, ...] = ()) -> ComponentManifest:
    return ComponentManifest(
        identity=ComponentId(name),
        version=ComponentVersion.parse("1.0.0"),
        type="service",
        dependencies=tuple(ComponentId(dep) for dep in dependencies),
    )


def chain_graph() -> ComponentGraph:
    return ComponentGraph(
        (
            manifest("alpha"),
            manifest("beta", ("alpha",)),
            manifest("gamma", ("beta",)),
            manifest("delta", ("alpha",)),
        )
    )


def test_names_include_registered_and_referenced() -> None:
    graph = ComponentGraph((manifest("beta", ("alpha",)), manifest("chart", ("core", "market"))))
    assert graph.names() == ("alpha", "beta", "chart", "core", "market")


def test_dependencies_are_sorted() -> None:
    graph = ComponentGraph((manifest("chart", ("market", "core")),))
    assert graph.dependencies("chart") == ("core", "market")


def test_dependents_are_reverse_edges() -> None:
    graph = chain_graph()
    assert graph.dependents("alpha") == ("beta", "delta")
    assert graph.dependents("beta") == ("gamma",)
    assert graph.dependents("gamma") == ()
    assert graph.dependencies("gamma") == ("beta",)


def test_optional_dependencies_are_separate() -> None:
    graph = ComponentGraph(
        (
            manifest("beta", ("alpha",)),
            ComponentManifest(
                identity=ComponentId("gamma"),
                version=ComponentVersion.parse("1.0.0"),
                type="service",
                optional_dependencies=(ComponentId("beta"),),
            ),
        )
    )
    assert graph.dependencies("gamma") == ()
    assert graph.optional_dependencies("gamma") == ("beta",)
    assert graph.dependents("beta") == ("gamma",)


def test_transitive_dependents() -> None:
    graph = chain_graph()
    assert graph.transitive_dependents("alpha") == ("beta", "delta", "gamma")
    assert graph.transitive_dependents("beta") == ("gamma",)
    assert graph.transitive_dependents("gamma") == ()


def test_transitive_dependencies() -> None:
    graph = chain_graph()
    assert graph.transitive_dependencies("gamma") == ("alpha", "beta")
    assert graph.transitive_dependencies("alpha") == ()


def test_acyclic_graph_has_no_cycle() -> None:
    assert chain_graph().find_cycle() is None


def test_cycle_is_detected() -> None:
    graph = ComponentGraph(
        (
            manifest("alpha", ("beta",)),
            manifest("beta", ("gamma",)),
            manifest("gamma", ("alpha",)),
        )
    )
    assert graph.find_cycle() is not None
    cycle = graph.find_cycle()
    assert cycle is not None
    assert set(cycle) == {"alpha", "beta", "gamma"}


def test_cycle_is_detected_with_acyclic_nodes_present() -> None:
    graph = ComponentGraph(
        (
            manifest("alpha", ("beta",)),
            manifest("beta", ("alpha",)),
            manifest("gamma", ("alpha",)),
        )
    )
    cycle = graph.find_cycle()
    assert cycle is not None
    assert set(cycle) == {"alpha", "beta"}
