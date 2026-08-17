"""forward_sweep — chunked sweeps, progress reporting, boundary detection."""

from datetime import datetime, timedelta
from functools import partial

from data.downloader.sweep import forward_sweep
from data.provider.zerodha.fetch import FetchEngine
from data.reporter import RecordingReporter
from data.storage.candle_db import CandleDB, db_path
from data.tests.conftest import FakeKite, candle, make_settings


def make_cdb(data_dir, symbol: str) -> CandleDB:
    cdb = CandleDB(db_path(data_dir, symbol, "15m"))
    cdb.connect()
    return cdb


def make_fetcher(settings, kite) -> FetchEngine:
    return FetchEngine(settings, kite)


def make_chunk(fetcher, token: int, interval: str):
    """Engine-style fetch_chunk callable: (start, end) → candles/sentinel."""
    return partial(fetcher.fetch, token, interval)


def test_sweep_writes_all_chunks(tmp_path) -> None:
    settings = make_settings(tmp_path, chunk_days=1)
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 3)
    kite = FakeKite(candles=[candle(start), candle(start + timedelta(days=1))])
    reporter = RecordingReporter()
    cdb = make_cdb(tmp_path, "SWEEP")
    ok, new_rows, _zero = forward_sweep(
        make_chunk(make_fetcher(settings, kite), 123, "15minute"),
        cdb,
        "SWEEP",
        "15m",
        start,
        end,
        settings,
        reporter=reporter,
    )
    cdb.close()
    assert ok
    assert new_rows == 2
    chunks = [call for call in reporter.calls if call[0] == "on_chunk"]
    assert len(chunks) >= 1
    # Both dates landed in the DB: reopen to verify
    cdb = make_cdb(tmp_path, "SWEEP")
    assert cdb.count() == 2
    cdb.close()


def test_sweep_aborts_between_chunks(tmp_path) -> None:
    settings = make_settings(tmp_path, chunk_days=1)
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 5)
    kite = FakeKite(candles=[candle(start)])
    cdb = make_cdb(tmp_path, "ABORT")
    calls = {"n": 0}

    def abort():
        calls["n"] += 1
        return calls["n"] >= 2

    ok, _new, _zero = forward_sweep(
        make_chunk(make_fetcher(settings, kite), 123, "15minute"),
        cdb,
        "ABORT",
        "15m",
        start,
        end,
        settings,
        should_abort=abort,
    )
    cdb.close()
    assert not ok
    assert len(kite.calls) == 1


def test_head_sweep_all_zero_writes_boundary_eligible(tmp_path) -> None:
    settings = make_settings(tmp_path, chunk_days=200)
    start = datetime(2018, 1, 1)
    end = datetime(2018, 6, 1)
    kite = FakeKite(candles=[])
    cdb = make_cdb(tmp_path, "ZERO")
    cdb.upsert([candle(datetime(2019, 1, 1))])
    ok, new_rows, all_zero = forward_sweep(
        make_chunk(make_fetcher(settings, kite), 123, "15minute"),
        cdb,
        "ZERO",
        "15m",
        start,
        end,
        settings,
        is_head_sweep=True,
    )
    cdb.close()
    assert ok
    assert new_rows == 0
    assert all_zero


def test_tail_sweep_empty_is_not_boundary_eligible(tmp_path) -> None:
    settings = make_settings(tmp_path)
    kite = FakeKite(candles=[])
    cdb = make_cdb(tmp_path, "TAIL")
    cdb.upsert([candle(datetime(2026, 8, 1))])
    ok, _new, all_zero = forward_sweep(
        make_chunk(make_fetcher(settings, kite), 123, "15minute"),
        cdb,
        "TAIL",
        "15m",
        datetime(2026, 8, 2),
        datetime(2026, 8, 3),
        settings,
        is_head_sweep=False,
    )
    cdb.close()
    assert ok
    assert not all_zero
