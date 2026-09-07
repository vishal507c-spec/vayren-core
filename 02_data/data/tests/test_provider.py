"""Provider contract, factory, adapter and core-isolation tests.

- The isolation test runs a fresh interpreter: importing the core engine must
  NOT import the Zerodha adapter, its SDKs, credentials or any provider
  internals.
- The FakeProvider test proves the engine drives a NON-Zerodha provider
  (implementing only the contract) end to end into SQLite — a future broker
  needs zero engine changes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from data.downloader.engine import HistoricalDownloadEngine
from data.provider import RATE_LIMITED, TOKEN_EXPIRED, ProviderError
from data.provider.contract import (
    CANONICAL_INTERVALS,
    ERR_AUTHENTICATION_FAILED,
    ERR_INVALID_REQUEST,
    ERR_INVALID_SYMBOL,
    ERR_NETWORK_ERROR,
)
from data.provider.factory import build_provider
from data.provider.zerodha import AuthEngine, ZerodhaProvider
from data.reporter import RecordingReporter
from data.settings import DownloadSettings
from data.storage.candle_db import CandleDB, db_path
from data.tests.conftest import FakeAuth, FakeKite, candle, make_settings

_DATA_ROOT = Path(__file__).resolve().parents[2]

_INSTRUMENT_ROWS = [{"tradingsymbol": "TEST", "instrument_token": 777}]


class FakeProvider:
    """Minimal contract implementation — NO Zerodha code anywhere.

    Stands in for Broker B: canonical symbols, canonical intervals, generated
    candle rows, token/session bookkeeping and normalized errors.
    """

    def __init__(
        self,
        settings: DownloadSettings | None = None,  # noqa: ARG002 — factory contract
        *,
        symbols: set[str] | None = None,
    ) -> None:
        self._symbols = set(symbols or {"RELIANCE", "TCS"})
        self._calls: list[tuple[str, str, datetime, datetime]] = []
        self._session_resets = 0
        self.renewals = 0

    def available(self) -> tuple[bool, str]:
        return True, "ready"

    def symbols(self) -> set[str]:
        return set(self._symbols)

    def fetch_candles(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict] | object:
        if interval not in CANONICAL_INTERVALS:
            raise ProviderError(f"unsupported interval: {interval!r}", ERR_INVALID_REQUEST)
        if symbol not in self._symbols:
            raise ProviderError(f"unknown symbol: {symbol}", ERR_INVALID_SYMBOL)
        self._calls.append((symbol, interval, start, end))
        rows = []
        day = start
        while day <= end:
            rows.append(candle(day))
            day += timedelta(days=1)
        return rows

    def new_session(self) -> None:
        self._session_resets += 1

    def renew(self) -> None:
        self.renewals += 1


# ── contract ────────────────────────────────────────────────────────────────


def test_sentinels_are_distinct_control_tokens() -> None:
    assert TOKEN_EXPIRED is not RATE_LIMITED
    assert isinstance(TOKEN_EXPIRED, object)
    assert isinstance(RATE_LIMITED, object)


def test_canonical_intervals_are_broker_free() -> None:
    assert CANONICAL_INTERVALS == ("1m", "5m", "15m", "30m", "1h")


def test_provider_error_carries_normalized_code() -> None:
    err = ProviderError("boom", ERR_INVALID_REQUEST)
    assert err.code == ERR_INVALID_REQUEST
    assert str(err) == "boom"
    assert ProviderError("boom").code == "UNKNOWN_PROVIDER_ERROR"


# ── factory ─────────────────────────────────────────────────────────────────


def test_build_provider_resolves_zerodha(tmp_path) -> None:
    settings = make_settings(tmp_path)  # provider defaults to "zerodha"
    provider = build_provider(settings)
    assert isinstance(provider, ZerodhaProvider)


def test_build_provider_unknown_name_raises(tmp_path) -> None:
    settings = make_settings(tmp_path, provider="nope")
    with pytest.raises(ValueError, match="unknown provider"):
        build_provider(settings)


def test_registry_registration_enables_future_providers(tmp_path) -> None:
    """Future providers register directly in the single UBL registry (M7:
    the ``register_provider`` shim is retired); ``build_provider`` resolves
    them unchanged."""
    from broker.capabilities import Caps, Domain, capability_set
    from broker.faces import FactoryPlugin
    from broker.registry import BrokerRecord, default_registry

    caps = capability_set({Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS)})
    registry = default_registry()
    registry.register(
        BrokerRecord(
            name="mock",
            display_name="mock",
            plugin=FactoryPlugin(
                name="mock",
                display_name="mock",
                factories={Domain.HISTORICAL_DATA: FakeProvider},
                capabilities=caps,
            ),
            capabilities=caps,
            faces=(Domain.HISTORICAL_DATA,),
        )
    )
    try:
        provider = build_provider(make_settings(tmp_path, provider="mock"))
        assert isinstance(provider, FakeProvider)
    finally:
        if "mock" in registry:
            registry.unregister("mock")


# ── ZerodhaProvider adapter ─────────────────────────────────────────────────


def test_zerodha_provider_delegates_to_preserved_stack(tmp_path) -> None:
    settings = make_settings(tmp_path)
    kite = FakeKite(candles=[candle(datetime(2026, 1, 1))])
    kite.instrument_rows = list(_INSTRUMENT_ROWS)
    network_calls: list[str] = []

    def _instruments(_exchange: str) -> list[dict]:
        network_calls.append(_exchange)
        return kite.instrument_rows

    kite.instruments = _instruments
    auth = FakeAuth(kite=kite)
    provider = ZerodhaProvider(settings, auth=cast(AuthEngine, auth))

    assert provider.available() == (True, "ready")
    assert provider.symbols() == {"TEST"}
    assert provider.symbols() == {"TEST"}  # resolver cached
    assert network_calls == ["NSE"]

    candles = provider.fetch_candles("TEST", "15m", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert candles == kite.candles
    # the Kite interval id never leaves the adapter
    assert kite.calls[0][3] == "15minute"

    provider.new_session()
    provider.renew()
    assert auth.renewed == 1


def test_zerodha_provider_maps_canonical_intervals(tmp_path) -> None:
    settings = make_settings(tmp_path)
    kite = FakeKite(candles=[])
    kite.instrument_rows = list(_INSTRUMENT_ROWS)
    provider = ZerodhaProvider(settings, auth=cast(AuthEngine, FakeAuth(kite=kite)))
    provider.fetch_candles("TEST", "1m", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert kite.calls[0][3] == "minute"
    provider.fetch_candles("TEST", "1h", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert kite.calls[1][3] == "60minute"


def test_zerodha_provider_rejects_unknown_interval(tmp_path) -> None:
    settings = make_settings(tmp_path)
    kite = FakeKite(candles=[])
    kite.instrument_rows = list(_INSTRUMENT_ROWS)
    provider = ZerodhaProvider(settings, auth=cast(AuthEngine, FakeAuth(kite=kite)))
    with pytest.raises(ProviderError) as err:
        provider.fetch_candles("TEST", "45m", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert err.value.code == ERR_INVALID_REQUEST


def test_zerodha_provider_unknown_symbol_is_invalid_symbol(tmp_path) -> None:
    settings = make_settings(tmp_path)
    kite = FakeKite(candles=[])
    kite.instrument_rows = list(_INSTRUMENT_ROWS)
    provider = ZerodhaProvider(settings, auth=cast(AuthEngine, FakeAuth(kite=kite)))
    with pytest.raises(ProviderError) as err:
        provider.fetch_candles("GHOST", "15m", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert err.value.code == ERR_INVALID_SYMBOL


def test_zerodha_provider_auth_failure_is_normalized(tmp_path) -> None:
    from data.provider.zerodha.auth import AuthError

    settings = make_settings(tmp_path)
    auth = FakeAuth(kite=None)
    provider = ZerodhaProvider(settings, auth=cast(AuthEngine, auth))
    auth.get_kite = lambda: (_ for _ in ()).throw(AuthError("no sdk"))
    with pytest.raises(ProviderError) as err:
        provider.symbols()
    assert err.value.code == ERR_AUTHENTICATION_FAILED
    assert "no sdk" in str(err.value)


def test_zerodha_provider_network_failure_is_normalized(tmp_path) -> None:
    settings = make_settings(tmp_path)
    kite = FakeKite(candles=[])

    def _explode(_exchange: str) -> list[dict]:
        raise RuntimeError("connection reset")

    kite.instruments = _explode
    provider = ZerodhaProvider(settings, auth=cast(AuthEngine, FakeAuth(kite=kite)))
    with pytest.raises(ProviderError) as err:
        provider.symbols()
    assert err.value.code == ERR_NETWORK_ERROR


# ── engine requires a provider ──────────────────────────────────────────────


def test_engine_requires_provider(tmp_path) -> None:
    with pytest.raises(TypeError, match="requires a provider"):
        HistoricalDownloadEngine(make_settings(tmp_path))


# ── future broker: FakeProvider drives the real engine into SQLite ──────────


def test_engine_downloads_through_fake_provider_into_sqlite(tmp_path) -> None:
    """Future Broker proof: engine → FakeProvider → normalized candles → SQLite."""
    settings = make_settings(tmp_path, chunk_days=200)
    fake = FakeProvider(symbols={"RELIANCE"})
    engine = HistoricalDownloadEngine(settings, reporter=RecordingReporter(), provider=fake)

    result = engine.run_download("RELIANCE", "15m", datetime(2026, 1, 1), datetime(2026, 1, 3))

    assert result["ok"]
    assert result["new_rows"] == 3
    assert fake._calls  # fetched through the provider
    assert fake._session_resets == 1

    cdb = CandleDB(db_path(settings.data_dir, "RELIANCE", "15m"))
    cdb.connect()
    assert cdb.count() == 3
    cdb.close()


def test_engine_reports_unknown_symbol_from_fake_provider(tmp_path) -> None:
    settings = make_settings(tmp_path, chunk_days=200)
    fake = FakeProvider(symbols={"RELIANCE"})
    reporter = RecordingReporter()
    engine = HistoricalDownloadEngine(settings, reporter=reporter, provider=fake)

    result = engine.run_download("GHOST", "15m", datetime(2026, 1, 1), datetime(2026, 1, 2))

    assert not result["ok"]
    assert result["error"] == "unknown-symbol"
    assert any("symbol not found" in c["message"] for _, c in reporter.calls)


# ── core import isolation (fresh interpreter) ───────────────────────────────


def test_core_engine_imports_are_broker_free() -> None:
    """Fresh interpreter: importing the core engine pulls in NO provider
    internals, SDKs or broker credentials.

    The engine may import the contract (``data.provider.contract``) and the
    parent package, but never the Zerodha adapter, its auth stack, the
    factory, kiteconnect, or ``ZerodhaCredentials``.
    """
    code = (
        "import sys\n"
        "import data.downloader.engine\n"
        "import data.provider.contract\n"
        "forbidden = ["
        "'data.provider.zerodha', 'data.provider.zerodha.adapter', "
        "'data.provider.zerodha.auth', 'data.provider.zerodha.credentials', "
        "'data.provider.zerodha.fetch', 'data.provider.zerodha.instruments', "
        "'data.provider.factory', 'kiteconnect'\n"
        "]\n"
        "present = [m for m in sys.modules if m in forbidden]\n"
        "assert not present, f'core engine imported provider internals: {present}'\n"
        "settings_mod = sys.modules.get('data.settings')\n"
        "assert settings_mod is not None\n"
        "assert not hasattr(settings_mod, 'ZerodhaCredentials'), "
        "'ZerodhaCredentials leaked into data.settings'\n"
        "print('CLEAN')\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_DATA_ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_DATA_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "CLEAN" in result.stdout
