"""HistoricalDownloadEngine — run_download flow with a fake provider."""

from datetime import datetime, timedelta
from typing import cast

from data.downloader.engine import HistoricalDownloadEngine
from data.provider.zerodha import AuthEngine, ZerodhaProvider
from data.reporter import RecordingReporter
from data.tests.conftest import FakeAuth, FakeKite, candle, make_settings


def _instrument_row(symbol: str, token: int) -> dict:
    return {"tradingsymbol": symbol, "instrument_token": token}


DEFAULT_INSTRUMENTS = [
    _instrument_row("TEST", 777),
    _instrument_row("RELIANCE", 738561),
    _instrument_row("RETRY", 1001),
    _instrument_row("RENEW", 1002),
    _instrument_row("C", 1003),
    _instrument_row("R", 1004),
    _instrument_row("X", 1005),
]


def make_engine(tmp_path, kite, reporter=None, auth=None, instrument_rows=None):
    settings = make_settings(tmp_path)
    if instrument_rows is not None:
        kite.instrument_rows = list(instrument_rows)
    elif not kite.instrument_rows:
        kite.instrument_rows = list(DEFAULT_INSTRUMENTS)
    auth = auth if auth is not None else cast(AuthEngine, FakeAuth(kite=kite))
    provider = ZerodhaProvider(settings, auth=auth)
    return HistoricalDownloadEngine(settings, reporter=reporter, provider=provider)


def test_run_download_writes_rows(tmp_path) -> None:
    start = datetime(2026, 1, 1)
    kite = FakeKite(candles=[candle(start), candle(start + timedelta(days=1))])
    reporter = RecordingReporter()
    engine = make_engine(tmp_path, kite, reporter=reporter)

    result = engine.run_download("TEST", "15m", start, datetime(2026, 1, 5))
    assert result["ok"]
    assert result["new_rows"] == 2
    assert result["db_total"] == 2
    assert result["trading_days"] == 2
    assert result["symbol"] == "TEST"
    names = [call[0] for call in reporter.calls]
    assert "on_symbol_started" in names
    assert "on_symbol_finished" in names
    assert "on_chunk" in names


def test_run_download_unknown_symbol_fails(tmp_path) -> None:
    kite = FakeKite(candles=[], instruments=[_instrument_row("KNOWN", 42)])
    engine = make_engine(tmp_path, kite)
    result = engine.run_download("GHOST", "15m", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert not result["ok"]
    assert result["error"] == "unknown-symbol"
    assert len(kite.calls) == 0  # never fetched candles


def test_run_download_uses_token_map(tmp_path) -> None:
    start = datetime(2026, 1, 1)
    kite = FakeKite(
        candles=[candle(start)],
        instruments=[_instrument_row("RELIANCE", 738561)],
    )
    engine = make_engine(tmp_path, kite)
    result = engine.run_download("RELIANCE", "15m", start, start + timedelta(days=1))
    assert result["ok"]
    assert kite.calls[0][0] == 738561


def test_run_download_retries_once_after_token_renewal(tmp_path) -> None:
    start = datetime(2026, 1, 1)
    kite = FakeKite(
        candles=[candle(start)],
        errors=["TokenException: expired", "TokenException: expired", "TokenException: expired"],
    )
    reporter = RecordingReporter()
    fake_auth = FakeAuth(kite=kite)
    auth = cast(AuthEngine, fake_auth)
    engine = make_engine(tmp_path, kite, reporter=reporter, auth=auth)
    result = engine.run_download("RETRY", "15m", start, start + timedelta(days=1))
    # First attempt fails with token expiry; renew + retry also fails → the
    # run reports failure and aborts (original engine behaviour).
    assert not result["ok"]
    assert fake_auth.renewed == 1
    assert result.get("error") is None


def test_run_download_succeeds_after_renewal(tmp_path) -> None:
    start = datetime(2026, 1, 1)
    kite = FakeKite(candles=[candle(start)], errors=["TokenException: expired"])
    reporter = RecordingReporter()
    fake_auth = FakeAuth(kite=kite)
    auth = cast(AuthEngine, fake_auth)
    engine = make_engine(tmp_path, kite, reporter=reporter, auth=auth)
    result = engine.run_download("RENEW", "15m", start, start + timedelta(days=1))
    assert result["ok"]
    assert result["new_rows"] == 1
    assert fake_auth.renewed == 1


def test_run_download_auth_failure_reported(tmp_path) -> None:
    from data.provider.zerodha.auth import AuthError

    reporter = RecordingReporter()
    engine = make_engine(tmp_path, kite=FakeKite(), reporter=reporter, auth=FakeAuth(kite=None))
    cast(ZerodhaProvider, engine._provider)._auth.get_kite = lambda: (_ for _ in ()).throw(
        AuthError("no sdk")
    )
    result = engine.run_download("X", "15m", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert not result["ok"]
    assert result["error"] == "auth"


def test_cancel_stops_between_operations(tmp_path) -> None:
    start = datetime(2026, 1, 1)
    kite = FakeKite(candles=[candle(start)])
    engine = make_engine(tmp_path, kite)
    engine.cancel()
    result = engine.run_download("C", "15m", start, start + timedelta(days=1))
    assert "aborted" in result
    assert len(kite.calls) == 0


def test_cancel_stops_in_flight_run_between_chunks(tmp_path) -> None:
    import threading
    import time

    start = datetime(2026, 1, 1)
    settings = make_settings(tmp_path, chunk_days=1)
    FakeKite(candles=[candle(start)])

    class SlowKite(FakeKite):
        def historical_data(self, *args, **kwargs):
            time.sleep(0.05)
            return super().historical_data(*args, **kwargs)

    slow = SlowKite(candles=[candle(start)], instruments=[_instrument_row("SLOW", 1)])
    auth = cast(AuthEngine, FakeAuth(kite=slow))
    provider = ZerodhaProvider(settings, auth=auth)
    engine = HistoricalDownloadEngine(settings, provider=provider)

    results: list[dict] = []

    def run() -> None:
        results.append(engine.run_download("SLOW", "15m", start, start + timedelta(days=4)))

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.12)  # let a couple of chunks complete
    engine.cancel()
    thread.join(timeout=10)

    assert len(results) == 1
    assert not results[0]["ok"]


def test_reset_clears_cancel(tmp_path) -> None:
    start = datetime(2026, 1, 1)
    kite = FakeKite(candles=[candle(start)])
    engine = make_engine(tmp_path, kite)
    engine.cancel()
    engine.reset()
    result = engine.run_download("R", "15m", start, start + timedelta(days=1))
    assert result["ok"]


def test_provider_available_reports_ready_and_status(tmp_path) -> None:
    engine = make_engine(tmp_path, kite=FakeKite(), auth=FakeAuth(ready=True))
    ready, reason = engine.provider_available()
    assert ready and reason == "ready"
    status = engine.status()
    assert status["provider"] == "zerodha"
    assert status["provider_ready"] is True
    assert "api_key" not in str(status)
    assert "secret" not in str(status).lower()


def test_scan_symbol_no_db(tmp_path) -> None:
    engine = make_engine(tmp_path, kite=FakeKite())
    info = engine.scan_symbol("ABSENT", "15m")
    assert info.symbol == "ABSENT"
    assert info.state.name == "NOT_STARTED"
