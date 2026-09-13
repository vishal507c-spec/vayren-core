"""Portfolio workspace: institutional redesign, real state rendering, honest unavailable states."""

from PySide6.QtWidgets import QTableWidget

from app.ui.portfolio_workspace import PortfolioWorkspace


def cell_text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None
    return item.text()


def _state(**overrides):
    state = {
        "mode": "PAPER",
        "broker": {
            "name": "paper",
            "environment": "paper",
            "connected": True,
            "status": "CONNECTED",
        },
        "funds": {
            "account": "paper-acct",
            "environment": "paper",
            "currency": "INR",
            "equity": 100000.0,
            "available": 80000.0,
            "used": 20000.0,
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
                "symbol": "TEST",
                "side": "BUY",
                "quantity": 10.0,
                "status": "FILLED",
            }
        ],
        "fills": [{"time": "t", "symbol": "TEST", "quantity": 10.0, "price": 100.0}],
        "pnl": {"realized": 0.0, "unrealized": 100.0, "total": 100.0, "wins": 1, "losses": 0},
        "risk": {"status": "READY"},
        "reconciliation": {"status": "CLEAN"},
        "kill": {"halted": False},
        "lifecycle": "RUNNING",
    }
    state.update(overrides)
    return state


def test_account_status_connected(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    assert workspace._status_indicator._text.text() == "ACCOUNT CONNECTED"


def test_kpi_equity_rendered(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    assert "100,000" in workspace._kpi_equity._value.text()


def test_positions_table_rendered(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    assert workspace._positions_table.rowCount() == 1
    assert cell_text(workspace._positions_table, 0, 0) == "TEST"
    alloc_col = 8
    assert cell_text(workspace._positions_table, 0, alloc_col) == "1.1%"


def test_orders_table_rendered(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    assert workspace._orders_table.rowCount() == 1


def test_fills_table_rendered(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    assert workspace._fills_table.rowCount() == 1


def test_performance_summary_visible_with_data(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    workspace.show()
    assert workspace._perf_summary.isVisible() is True
    assert workspace._perf_empty.isVisible() is False


def test_missing_funds_is_unavailable_not_zero(qt_app) -> None:
    assert qt_app is not None
    state = _state()
    del state["funds"]
    state["broker"] = {"name": "paper", "environment": "paper", "connected": True}
    workspace = PortfolioWorkspace()
    workspace.set_state(state)
    assert workspace._kpi_equity._value.text() == "N/A"
    assert cell_text(workspace._positions_table, 0, 8) == "N/A"


def test_not_configured_state(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state({})
    workspace.show()
    assert workspace._status_indicator._text.text() == "ACCOUNT NOT CONFIGURED"
    assert workspace._positions_not_configured.isVisible() is True
    assert workspace._positions_table.isVisible() is False


def test_empty_portfolio_flat(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    state = _state()
    state["position"] = {"flat": True}
    state["positions"] = []
    workspace.set_state(state)
    workspace.show()
    assert workspace._positions_empty.isVisible() is True
    assert workspace._positions_table.isVisible() is False


def test_blocked_risk_gate(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state(risk={"status": "BLOCKED"}))
    assert workspace._gate_trading.pill.text() == "BLOCKED"


def test_allocation_bars_rendered(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    workspace.show()
    assert workspace._alloc_rows.isVisible() is True
    assert workspace._alloc_empty.isVisible() is False
    assert workspace._alloc_rows_lay.count() == 1


def test_allocation_unavailable_without_equity(qt_app) -> None:
    assert qt_app is not None
    state = _state()
    del state["funds"]
    state["broker"] = {"name": "paper", "environment": "paper", "connected": True}
    workspace = PortfolioWorkspace()
    workspace.set_state(state)
    workspace.show()
    assert workspace._alloc_empty.isVisible() is True


def test_position_detail_on_selection(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    workspace.show()
    workspace._positions_table.selectRow(0)
    assert workspace._detail_symbol.text() == "TEST"
    assert workspace._detail_fields["Qty"].text() == "10.00"


def test_health_healthy_when_configured(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    assert workspace._health_badge.text() == "HEALTHY"


def test_health_not_configured(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state({})
    assert workspace._health_badge.text() == "NOT CONFIGURED"


def test_refresh_updates_timestamp(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    workspace.refresh()
    assert workspace._updated_label.text().startswith("Updated ")


def test_order_tab_switching(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    workspace.show()
    workspace._switch_order_tab("fills")
    assert workspace._tab_fills.isChecked() is True
    assert workspace._fills_table.isVisible() is True
    assert workspace._orders_table.isVisible() is False
    workspace._switch_order_tab("orders")
    assert workspace._tab_orders.isChecked() is True
    assert workspace._orders_table.isVisible() is True
