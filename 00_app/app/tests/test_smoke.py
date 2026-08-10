"""End-to-end smoke test: full event flow on offscreen Qt.

Verifies the complete Phase 2 chain:
AppStarted → ListSymbols → SymbolsListed → (sidebar)
user selection → LoadSymbol → DataLoaded → ChartReady → WindowRendered.
"""

from pathlib import Path

from chart.events.window_rendered import WindowRendered
from PySide6.QtWidgets import QApplication

from app.bootstrap.bootstrap import Bootstrap
from app.tests.conftest import seed_symbol_directory


def test_full_event_flow_reaches_window_rendered(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 50, "BPCL": 30})
    bootstrap = Bootstrap(data_dir=data_dir, limit=50)

    rendered: list[WindowRendered] = []
    bootstrap.bus.subscribe(WindowRendered, rendered.append)

    bootstrap.start()
    window = bootstrap.services.get("chart_window")
    assert window.windowTitle() == "VAYREN"
    assert window.isVisible()

    sidebar = window.sidebar
    assert [sidebar.item(i).text() for i in range(sidebar.count())] == ["AMBUJACEM", "BPCL"]

    sidebar.symbol_selected.emit("AMBUJACEM")

    assert len(rendered) == 1
    assert window.windowTitle() == "VAYREN — AMBUJACEM"
    assert sidebar.currentItem().text() == "AMBUJACEM"


def test_switch_symbol_replaces_chart(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 50, "BPCL": 30})
    bootstrap = Bootstrap(data_dir=data_dir, limit=50)

    rendered: list[WindowRendered] = []
    bootstrap.bus.subscribe(WindowRendered, rendered.append)

    bootstrap.start()
    window = bootstrap.services.get("chart_window")
    window.sidebar.symbol_selected.emit("AMBUJACEM")
    window.sidebar.symbol_selected.emit("BPCL")

    assert len(rendered) == 2
    assert window.windowTitle() == "VAYREN — BPCL"
    assert window.sidebar.currentItem().text() == "BPCL"


def test_bootstrap_registers_all_services(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 10})
    bootstrap = Bootstrap(data_dir=data_dir, limit=10)
    assert sorted(bootstrap.services.list()) == [
        "app_lifecycle",
        "chart_engine",
        "chart_window",
        "market_data_loader",
        "symbol_list_loader",
        "symbol_repository",
        "timeframe_list_loader",
    ]
