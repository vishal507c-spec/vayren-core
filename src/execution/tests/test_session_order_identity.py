"""Session-aware order identity: watchlist scoping + cross-session intent ids."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.live_trading_service import _current_session_order
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from execution.engine import ExecutionEngine, IllegalTransitionError
from execution.models.intent import make_intent_id, make_session_intent_id
from execution.models.order import BrokerOrder, OrderState


def _restored_order(
    *,
    cid: str,
    intent: str,
    state: str,
    session_date: str,
    symbol: str = "NSE:IFCI",
) -> dict:
    return {
        "client_order_id": cid,
        "intent_id": intent,
        "symbol": symbol,
        "side": "SELL",
        "quantity": 1.0,
        "order_type": "LIMIT",
        "state": state,
        "filled_qty": 0.0,
        "avg_fill_price": None,
        "broker_order_id": None,
        "reason": "venue rejected the request: orderTag: Only alphanumeric characters allowed",
        "history": [["REJECTED", "1970-01-01T00:00:00+00:00"]],
        "session_date": session_date,
    }


def test_previous_day_rejected_order_is_not_todays_watchlist_status() -> None:
    engine = ExecutionEngine()
    engine.restore(
        {
            "orders": [
                _restored_order(
                    cid="obr-c1c4:1.0:4:1:o1",
                    intent="obr-c1c4:1.0:4:1",
                    state="REJECTED",
                    session_date="2026-11-18",
                )
            ]
        }
    )

    assert _current_session_order(engine, "NSE:IFCI", "2026-11-19") is None
    assert _current_session_order(engine, "NSE:IFCI", "2026-11-18") is not None


def test_historical_rejected_order_stays_in_order_history() -> None:
    engine = ExecutionEngine()
    engine.restore(
        {
            "orders": [
                _restored_order(
                    cid="old:o1", intent="old", state="REJECTED", session_date="2026-11-18"
                )
            ]
        }
    )
    snap = engine.snapshot()
    assert len(snap["orders"]) == 1
    assert snap["orders"][0]["state"] == "REJECTED"
    assert snap["orders"][0]["session_date"] == "2026-11-18"
    assert snap["orders"][0]["history"][0][1] == "1970-01-01T00:00:00+00:00"


def test_history_time_uses_session_date_for_legacy_epoch() -> None:
    from app.services.live_trading_service import LiveTradingService

    legacy = _restored_order(
        cid="old:o1", intent="old", state="REJECTED", session_date="2026-11-18"
    )
    assert LiveTradingService._history_time(legacy) == "2026-11-18"

    current = dict(legacy)
    current["history"] = [["REJECTED", "2026-11-19T06:15:03+00:00"]]
    assert LiveTradingService._history_time(current) == "2026-11-19T06:15:03+00:00"


def test_session_intent_ids_differ_across_dates_and_stay_stable_within() -> None:
    args = ("obr-c1c4", "1.0", 4, 1)
    yesterday = make_session_intent_id(*args[:2], "2026-11-18", *args[2:])
    today = make_session_intent_id(*args[:2], "2026-11-19", *args[2:])

    assert yesterday != today
    assert today == make_session_intent_id("obr-c1c4", "1.0", "2026-11-19", 4, 1)
    assert today.endswith(":20261119")
    # First segment stays the strategy id (Rust + Python strategy_of_intent).
    assert today.split(":")[0] == "obr-c1c4"
    # Legacy ids remain byte-identical for restored orders.
    assert make_session_intent_id("obr-c1c4", "1.0", "", 4, 1) == make_intent_id(*args)


def test_duplicate_order_within_same_session_still_blocked() -> None:
    intent_id = make_session_intent_id("obr-c1c4", "1.0", "2026-11-19", 4, 1)
    order = BrokerOrder(
        client_order_id=f"{intent_id}:o1",
        intent_id=intent_id,
        symbol="NSE:IFCI",
        side="SELL",
        quantity=1.0,
        session_date="2026-11-19",
    )
    engine = ExecutionEngine()
    engine.create(order)
    with pytest.raises(IllegalTransitionError, match="duplicate client order id"):
        engine.create(order)


def test_unknown_orders_remain_for_reconciliation_after_restore() -> None:
    cid = "unknown:o1"
    engine = ExecutionEngine()
    engine.restore(
        {
            "orders": [
                {
                    "client_order_id": cid,
                    "intent_id": "unknown",
                    "symbol": "NSE:IFCI",
                    "side": "SELL",
                    "quantity": 1.0,
                    "order_type": "LIMIT",
                    "state": "UNKNOWN",
                    "history": [["UNKNOWN", "2026-11-19T06:00:00+00:00"]],
                    "session_date": "2026-11-19",
                }
            ]
        }
    )
    restored = engine.get(cid)
    assert restored is not None
    assert restored.state == OrderState.UNKNOWN
    assert _current_session_order(engine, "NSE:IFCI", "2026-11-19") is not None
    with pytest.raises(IllegalTransitionError, match="UNKNOWN exits only via reconcile"):
        engine.transition(cid, OrderState.FILLED)


def test_protective_stop_id_format_unchanged() -> None:
    adapter = FyersSessionAdapter("APP", "TOKEN", enable_order_ws=False)
    plan = SimpleNamespace(
        intent_id="sl:NSE:IFCI:1",
        symbol="NSE:IFCI",
        order_type="STOP_MARKET",
        stop_price=60.9,
    )
    assert adapter._is_protective_exit(plan, "sl:NSE:IFCI:1:o1") is True

    strategy_intent = make_session_intent_id("obr-c1c4", "1.0", "2026-11-19", 4, 1)
    entry_plan = SimpleNamespace(
        intent_id=strategy_intent,
        symbol="NSE:IFCI",
        order_type="LIMIT",
        stop_price=None,
    )
    assert adapter._is_protective_exit(entry_plan, f"{strategy_intent}:o1") is False


def test_checkpoint_roundtrip_preserves_both_sessions_and_restore_state() -> None:
    engine = ExecutionEngine()
    engine.restore(
        {
            "orders": [
                _restored_order(
                    cid="old:o1", intent="old", state="REJECTED", session_date="2026-11-18"
                ),
                {
                    "client_order_id": "current:o1",
                    "intent_id": "current",
                    "symbol": "NSE:IFCI",
                    "side": "SELL",
                    "quantity": 1.0,
                    "order_type": "LIMIT",
                    "state": "UNKNOWN",
                    "history": [["UNKNOWN", "2026-11-19T06:00:00+00:00"]],
                    "session_date": "2026-11-19",
                },
            ]
        }
    )
    fresh = ExecutionEngine()
    fresh.restore(engine.snapshot())

    assert len(fresh.snapshot()["orders"]) == 2
    current = _current_session_order(fresh, "NSE:IFCI", "2026-11-19")
    historical = _current_session_order(fresh, "NSE:IFCI", "2026-11-18")
    assert current is not None and current.client_order_id == "current:o1"
    assert historical is not None and historical.state == OrderState.REJECTED
