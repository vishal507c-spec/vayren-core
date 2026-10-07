"""Snapshot tests: broker-neutral projection, no fabrication, no secrets."""

import json
from typing import Any

from remote.snapshot import (
    FORBIDDEN_VALUE_MARKERS,
    build_snapshot,
    classify_event,
    project_event,
    scrub,
)


def make_live(**overrides: Any) -> dict[str, Any]:
    """Minimal live-service-shaped snapshot (backend's own keys)."""
    live: dict[str, Any] = {
        "mode": "PAPER",
        "session_status": "STOPPED",
        "status_reason": "",
        "as_of": "2026-10-06T00:00:00+00:00",
        "websocket": {"status": "DISCONNECTED"},
        "market_data": {"status": "STOPPED", "exchange": "NSE Cash", "timeframe": "15m"},
        "feed": "none",
        "risk_engine": {
            "effective_capital": 400000.0,
            "max_allowed_risk": 600.0,
            "broker_capital": None,
            "sizing_status": "NOT READY",
            "sizing_reason": "broker capital unavailable",
        },
        "broker": {
            "name": "FYERS",
            "connected": False,
            "reason": "not started",
            "account_id": "",
            "status": "",
        },
        "positions": [],
        "orders": [],
        "fills": [],
        "reconciliation": {"status": "NOT CONFIGURED", "blocks_live": False},
        "kill": {"halted": False},
        "execution": {"ready": False, "reason": "session not running"},
        "capital": {"source": "configured", "available_margin": None},
        "start_blockers": ["no strategy selected"],
        "events": [],
    }
    live.update(overrides)
    return live


def test_snapshot_sections_present() -> None:
    snap = build_snapshot(make_live())
    for section in (
        "broker",
        "market",
        "order_stream",
        "strategy",
        "risk",
        "capital",
        "orders",
        "positions",
        "fills",
        "stops",
        "reconciliation",
        "system",
        "blockers",
    ):
        assert section in snap, section
    assert snap["broker"]["status"] == "DISCONNECTED"
    assert snap["strategy"]["status"] == "BLOCKED"
    assert snap["risk"]["status"] == "NOT READY"
    assert snap["stops"]["status"] == "NONE"
    assert snap["capital"]["capital_source"] == "configured"


def test_running_connected_snapshot() -> None:
    snap = build_snapshot(
        make_live(
            session_status="RUNNING",
            feed="live",
            start_blockers=[],
            broker={
                "name": "FYERS",
                "connected": True,
                "reason": "FYERS connected",
                "account_id": "ACC1",
                "status": "CONNECTED",
            },
            risk_engine={
                "effective_capital": 400000.0,
                "max_allowed_risk": 600.0,
                "broker_capital": 100000.0,
                "sizing_status": "READY",
                "sizing_reason": "ready",
                "status": "READY",
            },
            capital={"source": "broker", "available_margin": 100000.0},
            positions=[
                {
                    "symbol": "NSE:AAA",
                    "side": "LONG",
                    "quantity": 10,
                    "entry_price": 100.0,
                    "current_price": 101.0,
                    "pnl": 10.0,
                    "pnl_pct": 0.1,
                    "status": "OPEN",
                    "entry_time": "t",
                }
            ],
            orders=[
                {
                    "order_id": "c1",
                    "symbol": "NSE:AAA",
                    "side": "SELL",
                    "quantity": 10.0,
                    "type": "STOP_MARKET",
                    "price": 0.0,
                    "status": "WORKING",
                    "time": "t",
                    "broker": "fyers-live",
                }
            ],
            reconciliation={"status": "SYNCED", "blocks_live": False},
            execution={"ready": True, "reason": ""},
        )
    )
    assert snap["broker"]["status"] == "CONNECTED"
    assert snap["order_stream"]["status"] == "CONNECTED"
    assert snap["strategy"]["status"] == "RUNNING"
    assert snap["risk"]["status"] == "READY"
    assert snap["capital"]["available"] == 100000.0
    assert snap["capital"]["max_risk"] == 600.0
    assert snap["stops"]["status"] == "PROTECTED"
    assert snap["reconciliation"]["status"] == "SYNCED"
    assert len(snap["positions"]) == 1 and len(snap["orders"]) == 1


def test_kill_halted_running_maps_to_error() -> None:
    snap = build_snapshot(make_live(session_status="RUNNING", kill={"halted": True}))
    assert snap["strategy"]["status"] == "ERROR"


def test_reconciliation_block_maps_to_required() -> None:
    snap = build_snapshot(make_live(reconciliation={"status": "MISMATCH", "blocks_live": True}))
    assert snap["reconciliation"]["status"] == "REQUIRED"


def test_position_without_stop_is_at_risk() -> None:
    snap = build_snapshot(
        make_live(
            positions=[{"symbol": "NSE:AAA", "status": "OPEN"}],
            orders=[{"order_id": "c1", "status": "FILLED", "type": "MARKET"}],
        )
    )
    assert snap["stops"]["status"] == "AT RISK"


def test_scrub_drops_credential_like_keys() -> None:
    dirty = {
        "name": "FYERS",
        "api_secret": "shh",
        "nested": {"access_token": "shh", "ok": 1},
        "items": [{"totp_seed": "shh", "qty": 2}],
    }
    clean = scrub(dirty)
    assert clean == {"name": "FYERS", "nested": {"ok": 1}, "items": [{"qty": 2}]}


def test_no_vendor_or_secret_markers_on_wire() -> None:
    snap = build_snapshot(make_live())
    raw = json.dumps(snap).lower()
    for marker in FORBIDDEN_VALUE_MARKERS:
        assert marker not in raw
    for fragment in ("secret", "token", "totp", "password", "api_key"):
        assert fragment not in raw


def test_non_dict_snapshot_is_honest() -> None:
    assert build_snapshot([]) == {"error": "backend snapshot unavailable"}  # type: ignore[arg-type]


def test_event_classification_table() -> None:
    cases = [
        ({"event": "SELL SIGNAL GENERATED · s1", "category": "STRATEGY"}, "SIGNAL_GENERATED"),
        ({"event": "RISK VALIDATED · Qty 22", "category": "RISK"}, "RISK_VALIDATED"),
        ({"event": "RISK BLOCKED · reasons", "category": "RISK"}, "RISK_BLOCKED"),
        ({"event": "ORDER SUBMITTED · c1", "category": "ORDERS"}, "ORDER_SENT"),
        ({"event": "ORDER ACKNOWLEDGED BY BROKER · c1", "category": "ORDERS"}, "BROKER_ACK"),
        ({"event": "FILLED · 10 @ 100.5", "category": "ORDERS"}, "FILL"),
        ({"event": "REJECTED · c1: reason", "category": "ORDERS"}, "REJECTION"),
        ({"event": "RECONCILED", "category": "SYSTEM"}, "RECONCILIATION"),
        ({"event": "broker connected — FYERS", "category": "BROKER"}, "SYSTEM"),
        ({"event": "something entirely new", "category": "ORDERS"}, "ORDER_WORKING"),
        ({"event": "something entirely new", "category": "NOPE"}, "SYSTEM"),
    ]
    for entry, expected in cases:
        assert classify_event(entry) == expected, entry


def test_partial_fill_detected() -> None:
    assert (
        classify_event({"event": "PARTIAL FILL · 5 @ 100", "category": "ORDERS"}) == "PARTIAL_FILL"
    )


def test_project_event_shape_is_scrubbed() -> None:
    projected = project_event(
        {
            "timestamp": "t",
            "strategy": "OBR",
            "symbol": "AAA",
            "event": "FILLED · 1 @ 2",
            "status": "ok",
            "token": "shh",
        }
    )
    assert projected["name"] == "FILL"
    assert "token" not in projected
