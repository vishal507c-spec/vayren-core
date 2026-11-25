"""1:1 preservation + connected-broker seam tests for the historical engine."""

from __future__ import annotations

import hashlib
import pathlib
from datetime import datetime
from typing import Any

import pytest

from data import download_broker_adapter, historical_download_engine

# SHA256 of every byte of the preserved engine EXCEPT the auth seam
# (imports/constants + AuthEngine). Computed from the original attached file.
_PRESERVED_CORE_SHA256 = "07ab37661854569c52e4507af22d05b5763d76d70248596379698886ef36c5cd"


def _preserved_core_hash(path: pathlib.Path) -> str:
    source = path.read_text(encoding="utf-8")
    start = source.index("def dt_str(dt: datetime) -> str:")
    auth = source.rfind("# ====", 0, source.index("#  AUTH ENGINE"))
    console = source.rfind("# ====", 0, source.index("#  CONSOLE HELPERS"))
    return hashlib.sha256((source[start:auth] + source[console:]).encode("utf-8")).hexdigest()


class _Manager:
    def __init__(self, broker_id: str = "", connected: bool = False, state: str = "CONNECTED"):
        self.broker_id = broker_id
        self.connected = connected
        self.state = state
        self.adapter: Any = None

    def snapshot(self) -> dict:
        return {"active_broker": self.broker_id, "brokers": [{"id": self.broker_id}]}

    def is_connected(self, broker_id: str) -> bool:
        return self.connected and broker_id == self.broker_id

    def get_session_snapshot(self, _broker_id: str) -> dict:
        return {"state": self.state}

    def read_only_adapter(self, _broker_id: str) -> Any:
        return self.adapter


def test_preserved_engine_logic_is_byte_identical_to_original() -> None:
    path = pathlib.Path(historical_download_engine.__file__)
    assert _preserved_core_hash(path) == _PRESERVED_CORE_SHA256


def test_no_embedded_credentials_or_selenium_auth_in_app() -> None:
    source = pathlib.Path(historical_download_engine.__file__).read_text(encoding="utf-8")
    for secret in ("mw629g78m1xqp2n4", "7esv0j10ovkvu0yx5nfc9k2u6y3ncuht", "MBS239", "vipvipvip"):
        assert secret not in source
    for module in ("import selenium", "import pyotp", "from kiteconnect import", "chromedriver"):
        assert module not in source


@pytest.mark.parametrize(
    ("manager", "message"),
    [
        (_Manager(), "No broker CONNECTED"),
        (_Manager("fyers", False), "not CONNECTED"),
        (_Manager("fyers", True, "DEGRADED"), "not CONNECTED"),
    ],
)
def test_no_download_without_a_connected_broker(manager: _Manager, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        download_broker_adapter.build_selected_broker_shim(manager, "/tmp")


def test_fyers_shim_preserves_kite_request_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _Manager("fyers", True)
    delivered: dict[str, Any] = {}

    class FakeFyers:
        def fetch_candles(self, symbol, interval, start, end):
            delivered.update(symbol=symbol, interval=interval, start=start, end=end)
            return [
                {
                    "date": datetime(2026, 1, 1, 9, 15),
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 10,
                }
            ]

    manager.adapter = FakeFyers()
    monkeypatch.setattr(
        download_broker_adapter,
        "_universe_symbol_rows",
        lambda _csv: [{"tradingsymbol": "KAYNES", "instrument_token": 1234, "exchange": "NSE"}],
    )
    shim = download_broker_adapter.build_selected_broker_shim(manager)
    rows = shim.instruments("NSE")
    assert rows[0]["tradingsymbol"] == "KAYNES"
    start = datetime(2026, 1, 1, 9, 15)
    end = datetime(2026, 1, 2, 15, 15)
    candles = shim.historical_data(1234, start, end, "15minute")
    assert delivered == {"symbol": "KAYNES", "interval": "15m", "start": start, "end": end}
    assert candles[0]["date"] == datetime(2026, 1, 1, 9, 15)
    assert set(candles[0]) == {"date", "open", "high", "low", "close", "volume"}


def test_fyers_rate_limit_is_visible_to_the_original_fetch_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager("fyers", True)

    class FakeFyers:
        def fetch_candles(self, _symbol, _interval, _start, _end):
            raise RuntimeError("rate limited by venue (HTTP 429)")

    manager.adapter = FakeFyers()
    monkeypatch.setattr(
        download_broker_adapter,
        "_universe_symbol_rows",
        lambda _csv: [{"tradingsymbol": "ZEEL", "instrument_token": 99}],
    )
    shim = download_broker_adapter.build_selected_broker_shim(manager)
    with pytest.raises(RuntimeError, match="429"):
        shim.historical_data(99, datetime(2026, 1, 1), datetime(2026, 1, 2), "30minute")


def test_zerodha_shim_reuses_the_public_history_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class FakeProvider:
        def fetch_candles(self, symbol, interval, start, end):
            calls.update(symbol=symbol, interval=interval, start=start, end=end)
            return [
                {"date": start, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}
            ]

    monkeypatch.setattr(
        download_broker_adapter,
        "build_provider",
        lambda _settings: FakeProvider(),
    )
    monkeypatch.setattr(
        download_broker_adapter,
        "_universe_symbol_rows",
        lambda _csv: [{"tradingsymbol": "KEC", "instrument_token": 321}],
    )

    shim = download_broker_adapter.build_selected_broker_shim(_Manager("zerodha", True))
    assert shim.instruments()[0]["tradingsymbol"] == "KEC"
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 2)
    rows = shim.historical_data(321, start, end, "minute", continuous=False, oi=False)
    assert calls == {"symbol": "KEC", "interval": "1m", "start": start, "end": end}
    assert rows[0]["date"] == start
