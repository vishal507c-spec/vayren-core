"""Portfolio workspace: real state rendering, honest unavailable states."""

from PySide6.QtWidgets import QTableWidget

from app.ui.portfolio_workspace import PortfolioWorkspace


def cell_text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None
    return item.text()


def _state(**overrides):
    state = {
        "mode": "PAPER",
        "broker": {"name": "paper", "environment": "paper"},
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


def test_account_and_allocation_rendered(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state())
    assert workspace._account_block.value("equity").text() == "100,000.00"
    assert workspace._positions_table.rowCount() == 1
    # 1100 / 100000 = 1.1%
    assert cell_text(workspace._positions_table, 0, 4) == "1.1%"
    assert workspace._orders_table.rowCount() == 1
    assert workspace._fills_table.rowCount() == 1
    assert workspace._perf_block.value("wins").text() == "1"
    assert workspace._status_badges.text() == "READY"


def test_missing_funds_is_unavailable_not_zero(qt_app) -> None:
    assert qt_app is not None
    state = _state()
    del state["funds"]
    workspace = PortfolioWorkspace()
    workspace.set_state(state)
    assert workspace._account_block.value("equity").text() == "UNAVAILABLE"
    assert "unavailable" in workspace._funds_note.text().lower()
    # Allocation falls back honestly when no equity basis exists.
    assert cell_text(workspace._positions_table, 0, 4) == "N/A"


def test_empty_portfolio_states(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state({})
    workspace.show()
    assert workspace._positions_empty.isVisible() is True
    assert workspace._positions_table.rowCount() == 0
    assert workspace._orders_table.rowCount() == 0
    assert workspace._account_block.value("equity").text() == "UNAVAILABLE"


def test_blocked_status_tone(qt_app) -> None:
    assert qt_app is not None
    workspace = PortfolioWorkspace()
    workspace.set_state(_state(risk={"status": "BLOCKED"}))
    assert workspace._status_badges.text() == "BLOCKED"
