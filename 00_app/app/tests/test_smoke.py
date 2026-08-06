"""End-to-end smoke test: full event flow on offscreen Qt.

Verifies the complete Phase 1 chain:
AppStarted → LoadSymbol → DataLoaded → ChartReady → WindowRendered.
"""

from pathlib import Path

from chart.events.window_rendered import WindowRendered
from PySide6.QtWidgets import QApplication

from app.bootstrap.bootstrap import Bootstrap
from app.tests.conftest import seed_database


def test_full_event_flow_reaches_window_rendered(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    database_path = seed_database(tmp_path / "smoke.db", symbol="SPY", count=50)
    bootstrap = Bootstrap(database_path=database_path, symbol="SPY", limit=50)

    rendered: list[WindowRendered] = []
    bootstrap.bus.subscribe(WindowRendered, rendered.append)

    bootstrap.start()

    assert len(rendered) == 1
    window = bootstrap.services.get("chart_window")
    assert window.windowTitle() == "Vayren — SPY (50 bars)"
    assert window.isVisible()


def test_bootstrap_registers_all_services(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None
    database_path = seed_database(tmp_path / "smoke.db", symbol="SPY", count=10)
    bootstrap = Bootstrap(database_path=database_path, symbol="SPY", limit=10)
    assert sorted(bootstrap.services.list()) == [
        "app_lifecycle",
        "candle_repository",
        "chart_engine",
        "chart_window",
        "market_data_loader",
        "sqlite_database",
    ]
