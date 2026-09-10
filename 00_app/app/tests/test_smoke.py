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
    # Auto-load: first symbol charted on open
    assert window.windowTitle() == "VAYREN — AMBUJACEM"
    assert window.isVisible()
    assert len(rendered) == 1

    watchlist = window.watchlist
    assert watchlist.symbols == ("AMBUJACEM", "BPCL")

    watchlist.symbol_selected.emit("AMBUJACEM")

    assert len(rendered) == 2
    assert window.windowTitle() == "VAYREN — AMBUJACEM"
    assert watchlist.current_symbol == "AMBUJACEM"


def test_switch_symbol_replaces_chart(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 50, "BPCL": 30})
    bootstrap = Bootstrap(data_dir=data_dir, limit=50)

    rendered: list[WindowRendered] = []
    bootstrap.bus.subscribe(WindowRendered, rendered.append)

    bootstrap.start()
    window = bootstrap.services.get("chart_window")
    # Auto-load gives first render for AMBUJACEM; next two emits give total 3
    window.watchlist.symbol_selected.emit("AMBUJACEM")
    window.watchlist.symbol_selected.emit("BPCL")

    assert len(rendered) == 3
    assert window.windowTitle() == "VAYREN — BPCL"
    assert window.watchlist.current_symbol == "BPCL"


def test_startup_opens_maximized(qt_app: QApplication, tmp_path: Path) -> None:
    """Startup policy: every fresh launch opens maximized (native work area).

    Guards against regressions to a small restored startup size — the window
    must be maximized right after start, before any manual user action.
    """
    assert qt_app is not None
    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 10})
    bootstrap = Bootstrap(data_dir=data_dir, limit=10)
    bootstrap.start()
    window = bootstrap.services.get("chart_window")
    qt_app.processEvents()
    assert window.isVisible()
    assert window.isMaximized()


def test_bootstrap_registers_all_services(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    data_dir = seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 10})
    bootstrap = Bootstrap(data_dir=data_dir, limit=10)
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
