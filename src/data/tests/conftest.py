"""Shared fixtures for data domain tests."""

from datetime import datetime
from typing import Any

import pytest

from data.settings import DownloadSettings


def make_settings(data_dir, **overrides: Any) -> DownloadSettings:
    """Test settings: zero delays so tests never sleep."""
    values: dict[str, Any] = {
        "data_dir": data_dir,
        "chunk_days": 200,
        "max_retries": 2,
        "retry_delay_s": 0.0,
        "chunk_delay_min": 0.0,
        "chunk_delay_max": 0.0,
        "symbol_delay_min": 0.0,
        "symbol_delay_max": 0.0,
        "min_inter_call_seconds": 0.0,
        "max_consecutive_429": 2,
        "max_passes": 2,
        # Market window pinned outside real IST hours so tests are
        # deterministic no matter when the suite runs (09:15-12:40 IST
        # blocks downloads by design; the calendar tests override ist_now and
        # keep the default window via their own explicit settings).
        "market_open_h": 0,
        "market_open_m": 0,
        "market_close_h": 0,
        "market_close_m": 1,
    }
    values.update(overrides)
    return DownloadSettings(**values)


@pytest.fixture
def settings(tmp_path) -> DownloadSettings:
    return make_settings(tmp_path)


def candle(dt: datetime, price: float = 100.0) -> dict:
    return {
        "date": dt,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price,
        "volume": 1000,
    }


class FakeKite:
    """Recorded historical_data stub: candles or scripted errors."""

    def __init__(self, candles=None, errors=None, instruments=None) -> None:
        self.candles = list(candles or [])
        self.errors = list(errors or [])
        self.instrument_rows = list(instruments or [])
        self.calls: list[tuple] = []

    def historical_data(
        self,
        instrument_token,
        from_date,
        to_date,
        interval,
        continuous=False,  # noqa: ARG002 - mirrors the real kiteconnect SDK signature (kwarg calls)
        oi=False,  # noqa: ARG002
    ):
        self.calls.append((instrument_token, from_date, to_date, interval))
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        return self.candles

    def instruments(self, _exchange: str):
        return self.instrument_rows


class FakeAuth:
    """Engine auth stub: returns the FakeKite, never touches the network."""

    def __init__(self, kite=None, ready: bool = True, reason: str = "ready") -> None:
        self._kite = kite
        self._ready = ready
        self._reason = reason
        self.renewed = 0

    def get_kite(self):
        return self._kite

    def available(self) -> tuple[bool, str]:
        return self._ready, self._reason

    def renew(self):
        self.renewed += 1
        return self._kite
