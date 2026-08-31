"""CandleDB — schema, idempotent upserts, dedupe, corruption, boundaries."""

from collections.abc import Generator
from datetime import datetime

import pytest

from data.storage.candle_db import (
    CandleDB,
    db_path,
    normalise_ts_str,
    parse_dt,
)
from data.tests.conftest import candle


@pytest.fixture
def cdb(tmp_path) -> Generator[CandleDB, None, None]:
    db = CandleDB(tmp_path / "TEST.db")
    db.connect()
    yield db
    db.close()


def test_db_path_is_per_symbol(tmp_path) -> None:
    assert db_path(tmp_path, "RELIANCE", "15m") == str(tmp_path / "RELIANCE.db")
    assert db_path(tmp_path, "A.B-C", "15m") == str(tmp_path / "ABC.db")


def test_upsert_inserts_and_counts(cdb: CandleDB) -> None:
    n = cdb.upsert([candle(datetime(2026, 1, 1, 9, 15))])
    assert n == 1
    assert cdb.count() == 1
    assert cdb.trading_day_count() == 1


def test_upsert_is_idempotent(cdb: CandleDB) -> None:
    row = candle(datetime(2026, 1, 1, 9, 15))
    assert cdb.upsert([row]) == 1
    assert cdb.upsert([row]) == 0
    assert cdb.count() == 1


def test_earliest_latest(cdb: CandleDB) -> None:
    cdb.upsert([candle(datetime(2026, 1, 3, 9, 15))])
    cdb.upsert([candle(datetime(2026, 1, 1, 9, 15))])
    cdb.upsert([candle(datetime(2026, 1, 2, 9, 15))])
    assert cdb.earliest() == datetime(2026, 1, 1, 9, 15)
    assert cdb.latest() == datetime(2026, 1, 3, 9, 15)
    assert cdb._conn is not None
    rows = cdb._conn.execute("SELECT MIN(candle_time), MAX(candle_time) FROM ohlcv").fetchone()
    assert rows is not None
    assert rows[0] == "2026-01-01 09:15:00"
    assert rows[1] == "2026-01-03 09:15:00"


def test_string_timestamps_are_normalised(cdb: CandleDB) -> None:
    n = cdb.upsert(
        [{"date": "2026-01-01T09:15:30", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 10}]
    )
    assert n == 1
    assert cdb._conn is not None
    raw = cdb._conn.execute("SELECT candle_time FROM ohlcv").fetchone()
    assert raw[0] == "2026-01-01 09:15:00"


def test_non_date_strings_stored_verbatim_like_original(cdb: CandleDB) -> None:
    # Original-engine behaviour: string dates are stored verbatim (no parse).
    n = cdb.upsert(
        [{"date": "not-a-date", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 10}]
    )
    assert n == 1
    assert cdb.earliest() is None  # unparsable, but stored


def test_remove_duplicates(cdb: CandleDB) -> None:
    row = candle(datetime(2026, 1, 1, 9, 15))
    cdb.upsert([row, row])
    assert cdb.count() == 1
    assert cdb.remove_duplicates() == 0


def test_corruption_detection_and_removal_on_clean_db(cdb: CandleDB) -> None:
    # The ohlcv schema is NOT NULL, so clean rows never count as corruption;
    # the removal path is a safety net for legacy data.
    cdb.upsert([candle(datetime(2026, 1, 1, 9, 15))])
    assert cdb.corruption_count() == 0
    assert cdb.remove_corruption() == 0
    assert cdb.count() == 1


def test_boundary_roundtrip(cdb: CandleDB) -> None:
    assert cdb.get_boundary("LISTING_START") is None
    cdb.set_boundary("LISTING_START", "2025-06-01", verified=1)
    boundary = cdb.get_boundary("LISTING_START")
    assert boundary is not None
    assert boundary["boundary_date"] == "2025-06-01"
    assert boundary["verified"] == 1


def test_migrations_run_on_connect(tmp_path) -> None:
    # Build a legacy non_trading table without the interval column.
    import sqlite3

    path = tmp_path / "LEGACY.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE non_trading (date_str TEXT NOT NULL, confirmed_at TEXT NOT NULL)")
    conn.commit()
    conn.close()

    db = CandleDB(path)
    db.connect()
    assert db._conn is not None
    cols = {row[1] for row in db._conn.execute("PRAGMA table_info(non_trading)").fetchall()}
    db.close()
    assert "interval" in cols
    assert "reason" in cols


def test_normalise_ts_str_helpers() -> None:
    assert normalise_ts_str("2026-01-01T09:15:30") == "2026-01-01 09:15:00"
    assert normalise_ts_str("2026-01-01 09:15:45") == "2026-01-01 09:15:00"
    assert parse_dt("2026-01-01T09:15:30") == datetime(2026, 1, 1, 9, 15)
    assert parse_dt(None) is None
