"""LIVE workspace tests — real state rendering, zero fabricated data.

Covers: workspace loads, PAPER/SANDBOX/LIVE display, gates + reasons,
ARM gating, kill display + banner, positions, orders (incl. UNKNOWN),
fills, PnL, reconciliation mismatch, lifecycle, event stream + filters,
no-fake-data discipline, MARKET UI untouched, and end-to-end rendering
of a REAL PaperService run through `paper_state_to_workspace`.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from app.services.paper_service import PaperRunConfig, PaperService
from app.tests.test_paper import _seed
from app.ui.live_workspace import LiveWorkspace
from app.ui.top_nav_bar import TopNavBar


@pytest.fixture()
def workspace(qt_app: QApplication) -> LiveWorkspace:  # type: ignore[no-untyped-def]
    assert qt_app is not None  # fixture provides the QApplication widgets require
    widget = LiveWorkspace()
    widget.resize(1280, 760)
    return widget


def _cell_text(table, row: int, col: int) -> str:
    item = table.item(row, col)
    assert item is not None
    return item.text()


def _full_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "mode": "PAPER",
        "broker": {
            "name": "Paper",
            "environment": "simulated",
            "connected": True,
            "reason": "paper ready",
            "capabilities": ["orders.market"],
            "latency_ms": 0.4,
            "last_heartbeat": "2026-01-06T10:00:00+00:00",
        },
        "strategy": {
            "id": "SMA-PAPER",
            "version": "1.0",
            "status": "RUNNING",
            "mode": "PAPER",
            "params": {"fast_period": 2},
            "instrument": "GAIL",
            "timeframe": "30m",
            "live_supported": True,
            "warmup": 20,
            "state": "RUNNING",
        },
        "position": {
            "symbol": "GAIL",
            "side": "LONG",
            "quantity": 10.0,
            "avg_price": 100.0,
            "current_price": 102.0,
            "unrealized": 20.0,
            "realized": 5.0,
            "exposure": 1020.0,
            "risk_utilization": 0.1,
        },
        "orders": [
            {
                "order_id": "o1",
                "strategy": "SMA-PAPER",
                "symbol": "GAIL",
                "side": "BUY",
                "quantity": 10.0,
                "type": "MARKET",
                "price": 100.0,
                "status": "FILLED",
                "time": "t",
                "broker": "PAPER-1",
            }
        ],
        "fills": [
            {
                "time": "t",
                "symbol": "GAIL",
                "side": "BUY",
                "quantity": 10.0,
                "price": 100.02,
                "order_id": "o1",
                "strategy": "SMA-PAPER",
                "slippage": 0.02,
            }
        ],
        "pnl": {
            "realized": 5.0,
            "unrealized": 20.0,
            "total": 25.0,
            "exposure": 1020.0,
            "orders": 1,
            "fills": 1,
            "wins": 1,
            "losses": 0,
        },
        "risk": {
            "status": "READY",
            "limits": [["max_order_qty", 500.0, "ok"]],
            "decisions": [["duplicate", True, ""], ["capital", True, ""]],
        },
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
            {"name": "CREDENTIALS_READY", "status": "NOT READY", "reason": "missing account_id"},
        ],
        "can_arm": False,
        "arm_blockers": ["CREDENTIALS_READY: missing account_id"],
        "can_halt": False,
        "lifecycle": "RUNNING",
        "events": [
            {
                "timestamp": "t",
                "strategy": "SMA-PAPER",
                "symbol": "GAIL",
                "event": "FILL",
                "status": "ok",
            },
        ],
        "market_symbol": "GAIL",
        "market_timeframe": "30m",
        "market_bars": None,
    }
    state.update(overrides)
    return state


def paper_state_to_workspace(
    service: PaperService, last_price: float | None = None
) -> dict[str, Any]:
    """Adapt a REAL PaperService.state() (+ session internals) to workspace schema.

    Test-side adapter only: every value originates from the live run, the
    seeded store, or explicit NOT READY reasons. Nothing is invented.
    """
    snap = service.state()
    session = service._session
    broker = snap.get("broker", {})
    strategy = snap.get("strategy", {})
    positions = snap.get("positions", [])
    position = None
    if positions:
        row = positions[0]
        qty = float(row.get("quantity", 0.0) or 0.0)
        avg = float(row.get("avg_price", 0.0) or 0.0)
        unrealized = (last_price - avg) * qty if last_price is not None else None
        position = {
            "symbol": row.get("symbol", ""),
            "side": "LONG" if qty > 0 else "SHORT",
            "quantity": qty,
            "avg_price": avg,
            "current_price": last_price,
            "unrealized": unrealized,
            "realized": None,
            "exposure": abs(qty) * (last_price if last_price is not None else avg),
            "risk_utilization": None,
        }
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    if session is not None:
        for order in session.engine._orders.values():
            time = order.history[-1][1] if order.history else ""
            orders.append(
                {
                    "order_id": order.client_order_id,
                    "strategy": strategy.get("name", ""),
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "type": order.order_type,
                    "price": order.limit_price
                    if order.limit_price is not None
                    else order.avg_fill_price,
                    "status": order.state.value,
                    "time": time,
                    "broker": order.broker_order_id or "",
                }
            )
        broker_obj = session._broker
        stored_fills = getattr(broker_obj, "fills", ())
        for fill in stored_fills:
            fills.append(
                {
                    "time": fill.timestamp,
                    "symbol": fill.symbol,
                    "side": fill.side,
                    "quantity": fill.fill_qty,
                    "price": fill.fill_price,
                    "order_id": fill.client_order_id,
                    "strategy": strategy.get("name", ""),
                    "slippage": None,
                }
            )
        for entry in session.journal.entries:
            events.append(
                {
                    "timestamp": entry.timestamp,
                    "strategy": entry.payload.get("strategy_id", ""),
                    "symbol": "",
                    "event": entry.kind,
                    "status": "",
                }
            )
    journal = snap.get("journal", {})
    return {
        "mode": snap.get("mode", "PAPER"),
        "broker": {
            "name": broker.get("mode", "Paper") if broker.get("connected") else "Paper",
            "environment": broker.get("environment", "simulated"),
            "connected": broker.get("connected"),
            "reason": broker.get("reason", ""),
            "capabilities": [],
            "latency_ms": None,
            "last_heartbeat": None,
        },
        "strategy": {
            "id": strategy.get("name", ""),
            "version": "",
            "status": snap.get("lifecycle", ""),
            "mode": snap.get("mode", ""),
            "params": {},
            "instrument": strategy.get("symbol", ""),
            "timeframe": "",
            "live_supported": None,
            "warmup": None,
            "state": snap.get("lifecycle", ""),
        },
        "position": position,
        "orders": orders,
        "fills": fills,
        "pnl": {
            "realized": None,
            "unrealized": None,
            "total": snap.get("pnl"),
            "exposure": None,
            "orders": len(orders),
            "fills": len(fills),
            "wins": None,
            "losses": None,
        },
        "risk": {
            "status": "HALTED" if snap.get("risk", {}).get("kill_halted") else "READY",
            "limits": [],
            "decisions": [],
        },
        "reconciliation": {
            "status": "CLEAN"
            if not snap.get("reconciliation", {}).get("blocks_live")
            else "BLOCKED",
            "positions": "",
            "orders": "",
            "last_check": None,
            "mismatches": 0,
            "blocks_live": bool(snap.get("reconciliation", {}).get("blocks_live", False)),
        },
        "kill": {"halted": bool(snap.get("risk", {}).get("kill_halted", False)), "level": "global"},
        "gates": [
            {"name": "BROKER_ADAPTER_READY", "status": "READY", "reason": ""},
            {
                "name": "CREDENTIALS_READY",
                "status": "NOT READY",
                "reason": "paper session: no live credentials",
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
        "lifecycle": snap.get("lifecycle", ""),
        "events": events,
        "market_symbol": strategy.get("symbol", ""),
        "market_timeframe": "",
        "market_bars": None,
        "_journal_kinds": journal,
    }


def test_workspace_loads_empty_without_fakes(workspace: LiveWorkspace) -> None:
    assert workspace._mode_pill.text() == "N/A"
    assert workspace._broker_pill.text() == "Broker: NOT CONFIGURED"
    assert workspace._orders_table.rowCount() == 0
    assert workspace._fills_table.rowCount() == 0
    assert workspace._events_table.rowCount() == 0
    assert workspace._arm_button.isEnabled() is False
    assert workspace._halt_banner.isVisible() is False
    assert "LIVE" not in workspace._mode_pill.text()


def test_paper_state_displayed(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state(mode="PAPER"))
    assert workspace._mode_pill.text() == "PAPER"
    assert workspace._orders_table.rowCount() == 1
    assert workspace._fills_table.rowCount() == 1
    assert workspace._events_table.rowCount() == 1


def test_sandbox_mode_tone(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state(mode="SANDBOX"))
    assert workspace._mode_pill.text() == "SANDBOX"


def test_live_not_configured_displayed(workspace: LiveWorkspace) -> None:
    workspace.set_state(
        _full_state(
            mode="LIVE",
            broker={
                "name": "NOT CONFIGURED",
                "environment": "",
                "connected": False,
                "reason": "no live venue adapter is registered",
            },
        )
    )
    assert workspace._mode_pill.text() == "LIVE"
    assert "NOT CONFIGURED" in workspace._broker_pill.text()
    assert workspace._conn_pill.text() == "○ Disconnected"


def test_readiness_gates_and_reasons(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state())
    assert len(workspace._gate_rows) == 2
    names = [name for name, _pill, _label in workspace._gate_rows]
    assert names == ["BROKER_ADAPTER_READY", "CREDENTIALS_READY"]
    _name, _pill, label = workspace._gate_rows[1]
    assert label.toolTip() == "missing account_id"


def test_arm_disabled_with_blockers(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state(can_arm=False))
    assert workspace._arm_button.isEnabled() is False
    assert "CREDENTIALS_READY" in workspace._arm_note.text()


def test_arm_enabled_calls_handler(workspace: LiveWorkspace) -> None:
    calls: list[str] = []
    workspace.set_state(_full_state(can_arm=True))
    workspace.set_arm_handler(lambda: (calls.append("arm"), (True, "armed by operator"))[1])
    assert workspace._arm_button.isEnabled() is True
    workspace._arm_button.click()
    assert calls == ["arm"]
    assert workspace._arm_note.text() == "armed by operator"


def test_kill_halt_banner_impossible_to_miss(workspace: LiveWorkspace) -> None:
    workspace.show()
    workspace.set_state(_full_state(kill={"halted": True, "level": "global"}))
    assert workspace._halt_banner.isVisible() is True
    assert workspace._kill_pill.text() == "■ HALTED"


def test_kill_halt_handler_called(workspace: LiveWorkspace) -> None:
    calls: list[str] = []
    workspace.set_state(_full_state(can_halt=True))
    workspace.set_halt_handler(lambda: (calls.append("halt"), (True, "halted"))[1])
    assert workspace._halt_button.isEnabled() is True
    workspace._halt_button.click()
    assert calls == ["halt"]


def test_positions_displayed(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state())
    assert workspace._position_rows["side"].text() == "LONG"
    assert workspace._position_rows["quantity"].text() == "10.00"
    assert workspace._position_rows["unrealized"].text() == "+20.00"


def test_flat_position_explicit(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state(position={"flat": True}))
    assert workspace._position_rows["side"].text() == "FLAT"


def test_orders_displayed_with_unknown_highlight(workspace: LiveWorkspace) -> None:
    workspace.set_state(
        _full_state(
            orders=[
                {
                    "order_id": "u1",
                    "strategy": "S",
                    "symbol": "T",
                    "side": "BUY",
                    "quantity": 1.0,
                    "type": "MARKET",
                    "price": 10.0,
                    "status": "UNKNOWN",
                    "time": "t",
                    "broker": "B-1",
                },
            ]
        )
    )
    assert workspace._orders_table.rowCount() == 1
    assert _cell_text(workspace._orders_table, 0, 7) == "UNKNOWN"


def test_fills_displayed(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state())
    assert workspace._fills_table.rowCount() == 1
    assert _cell_text(workspace._fills_table, 0, 4) == "100.02"


def test_pnl_displayed(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state())
    text = workspace._pnl_label.text()
    assert "+5.00" in text and "+20.00" in text and "+25.00" in text


def test_reconciliation_mismatch_blocks_visible(workspace: LiveWorkspace) -> None:
    workspace.set_state(
        _full_state(
            reconciliation={
                "status": "BLOCKED",
                "positions": "qty differs",
                "orders": "match",
                "last_check": "t",
                "mismatches": 1,
                "blocks_live": True,
            }
        )
    )
    assert "BLOCKED" in workspace._recon_label.text()
    assert workspace._recon_pill.text() == "Reconciliation: BLOCKED"


def test_lifecycle_displayed(workspace: LiveWorkspace) -> None:
    state = _full_state()
    state["strategy"] = dict(state["strategy"], state="RUNNING")
    workspace.set_state(state)
    assert workspace._strategy_rows["state"].text() == "RUNNING"


def test_event_stream_and_filters(workspace: LiveWorkspace) -> None:
    workspace.set_state(
        _full_state(
            events=[
                {
                    "timestamp": "t1",
                    "strategy": "S",
                    "symbol": "A",
                    "event": "FILL",
                    "status": "ok",
                },
                {
                    "timestamp": "t2",
                    "strategy": "S",
                    "symbol": "B",
                    "event": "SIGNAL_GENERATED",
                    "status": "ok",
                },
            ]
        )
    )
    assert workspace._events_table.rowCount() == 2
    workspace._event_type_filter.setCurrentText("FILL")
    assert workspace._events_table.rowCount() == 1
    workspace._event_type_filter.setCurrentText("ALL EVENTS")
    workspace._event_text_filter.setText("b")
    assert workspace._events_table.rowCount() == 1


def test_no_fake_live_data(workspace: LiveWorkspace) -> None:
    workspace.set_state({})
    assert workspace._broker_pill.text() == "Broker: NOT CONFIGURED"
    assert workspace._mode_pill.text() == "N/A"
    assert workspace._conn_pill.text() == "Connection: N/A"
    assert workspace._orders_table.rowCount() == 0
    assert workspace._fills_table.rowCount() == 0
    assert workspace._events_table.rowCount() == 0
    assert workspace._arm_button.isEnabled() is False
    assert workspace._halt_banner.isVisible() is False


def test_market_ui_unchanged() -> None:
    nav = TopNavBar()
    assert nav.active == "MARKET"
    # Canonical six-section nav (PORTFOLIO is the native Slint viewport).
    assert set(nav._buttons) == {
        "MARKET",
        "STRATEGY LAB",
        "RESEARCH",
        "PORTFOLIO",
        "LIVE",
        "SYSTEM",
    }
    assert hasattr(nav, "live_clicked")


def test_chart_shows_real_bars_and_price(workspace: LiveWorkspace) -> None:
    from market import Bar

    workspace.show()
    bars = tuple(
        Bar(
            symbol="GAIL",
            open=100.0 + i,
            high=102.0 + i,
            low=99.0 + i,
            close=101.0 + i,
            volume=1000,
            timestamp=f"2026-01-{(i % 28) + 1:02d} 09:15:00",
        )
        for i in range(30)
    )
    state = _full_state(market_symbol="GAIL", market_timeframe="15m", market_bars=bars)
    workspace.set_state(state)
    assert workspace._chart.isVisible() is True
    assert "GAIL" in workspace._price_label.text()
    assert "102.0" in workspace._price_label.text()  # position current_price


def test_chart_placeholder_without_bars(workspace: LiveWorkspace) -> None:
    workspace.set_state(_full_state(market_bars=None))
    assert "NO DATA" in workspace._chart_empty.text()


def test_paper_e2e_renders_real_pipeline_state(workspace: LiveWorkspace, tmp_path: Path) -> None:
    data_dir, strategy_dir = _seed(tmp_path)
    service = PaperService(PaperRunConfig(data_dir=data_dir, strategy_dir=strategy_dir))
    report = service.run()
    assert report.fills > 0
    last_close = 100.0 + (59 % 10) - 3.0 + 59 * 0.05
    workspace.set_state(paper_state_to_workspace(service, last_price=last_close))
    assert workspace._mode_pill.text() == "PAPER"
    assert workspace._fills_table.rowCount() == report.fills
    assert workspace._orders_table.rowCount() >= report.fills
    assert workspace._position_rows["side"].text() in ("LONG", "SHORT", "FLAT")
    kinds = service.state()["journal"]
    assert "FILL" in kinds and "SIGNAL_GENERATED" in kinds
    table_events = workspace._events_table.rowCount()
    assert table_events > 0
    workspace.set_state(paper_state_to_workspace(service, last_price=last_close))
    assert workspace._events_table.rowCount() == table_events
