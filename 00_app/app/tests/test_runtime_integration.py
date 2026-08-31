"""Runtime integration — the universal architecture connected to the real system.

Proves the 10 required properties end to end: real manifests, discoverable
capabilities, old registry compatibility, new capability lookup, SystemModel
visibility, unchanged EventBus, unchanged startup, serializable snapshot,
and negligible overhead.
"""

import json
import time
from pathlib import Path

from chart.engine.chart_engine import ChartEngine
from chart.events.window_rendered import WindowRendered
from core.contracts.component import ComponentId
from market.repository.symbol_repository import SymbolRepository
from PySide6.QtWidgets import QApplication, QSplitter

from app.bootstrap.bootstrap import Bootstrap
from app.tests.conftest import seed_symbol_directory


def make_bootstrap(qt_app: QApplication, tmp_path: Path) -> Bootstrap:
    assert qt_app is not None
    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 50, "BPCL": 30})
    return Bootstrap(data_dir=data_dir, limit=50)


def test_real_components_have_valid_manifests(qt_app: QApplication, tmp_path: Path) -> None:
    bootstrap = make_bootstrap(qt_app, tmp_path)
    assert "market" in bootstrap.components
    assert "chart" in bootstrap.components
    assert "historical_data" in bootstrap.components
    market = bootstrap.components.component("market").manifest
    chart = bootstrap.components.component("chart").manifest
    data = bootstrap.components.component("historical_data").manifest
    assert market.identity.name == "market"
    assert str(market.version) == "1.0.0"
    assert chart.identity.name == "chart"
    assert chart.capabilities_consumed[0].value == "data.query.candles"
    assert data.identity.name == "historical_data"
    assert data.dependencies == (ComponentId("core"),)


def test_real_capabilities_are_discoverable(qt_app: QApplication, tmp_path: Path) -> None:
    bootstrap = make_bootstrap(qt_app, tmp_path)
    found = [str(capability) for capability in bootstrap.components.find("data.")]
    assert found == [
        "data.query.candles",
        "data.query.quotes",
        "data.query.timeframes",
        "data.transform.aggregate",
    ]
    providers = bootstrap.components.providers("data.query.candles")
    assert [provider.component.name for provider in providers] == ["market"]


def test_existing_registry_still_works(qt_app: QApplication, tmp_path: Path) -> None:
    bootstrap = make_bootstrap(qt_app, tmp_path)
    assert isinstance(bootstrap.services.get("symbol_repository"), SymbolRepository)
    assert isinstance(bootstrap.services.get("chart_engine"), ChartEngine)
    # New services (strategy/backtest/lab) coexist with the original 11
    services = sorted(bootstrap.services.list())
    for name in (
        "app_lifecycle",
        "chart_engine",
        "chart_window",
        "data_engine",
        "data_window",
        "data_worker",
        "market_data_loader",
        "quote_loader",
        "symbol_list_loader",
        "symbol_repository",
        "timeframe_list_loader",
    ):
        assert name in services
    # Lab platform services are now registered as well
    assert "strategy_registry" in services
    assert "backtest_runner" in services


def test_new_capability_lookup_works(qt_app: QApplication, tmp_path: Path) -> None:
    bootstrap = make_bootstrap(qt_app, tmp_path)
    renderers = bootstrap.components.providers("chart.render")
    assert [provider.component.name for provider in renderers] == ["chart"]
    assert renderers[0].implementation is bootstrap.services.get("chart_engine")
    candles = bootstrap.components.providers("data.query.candles")
    assert candles[0].implementation is bootstrap.services.get("symbol_repository")


def test_system_model_sees_real_components(qt_app: QApplication, tmp_path: Path) -> None:
    model = make_bootstrap(qt_app, tmp_path).system_model
    names = sorted(manifest.identity.name for manifest in model.components())
    assert names == ["backtest", "chart", "historical_data", "market", "strategy"]
    assert model.has_component("market")
    assert model.find_dependencies("chart") == ("core", "market")
    assert model.find_dependencies("historical_data") == ("core",)


def test_system_model_sees_capabilities(qt_app: QApplication, tmp_path: Path) -> None:
    model = make_bootstrap(qt_app, tmp_path).system_model
    assert [str(capability) for capability in model.find_capability("data.")] == [
        "data.query.candles",
        "data.query.quotes",
        "data.query.timeframes",
        "data.transform.aggregate",
    ]
    assert model.find_consumers("data.query.candles") == ("chart",)
    assert model.find_capability("chart.render")[0].value == "chart.render"
    providers = model.find_implementations("data.query.candles")
    assert providers[0].implementation is not None


def test_event_bus_behavior_unchanged(qt_app: QApplication, tmp_path: Path) -> None:
    bootstrap = make_bootstrap(qt_app, tmp_path)
    received: list[WindowRendered] = []
    bootstrap.bus.subscribe(WindowRendered, received.append)
    bootstrap.start()
    # Chart now auto-loads the first symbol on startup (TradingView-style)
    assert len(received) == 1
    window = bootstrap.services.get("chart_window")
    window.watchlist.symbol_selected.emit("AMBUJACEM")
    assert len(received) == 2


def test_startup_flow_unchanged(qt_app: QApplication, tmp_path: Path) -> None:
    bootstrap = make_bootstrap(qt_app, tmp_path)
    rendered: list[WindowRendered] = []
    bootstrap.bus.subscribe(WindowRendered, rendered.append)
    bootstrap.start()
    window = bootstrap.services.get("chart_window")
    # Auto-load: first symbol (AMBUJACEM) is charted immediately on open
    assert window.windowTitle() == "VAYREN — AMBUJACEM"
    assert window.isVisible()
    watchlist = window.watchlist
    assert watchlist.symbols == ("AMBUJACEM", "BPCL")
    assert len(rendered) == 1
    # Emitting the same symbol again still produces a second render (TimeframeChanged)
    watchlist.symbol_selected.emit("AMBUJACEM")
    assert len(rendered) == 2
    assert window.windowTitle() == "VAYREN — AMBUJACEM"


def test_download_panel_is_embedded_in_main_window(qt_app: QApplication, tmp_path: Path) -> None:
    bootstrap = make_bootstrap(qt_app, tmp_path)
    bootstrap.start()
    window = bootstrap.services.get("chart_window")
    panel = bootstrap.services.get("data_window")
    assert panel is window.download
    assert isinstance(panel.parentWidget(), QSplitter)
    assert panel.symbols == ("AMBUJACEM", "BPCL")
    assert not panel.isVisible()
    window.tools.download_button.click()
    assert panel.isVisible()
    assert not window.watchlist.isVisible()
    assert window.active_panel == "download"
    window.tools.download_button.click()
    assert not panel.isVisible()
    assert window.active_panel is None


def test_snapshot_is_serializable(qt_app: QApplication, tmp_path: Path) -> None:
    snapshot = make_bootstrap(qt_app, tmp_path).system_model.snapshot()
    data = json.loads(snapshot.to_json())
    assert set(data) == {"components", "capabilities", "events", "workflows", "gaps"}
    assert "object at" not in json.dumps(data)
    assert "at 0x" not in json.dumps(data)
    names = sorted(component["name"] for component in data["components"])
    assert names == ["backtest", "chart", "historical_data", "market", "strategy"]
    assert data["capabilities"]["data.query.candles"] == ["market"]
    assert data["capabilities"]["chart.render"] == ["chart"]
    assert data["capabilities"]["historical_data.download"] == ["historical_data"]


def test_no_unnecessary_runtime_overhead(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    from chart.manifest import chart_manifest
    from core.registry.component_registry import ComponentRegistry
    from core.system.system_model import SystemModel
    from market.manifest import market_manifest

    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 50})
    start = time.perf_counter()
    bootstrap = Bootstrap(data_dir=data_dir, limit=50)
    startup_elapsed = time.perf_counter() - start
    assert startup_elapsed < 5.0

    started = time.perf_counter()
    registry = ComponentRegistry()
    registry.register(
        market_manifest(),
        implementations={
            "data.query.candles": object(),
            "data.query.timeframes": object(),
            "data.query.quotes": object(),
            "data.transform.aggregate": object(),
        },
    )
    registry.register(chart_manifest(), implementations={"chart.render": object()})
    SystemModel(registry).snapshot()
    architecture_elapsed = time.perf_counter() - started
    assert architecture_elapsed < 0.05, f"architecture build too slow: {architecture_elapsed:.3f}s"

    started = time.perf_counter()
    for _ in range(1000):
        bootstrap.components.providers("data.query.candles")
    lookup_elapsed = time.perf_counter() - started
    assert lookup_elapsed < 0.5, f"capability lookup too slow: {lookup_elapsed:.3f}s"

    started = time.perf_counter()
    for _ in range(1000):
        bootstrap.bus.publish(WindowRendered())
    dispatch_elapsed = time.perf_counter() - started
    assert dispatch_elapsed < 0.5, f"event dispatch too slow: {dispatch_elapsed:.3f}s"
