"""DatabaseScanner — state derivation and LISTING_START suppression."""

from datetime import datetime, timedelta

from data.models import DLState
from data.storage.candle_db import CandleDB, db_path
from data.storage.scanner import DatabaseScanner, cleanup_candle_dbs
from data.tests.conftest import candle, make_settings


def seed_symbol_db(data_dir, symbol: str, start: datetime, days: int) -> None:
    cdb = CandleDB(db_path(data_dir, symbol, "15m"))
    cdb.connect()
    rows = [candle(start + timedelta(days=i)) for i in range(days)]
    cdb.upsert(rows)
    cdb.close()


def test_not_started_when_no_db(tmp_path) -> None:
    scanner = DatabaseScanner(make_settings(tmp_path))
    info = scanner.scan_one("ABSENT", "15m")
    assert info.state == DLState.NOT_STARTED
    assert info.row_count == 0


def test_empty_db_is_not_started(tmp_path) -> None:
    settings = make_settings(tmp_path)
    cdb = CandleDB(db_path(tmp_path, "EMPTY", "15m"))
    cdb.connect()
    cdb.close()
    info = DatabaseScanner(settings).scan_one("EMPTY", "15m")
    assert info.state == DLState.NOT_STARTED


def test_recent_data_is_download_complete(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        max_history_years=1,
        tail_lag_tolerance_days=20,
        head_tolerance_trading_days=5,
    )
    now = datetime.now()
    target = now - timedelta(days=365)
    # Data spanning the whole 1-year target window → head and tail both covered.
    seed_symbol_db(tmp_path, "FRESH", target, 365)
    info = DatabaseScanner(settings).scan_one("FRESH", "15m")
    assert info.state == DLState.DOWNLOAD_COMPLETE
    assert info.row_count == 365
    assert not info.missing_head
    assert not info.missing_tail


def test_old_earliest_data_is_partial(tmp_path) -> None:
    settings = make_settings(tmp_path, max_history_years=10, head_tolerance_trading_days=5)
    seed_symbol_db(tmp_path, "OLD", datetime(2018, 6, 1), 3)
    info = DatabaseScanner(settings).scan_one("OLD", "15m")
    assert info.state == DLState.PARTIAL_DOWNLOAD
    assert info.missing_head


def test_listing_boundary_suppresses_head_gap(tmp_path) -> None:
    settings = make_settings(tmp_path, max_history_years=10, head_tolerance_trading_days=5)
    seed_symbol_db(tmp_path, "BOUND", datetime(2018, 6, 1), 3)
    cdb = CandleDB(db_path(tmp_path, "BOUND", "15m"))
    cdb.connect()
    cdb.set_boundary("LISTING_START", "2018-05-20", verified=1)
    cdb.close()

    info = DatabaseScanner(settings).scan_one("BOUND", "15m")
    assert info.listing_start_verified
    assert not info.missing_head
    # Tail is still missing → partial
    assert info.state == DLState.PARTIAL_DOWNLOAD


def test_cleanup_runs_housekeeping(tmp_path) -> None:
    settings = make_settings(tmp_path)
    seed_symbol_db(tmp_path, "DIRTY", datetime(2026, 8, 1), 2)
    path = db_path(tmp_path, "DIRTY", "15m")
    cdb = CandleDB(path)
    cdb.connect()
    assert cdb._conn is not None
    # Legacy row with a non-normalised timestamp (seconds + 'T' separator).
    cdb._conn.execute(
        "INSERT OR IGNORE INTO ohlcv (candle_time, open, high, low, close, volume) "
        "VALUES ('2026-08-02T09:15:45', 1, 2, 0, 1, 10)"
    )
    cdb._conn.commit()
    cdb.close()

    scanner = DatabaseScanner(settings)
    infos = scanner.scan([{"trading_symbol": "DIRTY", "interval": "15m"}])
    cleanup_candle_dbs(settings, infos)
    cdb = CandleDB(path)
    cdb.connect()
    assert cdb._conn is not None
    raw = cdb._conn.execute("SELECT candle_time FROM ohlcv").fetchall()
    cdb.close()
    assert all(ts[0].endswith(":00") for ts in raw)
    assert not any("T" in ts[0] for ts in raw)
