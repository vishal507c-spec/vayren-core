"""Responsive geometry: no clipping/overlap at production window sizes.

Offscreen harness (real Segoe UI metrics via conftest): each workspace is
resized to five production geometries and inspected for the screenshot bug
classes — clipped unwrapped text, zero-size critical controls, missing
scroll containment, degenerate tables. Scroll-area inhabitants are exempt
from width checks (they scroll by design).
"""

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QTableWidget

from app.ui.live_workspace import LiveWorkspace
from app.ui.portfolio_workspace import PortfolioWorkspace
from app.ui.research_workspace import ResearchWorkspace
from app.ui.top_nav_bar import TopNavBar

SIZES = [(1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440)]


def _pump() -> None:
    app = QApplication.instance()
    assert app is not None
    app.processEvents()


def _in_scroll(label: QLabel) -> bool:
    parent = label.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            return True
        parent = parent.parentWidget()
    return False


def _assert_labels_fit(root) -> list[str]:
    problems = []
    for label in root.findChildren(QLabel):
        if not label.isVisible():
            continue
        text = label.text()
        if not text or "\n" in text or label.wordWrap():
            continue
        if _in_scroll(label):
            continue
        if label.minimumWidth() > 0:
            continue
        need = label.fontMetrics().horizontalAdvance(text)
        if need > label.width() + 2:
            problems.append(f"{label.text()[:24]!r} needs {need}px in {label.width()}px")
    return problems


def _assert_tables_sane(root) -> list[str]:
    problems = []
    for table in root.findChildren(QTableWidget):
        if not table.isVisible():
            continue
        if table.columnCount() == 0:
            problems.append("table with zero columns")
        header = table.horizontalHeader()
        if table.viewport().width() <= 0:
            problems.append("table with zero-width viewport")
        widths = [header.sectionSize(i) for i in range(table.columnCount())]
        if widths and max(widths) <= 0:
            problems.append("table with all-zero column widths")
    return problems


def _live_state() -> dict:
    return {
        "mode": "PAPER",
        "broker": {"name": "paper", "environment": "paper", "connected": True},
        "strategy": {
            "id": "sma",
            "version": "1",
            "status": "RUNNING",
            "mode": "PAPER",
            "instrument": "TEST",
            "timeframe": "15m",
            "live_supported": False,
            "warmup": "ok",
            "state": "RUNNING",
            "params": {"fast": 2},
        },
        "position": {
            "symbol": "TEST",
            "side": "LONG",
            "quantity": 10.0,
            "avg_price": 100.0,
            "current_price": 110.0,
            "unrealized": 100.0,
            "realized": 0.0,
            "exposure": 1100.0,
        },
        "orders": [
            {
                "order_id": "c1",
                "strategy": "s",
                "symbol": "T",
                "side": "BUY",
                "quantity": 1.0,
                "type": "MARKET",
                "price": 1.0,
                "status": "SUBMITTED",
                "time": "t",
                "broker": "b",
            }
        ],
        "fills": [],
        "pnl": {
            "realized": 0.0,
            "unrealized": 100.0,
            "total": 100.0,
            "exposure": 1100.0,
            "orders": 1,
            "fills": 0,
            "wins": 0,
            "losses": 0,
        },
        "risk": {"status": "READY", "limits": [["max_order_qty", 500.0, "ok"]], "decisions": []},
        "reconciliation": {
            "status": "CLEAN",
            "positions": "match",
            "orders": "match",
            "last_check": "t",
            "mismatches": 0,
            "blocks_live": False,
        },
        "kill": {"halted": False, "level": "global"},
        "gates": [
            {"name": "BROKER_ADAPTER_READY", "status": "READY", "reason": ""},
            {
                "name": "CREDENTIALS_READY",
                "status": "NOT READY",
                "reason": "paper session: credentials not required but live would need them",
            },
            {
                "name": "ACCOUNT_CONFIRMED",
                "status": "NOT READY",
                "reason": "paper session: no live account",
            },
            {"name": "RISK_CONFIGURATION_VALID", "status": "READY", "reason": ""},
            {"name": "EXECUTION_SAFETY_ENABLED", "status": "READY", "reason": ""},
        ],
        "can_arm": False,
        "arm_blockers": ["LIVE broker is not configured"],
        "can_halt": False,
        "lifecycle": "RUNNING",
        "events": [],
        "market_symbol": "TEST",
        "market_timeframe": "15m",
        "market_bars": None,
    }


def test_live_no_clip_at_all_sizes(qt_app) -> None:
    assert qt_app is not None
    workspace = LiveWorkspace()
    workspace.set_state(_live_state())
    for width, height in SIZES:
        workspace.resize(width, height)
        workspace.show()
        _pump()
        problems = _assert_labels_fit(workspace)
        assert problems == [], f"{width}x{height}: {problems[:5]}"
        assert _assert_tables_sane(workspace) == []
    workspace.hide()


def test_live_rail_scrolls(qt_app) -> None:
    assert qt_app is not None
    workspace = LiveWorkspace()
    workspace.set_state(_live_state())
    scrolls = workspace.findChildren(QScrollArea)
    assert len(scrolls) >= 1
    scroll = scrolls[0]
    assert scroll.widgetResizable() is True


def test_live_critical_controls_sized(qt_app) -> None:
    assert qt_app is not None
    workspace = LiveWorkspace()
    workspace.set_state(_live_state())
    workspace.resize(1280, 720)
    workspace.show()
    _pump()
    for widget in (
        workspace._arm_button,
        workspace._halt_button,
        workspace._mode_selector,
    ):
        assert widget.width() > 0 and widget.height() > 0
    workspace.hide()


def test_research_no_clip_at_all_sizes(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    for width, height in SIZES:
        workspace.resize(width, height)
        workspace.show()
        _pump()
        problems = _assert_labels_fit(workspace)
        assert problems == [], f"{width}x{height}: {problems[:5]}"
        assert _assert_tables_sane(workspace) == []
        assert workspace._run_button.width() > 0
        assert workspace._save_button.width() > 0
    workspace.hide()


def test_portfolio_no_clip_at_all_sizes(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_live_state())
    for width, height in SIZES:
        workspace.resize(width, height)
        workspace.show()
        _pump()
        problems = _assert_labels_fit(workspace)
        assert problems == [], f"{width}x{height}: {problems[:5]}"
        assert _assert_tables_sane(workspace) == []
    workspace.hide()


def test_nav_no_clip_at_all_sizes(qt_app) -> None:
    assert qt_app is not None
    nav = TopNavBar()
    for width, _height in SIZES:
        nav.resize(width, 34)
        nav.show()
        _pump()
        problems = _assert_labels_fit(nav)
        assert problems == [], f"{width}: {problems[:5]}"
    nav.hide()
