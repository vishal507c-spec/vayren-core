"""Proof of concept — the universal foundation with existing components.

Demonstrates the chain: Component → Manifest → Capabilities → Registry →
Discovery using the real ``market`` and ``chart`` components. The manifests
are the production ones (``market.manifest`` / ``chart.manifest``); this test
proves they describe the real system.
"""

from chart.engine.chart_engine import ChartEngine
from chart.manifest import chart_manifest
from market.manifest import market_manifest
from market.repository.candle_repository import CandleRepository
from market.repository.symbol_repository import SymbolRepository

from core.registry.component_registry import ComponentRegistry


def build_system() -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register(
        market_manifest(),
        implementations={
            "data.query.candles": CandleRepository,
            "data.query.timeframes": CandleRepository,
            "data.query.quotes": SymbolRepository,
            "data.transform.aggregate": CandleRepository,
        },
    )
    registry.register(
        chart_manifest(),
        implementations={"chart.render": ChartEngine},
    )
    return registry


def test_component_manifest_chain() -> None:
    registry = build_system()
    assert "market" in registry
    assert "chart" in registry
    assert registry.component("market").manifest.identity.name == "market"
    assert registry.component("chart").manifest.identity.name == "chart"


def test_capability_discovery_answers_questions() -> None:
    registry = build_system()
    providers = registry.providers("data.query.candles")
    assert [provider.component.name for provider in providers] == ["market"]
    assert providers[0].implementation is CandleRepository
    assert registry.providers("chart.render")[0].component.name == "chart"
    assert registry.providers("chart.render")[0].implementation is ChartEngine


def test_prefix_discovery_lists_data_capabilities() -> None:
    registry = build_system()
    found = [str(capability) for capability in registry.find("data.")]
    assert found == [
        "data.query.candles",
        "data.query.quotes",
        "data.query.timeframes",
        "data.transform.aggregate",
    ]


def test_dependency_graph_answers_questions() -> None:
    registry = build_system()
    chart_deps = [dep.name for dep in registry.component("chart").manifest.dependencies]
    market_deps = [dep.name for dep in registry.component("market").manifest.dependencies]
    assert chart_deps == ["core", "market"]
    assert market_deps == ["core"]


def test_capabilities_of_each_component() -> None:
    registry = build_system()
    assert [str(capability) for capability in registry.capabilities("chart")] == ["chart.render"]
    assert "data.query.candles" in [str(c) for c in registry.capabilities("market")]


def test_summary_describes_the_system() -> None:
    summary = build_system().summary()
    assert "components: 2" in summary
    assert "- market v1.0.0 [storage]" in summary
    assert "- chart v1.0.0 [presentation]" in summary
    assert "    provides data.query.candles" in summary
    assert "    provides chart.render" in summary
