"""Focused regression tests for the FYERS order-status consistency.

The venue's order-status table is authoritative and shared by every surface
(order socket, REST order book, duplicate check, cancellation evidence):

    1 Cancelled · 2 Traded/Filled · 3 (unused) · 4 Transit · 5 Rejected ·
    6 PENDING (the venue still works it) · 7 Expired

Status 6 used to be read as "cancelled" on the REST path, which made a live
pending order look finished: it slipped past the duplicate-order check and
produced a cancellation that never happened.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import pytest

from broker.providers.fyers.session_adapter import FyersSessionAdapter


@dataclass
class _Plan:
    intent_id: str = "strat:1:i1"
    symbol: str = "SBIN"
    side: str = "BUY"
    quantity: float = 1.0
    order_type: str = "MARKET"
    limit_price: float | None = None
    trigger_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "DAY"
    product: str = "INTRADAY"
    bracket: tuple[dict[str, object], ...] = ()


class _BookTransport:
    """Serves one order book and records what the adapter sent."""

    def __init__(self, order_book: list[dict[str, Any]]) -> None:
        self.order_book = order_book
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def _record(self, method: str, url: str) -> None:
        with self._lock:
            self.calls.append((method, url))

    def count(self, method: str) -> int:
        with self._lock:
            return sum(1 for called, _ in self.calls if called == method)

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        self._record("GET", url)
        if url.endswith("/orders"):
            return {"s": "ok", "code": 200, "orderBook": list(self.order_book)}
        if url.endswith("/profile"):
            return {"s": "ok", "code": 200, "data": {"fy_id": "XV29244"}}
        if url.endswith("/positions"):
            return {"s": "ok", "code": 200, "netPositions": []}
        raise AssertionError(f"unexpected GET: {url}")

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        self._record("POST", url)
        return {"s": "ok", "code": 1101, "message": "Order submitted successfully", "id": "NEW-1"}

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        self._record("PUT", url)
        return {"s": "ok"}

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        self._record("DELETE", url)
        return {"s": "ok"}


def _adapter(book: list[dict[str, Any]]) -> tuple[FyersSessionAdapter, _BookTransport]:
    transport = _BookTransport(book)
    adapter = FyersSessionAdapter(
        app_id="APP",
        access_token="TOKEN",
        transport=transport,
        enable_order_ws=False,
    )
    adapter._connected = True
    return adapter, transport


def _row(status: int, tag: str = "1:sl:NSE:SBIN-EQ:1:o1", order_id: str = "VENUE-1") -> dict:
    return {
        "id": order_id,
        "orderTag": tag,
        "status": status,
        "symbol": "NSE:SBIN-EQ",
        "side": -1,
        "qty": 10,
        "remainingQuantity": 10,
        "filledQty": 0,
        "tradedPrice": 0.0,
        "orderDateTime": "20-Oct-2026 10:00:00",
    }


# ── pending / open ──────────────────────────────────────────────────


def test_pending_order_is_open_and_never_a_cancellation() -> None:
    adapter, _ = _adapter([_row(6)])

    rows = adapter.open_orders()
    assert len(rows) == 1
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["client_order_id"] == "sl:NSE:SBIN-EQ:1:o1"
    assert rows[0]["broker_order_id"] == "VENUE-1"

    # The order socket agrees: a pending order is an acknowledgement.
    adapter._on_ws_orders({"orders": _row(6)})
    events = adapter.stream_events()
    assert [event["type"] for event in events] == ["ack"]


def test_transit_order_is_open() -> None:
    adapter, _ = _adapter([_row(4)])

    rows = adapter.open_orders()
    assert [row["status"] for row in rows] == ["OPEN"]


# ── cancelled / filled / expired / rejected ──────────────────────────


def test_cancelled_order_is_cancelled() -> None:
    adapter, _ = _adapter([_row(1)])

    assert adapter.open_orders() == []

    adapter._on_ws_orders({"orders": _row(1)})
    events = adapter.stream_events()
    assert [event["type"] for event in events] == ["cancel"]


def test_filled_order_is_filled_and_not_partial() -> None:
    adapter, _ = _adapter([_row(2)])

    assert adapter.open_orders() == []

    filled_row = _row(2)
    filled_row["filledQty"] = 10
    adapter._on_ws_orders({"orders": filled_row})
    events = adapter.stream_events()
    assert [event["type"] for event in events] == ["fill"]
    assert events[0]["fill"].partial is False


def test_rejected_order_is_rejected() -> None:
    adapter, _ = _adapter([_row(5)])

    assert adapter.open_orders() == []

    adapter._on_ws_orders({"orders": _row(5)})
    events = adapter.stream_events()
    assert [event["type"] for event in events] == ["reject"]


def test_expired_order_is_terminal() -> None:
    adapter, _ = _adapter([_row(7)])

    assert adapter.open_orders() == []

    adapter._on_ws_orders({"orders": _row(7)})
    events = adapter.stream_events()
    assert [event["type"] for event in events] == ["expired"]


# ── REST order rows are normalized ───────────────────────────────────


def test_rest_order_rows_carry_client_and_broker_identity() -> None:
    adapter, _ = _adapter([_row(6), _row(1, tag="1:strat:1:i1:o1", order_id="VENUE-2")])

    positions, orders = adapter.reconciliation_snapshot()
    assert positions == []
    assert len(orders) == 2

    first, second = orders
    assert first["client_order_id"] == "sl:NSE:SBIN-EQ:1:o1"
    assert first["broker_order_id"] == "VENUE-1"
    assert first["status"] == "OPEN"
    assert first["venue_status"] == 6
    assert first["filled_qty"] == 0.0
    assert second["client_order_id"] == "strat:1:i1:o1"
    assert second["status"] == "CANCELLED"


def test_venue_order_tag_prefix_is_stripped() -> None:
    adapter, _ = _adapter([_row(6, tag="1:strat:1:i1:o1")])

    rows = adapter.open_orders()
    assert rows[0]["client_order_id"] == "strat:1:i1:o1"


# ── duplicate-order check ────────────────────────────────────────────


def test_duplicate_check_adopts_a_pending_order() -> None:
    """A pending order is still worked by the venue, so it must be found.

    Reading status 6 as terminal made the duplicate check miss a live order
    and allowed a second order for the same client order id.
    """
    adapter, transport = _adapter([_row(6, tag="1:strat:1:i1:o1", order_id="VENUE-9")])

    broker_id = adapter.place_order(_Plan(), client_order_id="strat:1:i1:o1")

    assert broker_id == "VENUE-9"
    assert transport.count("POST") == 0


def test_duplicate_check_ignores_a_cancelled_order() -> None:
    adapter, transport = _adapter([_row(1, tag="1:strat:1:i1:o1", order_id="VENUE-9")])

    broker_id = adapter.place_order(_Plan(), client_order_id="strat:1:i1:o1")

    assert broker_id == "NEW-1"
    assert transport.count("POST") == 1


# ── cancellation evidence ────────────────────────────────────────────


def test_order_state_reports_the_venues_status() -> None:
    adapter, _ = _adapter([_row(6, tag="1:sl:NSE:SBIN-EQ:1:o1", order_id="VENUE-1")])

    state = adapter.order_state("VENUE-1")
    assert state["status"] == "OPEN"
    assert state["listed"] is True
    assert state["client_order_id"] == "sl:NSE:SBIN-EQ:1:o1"

    unknown = adapter.order_state("VENUE-NOT-IN-BOOK")
    assert unknown["status"] == "UNKNOWN"
    assert unknown["listed"] is False


def test_order_state_reports_a_cancelled_order_as_cancelled() -> None:
    adapter, _ = _adapter([_row(1, tag="1:sl:NSE:SBIN-EQ:1:o1", order_id="VENUE-1")])

    state = adapter.order_state("VENUE-1")
    assert state["status"] == "CANCELLED"
    assert state["listed"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, "CANCELLED"),
        (2, "FILLED"),
        (4, "OPEN"),
        (5, "REJECTED"),
        (6, "OPEN"),
        (7, "EXPIRED"),
        (3, "UNKNOWN"),
        ("CANCELLED", "CANCELLED"),
        ("PENDING", "OPEN"),
        ("", "UNKNOWN"),
    ],
)
def test_status_table_is_shared_by_every_surface(raw: Any, expected: str) -> None:
    from broker.providers.fyers.session_adapter import _order_status

    assert _order_status(raw) == expected
