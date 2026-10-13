"""Candle backfill: it must store venue data or say plainly that it did not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.candle_sync_service import sync_candles
from data.candle_store import latest_candle


class FakeAdapter:
    """A venue that answers `history` with fixed, real-shaped rows."""

    def __init__(self, rows: dict[str, list[tuple]], fail: set[str] | None = None) -> None:
        self.rows = rows
        self.fail = fail or set()
        self.calls: list[tuple[str, str, int, int]] = []

    def history(self, symbol, resolution, start, end):  # noqa: ANN001, ANN201
        self.calls.append((symbol, str(resolution), int(start), int(end)))
        if symbol in self.fail:
            raise RuntimeError("venue said no")
        return list(self.rows.get(symbol, []))


class FakeManager:
    def __init__(self, adapter: Any | None, active: str = "fyers") -> None:
        self.adapter = adapter
        self.active = active

    def snapshot(self) -> dict[str, Any]:
        return {"active_broker": self.active}

    def read_only_adapter(self, broker_id: str) -> Any | None:  # noqa: ARG002
        return self.adapter


def _rows(close: float = 100.0) -> list[tuple]:
    base = 1_790_000_000
    return [
        (base, close - 1.0, close + 2.0, close - 2.0, close, 10),
        (base + 1800, close - 1.0, close + 3.0, close - 2.0, close + 1.0, 20),
    ]


def test_stores_real_venue_rows(tmp_path: Path) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()})
    report = sync_candles(
        str(tmp_path), ["NSE:KAYNES"], FakeManager(adapter), timeframe="30m", days=30
    )

    assert report["detail"].startswith("1/1")
    assert report["written_total"] == 2
    row = report["results"][0]
    assert row["status"] == "STORED"
    assert row["written"] == 2
    assert row["symbol"] == "KAYNES"
    assert adapter.calls[0][0] == "KAYNES"
    assert adapter.calls[0][1] == "30"

    latest = latest_candle(tmp_path, "KAYNES")
    assert latest is not None
    assert latest["close"] == 101.0


def test_bare_symbol_is_used_for_the_store_file(tmp_path: Path) -> None:
    """The store key matches the reader's rule exactly, suffix and all."""
    adapter = FakeAdapter({"SBIN-EQ": _rows()})
    report = sync_candles(str(tmp_path), ["NSE:SBIN-EQ"], FakeManager(adapter))
    assert report["results"][0]["symbol"] == "SBIN-EQ"
    assert latest_candle(tmp_path, "SBIN-EQ") is not None


def test_store_key_matches_the_live_service_rule() -> None:
    """Guard against writer/reader drift on the per-file symbol key."""
    from app.services.live_trading_service import _clean_symbol
    from data.candle_store import store_path

    for symbol in ("NSE:KAYNES", "NSE:SBIN-EQ", "nse:reliance-eq"):
        key = _clean_symbol(symbol)
        assert store_path("/tmp", key).name == f"{key}.db"


def test_a_venue_failure_is_reported_not_hidden(tmp_path: Path) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()}, fail={"SBIN"})
    report = sync_candles(str(tmp_path), ["NSE:KAYNES", "NSE:SBIN"], FakeManager(adapter))

    statuses = {r["symbol"]: r["status"] for r in report["results"]}
    assert statuses == {"KAYNES": "STORED", "SBIN": "FAILED"}
    failed = next(r for r in report["results"] if r["symbol"] == "SBIN")
    assert "venue said no" in failed["detail"]
    assert latest_candle(tmp_path, "SBIN") is None


def test_no_symbols_is_an_explicit_refusal(tmp_path: Path) -> None:
    report = sync_candles(str(tmp_path), [], FakeManager(FakeAdapter({})))
    assert report["detail"] == "no instruments configured"
    assert report["results"] == []


def test_without_a_session_nothing_is_stored(tmp_path: Path) -> None:
    report = sync_candles(str(tmp_path), ["NSE:KAYNES"], FakeManager(None))
    assert "no authenticated session" in report["detail"]
    assert report["written_total"] == 0


def test_unsupported_timeframe_is_refused(tmp_path: Path) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()})
    report = sync_candles(str(tmp_path), ["NSE:KAYNES"], FakeManager(adapter), timeframe="7m")
    assert "unsupported timeframe" in report["detail"]
    assert adapter.calls == []


def test_window_is_the_requested_number_of_days(tmp_path: Path) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()})
    now = 1_800_000_000
    sync_candles(str(tmp_path), ["NSE:KAYNES"], FakeManager(adapter), days=10, now_epoch=now)
    _symbol, _resolution, start, end = adapter.calls[0]
    assert end == now
    assert start == now - 10 * 86400


@pytest.mark.parametrize("broker_id", ["", "paper"])
def test_selection_falls_back_to_the_configured_venue(tmp_path: Path, broker_id: str) -> None:
    adapter = FakeAdapter({"KAYNES": _rows()})
    report = sync_candles(str(tmp_path), ["NSE:KAYNES"], FakeManager(adapter, broker_id))
    assert report["results"][0]["status"] == "STORED"
