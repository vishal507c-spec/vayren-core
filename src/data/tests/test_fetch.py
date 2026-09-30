"""FetchEngine — retries, 429 emergency stop, token sentinel."""

from datetime import datetime

from data.provider.zerodha.fetch import RATE_LIMITED, TOKEN_EXPIRED, FetchEngine
from data.tests.conftest import FakeKite, candle, make_settings

D1 = datetime(2026, 1, 1)
D2 = datetime(2026, 1, 2)


def fetch(settings, kite) -> FetchEngine:
    return FetchEngine(settings, kite)


def test_returns_candles(tmp_path) -> None:
    kite = FakeKite(candles=[candle(D1)])
    engine = fetch(make_settings(tmp_path), kite)
    result = engine.fetch(1, "15minute", D1, D2)
    assert result == kite.candles
    assert len(kite.calls) == 1


def test_empty_data_is_ok(tmp_path) -> None:
    engine = fetch(make_settings(tmp_path), FakeKite(candles=[]))
    assert engine.fetch(1, "15minute", D1, D2) == []


def test_token_exception_returns_sentinel(tmp_path) -> None:
    kite = FakeKite(errors=["TokenException: token expired"])
    engine = fetch(make_settings(tmp_path), kite)
    assert engine.fetch(1, "15minute", D1, D2) is TOKEN_EXPIRED


def test_retries_then_succeeds(tmp_path) -> None:
    kite = FakeKite(candles=[candle(D1)], errors=["boom 1"])
    engine = fetch(make_settings(tmp_path, max_retries=3), kite)
    assert engine.fetch(1, "15minute", D1, D2) == kite.candles


def test_rate_limit_emergency_stop(tmp_path) -> None:
    kite = FakeKite(errors=["429 Too Many Requests", "429 Too Many Requests"])
    engine = fetch(make_settings(tmp_path, max_consecutive_429=2, max_retries=5), kite)
    assert engine.fetch(1, "15minute", D1, D2) is RATE_LIMITED
    assert len(kite.calls) == 2


def test_generic_errors_exhaust_retries(tmp_path) -> None:
    kite = FakeKite(errors=["network down"])
    engine = fetch(make_settings(tmp_path, max_retries=2), kite)
    assert engine.fetch(1, "15minute", D1, D2) == []
    assert len(kite.calls) == 2
