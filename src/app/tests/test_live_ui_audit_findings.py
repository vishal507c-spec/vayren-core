"""Regression tests for Live UI Audit Findings in Python backend."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import app.headless as headless  # noqa: E402
from app.services.live_trading_service import LiveTradingService  # noqa: E402
from execution.engine import ExecutionEngine  # noqa: E402
from execution.models.order import BrokerOrder  # noqa: E402
from execution.models.order_state import OrderState  # noqa: E402


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    return str(tmp_path), str(tmp_path)


def _act(dirs: tuple[str, str], action: dict) -> dict:
    return headless._live_action(dirs[0], dirs[1], action)


def _service(dirs: tuple[str, str]) -> LiveTradingService:
    service = headless._live_service(dirs[0], dirs[1])
    assert service is not None
    return service


def test_finding_3_strategy_deselection_clears_strategy_and_symbols(dirs: tuple[str, str]) -> None:
    svc = _service(dirs)
    # Save universe for the strategy in the store
    svc._universes.save("OBR C1C4", ["NSE:RELIANCE"])

    # 1. Setup a valid strategy
    _act(
        dirs,
        {
            "action": "setup",
            "strategy_name": "OBR C1C4",
            "symbols": ["NSE:RELIANCE"],
            "timeframe": "30m",
            "quantity": 10.0,
        },
    )
    assert svc._config.strategy_name == "OBR C1C4"
    assert "NSE:RELIANCE" in svc._config.symbols

    # 2. Deselect strategy by sending empty strategy_name and empty symbols
    snap_deselected = _act(
        dirs,
        {
            "action": "setup",
            "strategy_name": "",
            "symbols": [],
            "timeframe": "30m",
            "quantity": 10.0,
        },
    )
    assert svc._config.strategy_name == ""
    assert len(svc._config.symbols) == 0
    assert snap_deselected["action_note"] == "setup updated — readiness re-evaluated"


def test_finding_4_rejected_orders_reflected_in_universe_quotes(dirs: tuple[str, str]) -> None:
    svc = _service(dirs)
    engine = ExecutionEngine()
    order = BrokerOrder(
        client_order_id="ORD-REJ-1",
        intent_id="INT-1",
        symbol="NSE:SBIN-EQ",
        side="BUY",
        quantity=10.0,
        order_type="MARKET",
        limit_price=None,
        state=OrderState.CREATED,
    )
    created = engine.create(order)
    engine.transition(created.client_order_id, OrderState.REJECTED, reason="Margin insufficient")

    # Mock an active session for the symbol with this engine
    mock_session = SimpleNamespace(
        engine=engine,
        ledger=SimpleNamespace(
            position=lambda _s: SimpleNamespace(flat=True, quantity=0, avg_price=0.0)
        ),
    )
    svc._sessions["NSE:SBIN-EQ"] = mock_session  # type: ignore[assignment]

    quotes = svc._universe_quotes(("NSE:SBIN-EQ",))
    assert len(quotes) == 1
    assert quotes[0]["order"] == "REJECTED"
    assert quotes[0]["watchlist_status"] == "REJECTED"
