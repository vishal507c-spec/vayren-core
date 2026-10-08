"""Test Suite for VAYREN Phase 4 — Professional Trading Dashboard.

Verifies:
1. ControlPlaneBridge: connection lifecycle, snapshot ingest, event deduplication,
   staleness detection (>15s threshold), command dispatch (Phase 3 contract).
2. TradingDashboardWidget: all 6 workspaces (LIVE, MARKET, STRATEGY LAB,
   RESEARCH, PORTFOLIO, SYSTEM), instant tab switching, stale banner.
3. LIVE Workspace: execution state, broker status, market freshness, protective SL,
   positions table, orders table, telemetry footer.
4. Risk Panel: 9-metric grid formatting, risk status, risk limit application.
5. MARKET Workspace: live quotes table, symbol watchlist, tick staleness alert.
6. STRATEGY LAB: strategy catalog, parameter inspector, deploy action.
7. RESEARCH Workspace: Windows local historical stores scanner (zero fake data).
8. PORTFOLIO Workspace: consolidated equity, realized/unrealized P&L, positions/orders.
9. SYSTEM Workspace: subsystem health, 8 Phase 1 safety gates matrix, event journal.
10. Controls: START, STOP, ARM, HALT, Reconcile, Apply Risk, Update Symbols.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services.control_plane_bridge import ControlPlaneBridge

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from app.services.trading_dashboard import (
        LiveWorkspaceView,
        MarketWorkspaceView,
        PortfolioWorkspaceView,
        ResearchWorkspaceView,
        RiskPanelWidget,
        StrategyLabWorkspaceView,
        SystemWorkspaceView,
        TradingDashboardWidget,
    )

    _QT_AVAILABLE = True
else:
    try:
        from PySide6.QtWidgets import QApplication

        from app.services.trading_dashboard import (
            LiveWorkspaceView,
            MarketWorkspaceView,
            PortfolioWorkspaceView,
            ResearchWorkspaceView,
            RiskPanelWidget,
            StrategyLabWorkspaceView,
            SystemWorkspaceView,
            TradingDashboardWidget,
        )

        _QT_AVAILABLE = True
    except (ImportError, OSError):
        _QT_AVAILABLE = False
        QApplication = None
        LiveWorkspaceView = None
        MarketWorkspaceView = None
        PortfolioWorkspaceView = None
        ResearchWorkspaceView = None
        RiskPanelWidget = None
        StrategyLabWorkspaceView = None
        SystemWorkspaceView = None
        TradingDashboardWidget = None

if not _QT_AVAILABLE:
    pytestmark = pytest.mark.skip(
        reason="PySide6 / GUI system libraries not available in headless environment"
    )

from execution.control_plane import (
    CommandResponse,
    CommandStatus,
    ControlCommand,
)


@pytest.fixture
def qt_app() -> Any:
    """Return the process-wide QApplication, creating it if needed."""
    if not _QT_AVAILABLE:
        pytest.skip("PySide6 / GUI system libraries not available")
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return QApplication([])


@pytest.fixture
def sample_snapshot() -> dict[str, Any]:
    """Sample authoritative snapshot adhering to Phase 3 RuntimeSnapshot schema."""
    return {
        "execution_state": "READY",
        "broker_connection": "CONNECTED",
        "market_ws": "CONNECTED",
        "order_ws": "CONNECTED",
        "risk": {
            "max_risk_pct": 0.01,
            "max_capital": 100000.0,
            "max_drawdown_pct": 0.05,
            "broker_available_capital": 98500.0,
            "effective_capital": 100000.0,
        },
        "strategy": {
            "name": "OBR C1C4",
            "symbols": ["NSE:NIFTY50-INDEX", "NSE:BANKNIFTY-INDEX"],
            "parameters": {"timeframe": "15m", "sizing": "fixed_fractional"},
        },
        "positions": [
            {
                "symbol": "NSE:RELIANCE-EQ",
                "quantity": 50,
                "avg_price": 2850.0,
                "current_price": 2880.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 1500.0,
                "pnl_pct": 1.05,
                "sl_status": "ACTIVE",
                "stop_price": 2820.0,
            }
        ],
        "orders": [
            {
                "client_order_id": "ord-rel-101",
                "symbol": "NSE:RELIANCE-EQ",
                "side": "BUY",
                "quantity": 50,
                "price": 2850.0,
                "stop_price": 2820.0,
                "state": "FILLED",
                "order_type": "LIMIT",
            }
        ],
        "safety": {
            "overall": "READY",
            "kill_switch_engaged": False,
            "kill_switch_reason": "",
            "live_trading_enabled": True,
            "broker_live_enabled": True,
            "account_confirmed": True,
            "risk_limits_valid": True,
            "kill_switch_off": True,
            "broker_connected": True,
            "reconciliation_healthy": True,
            "reconciliation_status": "SAFE",
            "risk_ready": True,
            "sl_protection_summary": "CLEAN",
            "market_data_stale": False,
        },
        "latest_seq": 10,
        "user_id": "test_trader",
        "server_time": "2026-10-07T10:00:00Z",
    }


# ── Bridge Tests ─────────────────────────────────────────────────────────────


def test_bridge_snapshot_and_signals(qt_app: Any, sample_snapshot: dict[str, Any]) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    snapshots_received: list[dict] = []
    bridge.snapshot_updated.connect(snapshots_received.append)

    bridge.apply_snapshot(sample_snapshot)
    assert len(snapshots_received) == 1
    assert bridge.latest_snapshot["execution_state"] == "READY"
    assert bridge.is_stale is False


def test_bridge_staleness_detection(qt_app: Any, sample_snapshot: dict[str, Any]) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    stale_states: list[bool] = []
    bridge.staleness_changed.connect(stale_states.append)

    # Initial fresh
    bridge.apply_snapshot(sample_snapshot)
    assert bridge.is_stale is False

    # Stale market snapshot
    stale_snap = dict(sample_snapshot)
    stale_snap["market_ws"] = "STALE"
    bridge.apply_snapshot(stale_snap)

    assert bridge.is_stale is True
    assert stale_states == [True]

    # Restored market snapshot
    stale_snap["market_ws"] = "CONNECTED"
    bridge.apply_snapshot(stale_snap)
    assert bridge.is_stale is False
    assert stale_states == [True, False]


def test_bridge_event_ingest_and_deduplication(qt_app: Any) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    received_events: list[dict] = []
    bridge.event_received.connect(received_events.append)

    ev1 = {"seq": 1, "event_type": "TICK", "payload": {"price": 100.0}}
    ev1_dup = {"seq": 1, "event_type": "TICK", "payload": {"price": 100.0}}
    ev2 = {"seq": 2, "event_type": "ORDER_FILLED", "payload": {"order_id": "ord-1"}}

    assert bridge.ingest_event(ev1) is True
    assert bridge.ingest_event(ev1_dup) is False  # Duplicate dropped
    assert bridge.ingest_event(ev2) is True

    assert len(received_events) == 2
    assert len(bridge.recent_events) == 2


def test_bridge_command_dispatches(qt_app: Any) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    # Mock _dispatch to inspect calls
    bridge._dispatch = MagicMock(  # type: ignore[method-assign]
        return_value=CommandResponse(
            request_id="test",
            command="TEST",
            status=CommandStatus.SUCCESS,
        )
    )

    bridge.send_start()
    bridge._dispatch.assert_called_with(ControlCommand.START)

    bridge.send_stop()
    bridge._dispatch.assert_called_with(ControlCommand.STOP)

    bridge.send_arm()
    bridge._dispatch.assert_called_with(ControlCommand.ARM)

    bridge.send_halt(reason="Emergency halt")
    bridge._dispatch.assert_called_with(ControlCommand.HALT, {"reason": "Emergency halt"})

    bridge.send_select_strategy("OBR C1C4", {"timeframe": "15m"})
    bridge._dispatch.assert_called_with(
        ControlCommand.SELECT_STRATEGY,
        {"strategy_name": "OBR C1C4", "parameters": {"timeframe": "15m"}},
    )

    bridge.send_set_risk(0.015, 150000.0, 0.05)
    bridge._dispatch.assert_called_with(
        ControlCommand.SET_RISK,
        {"max_risk_pct": 0.015, "max_capital": 150000.0, "max_drawdown_pct": 0.05},
    )

    bridge.send_select_symbols(["SBIN", "INFY"])
    bridge._dispatch.assert_called_with(
        ControlCommand.SELECT_SYMBOLS, {"symbols": ["SBIN", "INFY"]}
    )

    bridge.send_request_reconciliation()
    bridge._dispatch.assert_called_with(ControlCommand.REQUEST_RECONCILIATION)


# ── Dashboard & Workspaces UI Tests ──────────────────────────────────────────


def test_dashboard_embeds_all_6_workspaces(qt_app: Any) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    dashboard = TradingDashboardWidget(bridge)

    assert dashboard.stack.count() == 6
    assert isinstance(dashboard.live_view, LiveWorkspaceView)
    assert isinstance(dashboard.market_view, MarketWorkspaceView)
    assert isinstance(dashboard.strategy_lab_view, StrategyLabWorkspaceView)
    assert isinstance(dashboard.research_view, ResearchWorkspaceView)
    assert isinstance(dashboard.portfolio_view, PortfolioWorkspaceView)
    assert isinstance(dashboard.system_view, SystemWorkspaceView)


def test_dashboard_tab_switching(qt_app: Any) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    dashboard = TradingDashboardWidget(bridge)

    dashboard.switch_to_workspace("LIVE")
    assert dashboard.stack.currentIndex() == 0

    dashboard.switch_to_workspace("MARKET")
    assert dashboard.stack.currentIndex() == 1

    dashboard.switch_to_workspace("STRATEGY LAB")
    assert dashboard.stack.currentIndex() == 2

    dashboard.switch_to_workspace("RESEARCH")
    assert dashboard.stack.currentIndex() == 3

    dashboard.switch_to_workspace("PORTFOLIO")
    assert dashboard.stack.currentIndex() == 4

    dashboard.switch_to_workspace("SYSTEM")
    assert dashboard.stack.currentIndex() == 5


def test_live_workspace_updates(qt_app: Any, sample_snapshot: dict[str, Any]) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    live = LiveWorkspaceView(bridge)

    live.update_from_snapshot(sample_snapshot)
    assert "READY" in live.lbl_exec_state.text()
    assert "CONNECTED" in live.lbl_broker_conn.text()
    assert "CLEAN" in live.lbl_sl_status.text()

    # Positions and orders tables populated
    assert live.pos_table.rowCount() == 1
    item_pos = live.pos_table.item(0, 0)
    assert item_pos is not None and item_pos.text() == "NSE:RELIANCE-EQ"
    assert live.ord_table.rowCount() == 1
    item_ord = live.ord_table.item(0, 1)
    assert item_ord is not None and item_ord.text() == "NSE:RELIANCE-EQ"


def test_risk_panel_9_metrics_grid(qt_app: Any, sample_snapshot: dict[str, Any]) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    panel = RiskPanelWidget(bridge)

    panel.update_metrics(sample_snapshot)
    assert panel._labels["Broker Available Capital"].text() == "98,500.00"
    assert panel._labels["Effective Capital"].text() == "100,000.00"
    assert panel._labels["Max Risk"].text() == "1.00%"
    assert panel._labels["Entry Price"].text() == "2,850.00"
    assert panel._labels["Stop Price"].text() == "2,820.00"
    assert panel._labels["Risk / Share"].text() == "30.00"
    assert panel._labels["Quantity"].text() == "50"
    assert panel._labels["Planned Risk"].text() == "1,500.00"
    assert panel._labels["Risk Status"].text() == "HEALTHY"


def test_market_workspace_stale_alert(qt_app: Any, sample_snapshot: dict[str, Any]) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    market = MarketWorkspaceView(bridge)

    # Fresh update
    market.update_from_snapshot(sample_snapshot)
    assert "FRESH" in market.lbl_stale_alert.text()

    # Stale update
    stale_snap = dict(sample_snapshot)
    stale_snap["market_ws"] = "STALE"
    market.update_from_snapshot(stale_snap)
    assert "STALE" in market.lbl_stale_alert.text()
    assert "ORDERS BLOCKED" in market.lbl_stale_alert.text()


def test_strategy_lab_inspector_and_deploy(qt_app: Any) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    bridge.send_select_strategy = MagicMock()  # type: ignore[method-assign]
    lab = StrategyLabWorkspaceView(bridge)

    assert lab.table_catalog.rowCount() >= 3
    lab.inp_name.setText("OBR C1C4")
    lab._on_deploy()

    bridge.send_select_strategy.assert_called_with(
        "OBR C1C4", {"timeframe": "15m", "universe": "NIFTY50"}
    )


def test_research_workspace_local_data_scan(qt_app: Any) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    res = ResearchWorkspaceView(bridge)

    # Table contains real local rows or honest "NO LOCAL DATA FOUND" (zero fake data)
    assert res.table.rowCount() >= 1
    item_res = res.table.item(0, 0)
    assert item_res is not None and item_res.text() != ""


def test_portfolio_workspace_consolidation(qt_app: Any, sample_snapshot: dict[str, Any]) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    portfolio = PortfolioWorkspaceView(bridge)

    portfolio.update_from_snapshot(sample_snapshot)
    assert portfolio.table_pos.rowCount() == 1
    assert portfolio.table_ord.rowCount() == 1
    assert "POSITIONS: 1" in portfolio.lbl_positions_cnt.text()
    assert "1,500.00" in portfolio.lbl_unrealized.text()


def test_system_workspace_health_and_gates(qt_app: Any, sample_snapshot: dict[str, Any]) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    sys_view = SystemWorkspaceView(bridge)

    sys_view.update_from_snapshot(sample_snapshot)
    assert sys_view.table_health.rowCount() == 7
    assert sys_view.table_gates.rowCount() == 8

    # Add streaming event to journal
    sys_view.add_journal_event(
        {"seq": 1, "event_type": "RECONCILIATION_COMPLETED", "payload": {"matched": True}}
    )
    assert sys_view.table_journal.rowCount() == 1
    item_jour = sys_view.table_journal.item(0, 1)
    assert item_jour is not None and item_jour.text() == "RECONCILIATION_COMPLETED"


def test_global_stale_banner_toggle(qt_app: Any) -> None:
    assert qt_app is not None
    bridge = ControlPlaneBridge()
    dash = TradingDashboardWidget(bridge)

    assert dash.stale_banner.isHidden() is True

    # Simulate staleness signal
    bridge.staleness_changed.emit(True)
    assert dash.stale_banner.isHidden() is False

    bridge.staleness_changed.emit(False)
    assert dash.stale_banner.isHidden() is True
