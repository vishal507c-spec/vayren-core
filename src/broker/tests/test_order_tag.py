"""Venue-safe FYERS order tags: internal client ids never change."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pytest

import broker.order_tag as order_tag_module
from broker.order_tag import BrokerTagRegistry, from_broker_tag, to_broker_tag
from broker.providers.fyers.session_adapter import FyersSessionAdapter

CID = "obr-c1c4:1.0:4:1:o1"
FORMATS = (
    CID,
    "sl:NSE:SBIN-EQ:1:o1",
    "OBR C1C4:1:1:1:o1",
    "t:re:o1",
    "strategy:intent_1:o1",
    "otest:buy:SBIN:20261118",
    "210f8c1e4a5b4c6d8e0f1a2b3c4d5e6f",
)


@dataclass
class _Plan:
    symbol: str = "NSE:IFCI-EQ"
    side: str = "SELL"
    quantity: float = 1.0
    order_type: str = "LIMIT"
    limit_price: float = 60.21
    trigger_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "DAY"
    product: str = "CNC"
    bracket: tuple[dict[str, object], ...] = ()


class _TagGateTransport:
    """400 on non-alphanumeric tags; echoes accepted tags as ``1:<tag>``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []

    def _profile(self) -> dict[str, Any]:
        return {"s": "ok", "code": 200, "data": {"fy_id": "XY12345", "name": "Test User"}}

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = headers
        self.calls.append({"url": url, "payload": payload})
        if not url.endswith("/orders/sync"):
            return self._profile()
        tag = str(payload.get("orderTag") or "")
        if re.fullmatch(r"[A-Za-z0-9]{1,30}", tag) is None:
            return {
                "s": "error",
                "code": -1,
                "message": "orderTag: Only alphanumeric characters allowed (HTTP 400)",
            }
        broker_id = f"FY{len(self.orders) + 1}"
        self.orders.append(
            {
                "id": broker_id,
                "orderTag": f"1:{tag}",
                "status": 2,
                "qty": payload.get("qty", 1),
                "filledQty": payload.get("qty", 1),
                "remainingQuantity": 0,
                "tradedPrice": payload.get("limitPrice", 0.0),
                "symbol": payload.get("symbol", ""),
                "side": payload.get("side", -1),
                "message": "",
                "orderDateTime": "18-Nov-2026 11:45:00",
            }
        )
        return {"s": "ok", "code": 1101, "message": "ok", "id": broker_id}

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _ = headers
        if url.endswith("/orders"):
            return {"s": "ok", "code": 200, "orderBook": self.orders}
        return self._profile()

    def put_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        return {"s": "ok", "code": 1102, "message": "ok"}

    def delete_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _ = (url, payload, headers)
        return {"s": "ok", "code": 1103, "message": "ok"}


@pytest.fixture
def tag_map(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    path = tmp_path / "broker_tag_map.json"
    monkeypatch.setattr(order_tag_module, "_TAG_MAP_STORE", BrokerTagRegistry(path))
    return path


def _connected_adapter(transport: _TagGateTransport) -> FyersSessionAdapter:
    adapter = FyersSessionAdapter("APP", "TOKEN", transport=transport, enable_order_ws=False)
    adapter.connect()
    return adapter


def test_real_formats_pass_regex_and_are_unique() -> None:
    tags = [to_broker_tag(cid) for cid in FORMATS]
    assert all(re.fullmatch(r"[A-Za-z0-9]{1,30}", tag) for tag in tags)
    assert all(len(tag) <= 20 for tag in tags)
    assert len(set(tags)) == len(tags)
    assert all(tag != cid and tag != "Untagged" for cid, tag in zip(FORMATS, tags, strict=True))
    assert to_broker_tag(CID) == to_broker_tag(CID)


@pytest.mark.usefixtures("tag_map")
def test_tag_accepted_and_raw_id_rejected() -> None:
    transport = _TagGateTransport()
    adapter = _connected_adapter(transport)
    broker_id = adapter.place_order(_Plan(), client_order_id=CID)
    assert broker_id == "FY1"
    post = [c for c in transport.calls if c["url"].endswith("/orders/sync")][-1]
    tag = str(post["payload"]["orderTag"])
    assert re.fullmatch(r"[A-Za-z0-9]{1,30}", tag) and len(tag) <= 20
    # The same mock venue rejects the untranslated internal id.
    rejected = transport.post_json("https://api/orders/sync", {"orderTag": CID})
    assert rejected["s"] == "error" and "alphanumeric" in rejected["message"]


@pytest.mark.usefixtures("tag_map")
def test_order_book_and_ws_fill_match_internal_id() -> None:
    transport = _TagGateTransport()
    adapter = _connected_adapter(transport)
    adapter.place_order(_Plan(), client_order_id=CID)
    tag = str(transport.orders[0]["orderTag"]).removeprefix("1:")

    rows = adapter._read_orders()
    assert rows and rows[0]["client_order_id"] == CID

    events = adapter._parse_ws_order_event(
        {
            "id": "FY1",
            "orderTag": tag,
            "status": 2,
            "filledQty": 1,
            "tradedPrice": 60.21,
            "symbol": "NSE:IFCI-EQ",
            "side": -1,
        }
    )
    fills = [event for event in events if event["type"] == "fill"]
    assert fills and fills[0]["client_order_id"] == CID
    assert fills[0]["fill"].client_order_id == CID


def test_restart_mapping_decodes_tag(tag_map: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _TagGateTransport()
    first = _connected_adapter(transport)
    first.place_order(_Plan(), client_order_id=CID)
    tag = str(transport.orders[0]["orderTag"]).removeprefix("1:")
    assert tag_map.exists()

    # Fresh process: empty in-memory state, map reloaded from disk.
    monkeypatch.setattr(order_tag_module, "_TAG_MAP_STORE", BrokerTagRegistry(tag_map))
    restarted = _connected_adapter(_TagGateTransport())
    assert restarted._tag_candidates() == ()
    events = restarted._parse_ws_order_event(
        {"id": "FY1", "orderTag": f"1:{tag}", "status": 2, "filledQty": 1}
    )
    assert any(event["client_order_id"] == CID for event in events)


def test_missing_map_file_falls_back_to_deterministic_hash(
    tag_map: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _TagGateTransport()
    adapter = _connected_adapter(transport)
    adapter.place_order(_Plan(), client_order_id=CID)
    tag = str(transport.orders[0]["orderTag"]).removeprefix("1:")
    tag_map.unlink()
    monkeypatch.setattr(order_tag_module, "_TAG_MAP_STORE", BrokerTagRegistry(tag_map))

    assert from_broker_tag(f"1:{tag}", candidates=[CID]) == CID
    events = adapter._parse_ws_order_event(
        {"id": "FY1", "orderTag": f"1:{tag}", "status": 2, "filledQty": 1}
    )
    assert any(event["client_order_id"] == CID for event in events)


def test_registry_persists_across_instances(tmp_path: Any) -> None:
    path = tmp_path / "map.json"
    tag = to_broker_tag(CID)
    BrokerTagRegistry(path).remember(tag, CID)
    assert BrokerTagRegistry(path).resolve(tag) == CID
