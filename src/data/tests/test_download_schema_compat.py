"""Legacy history_boundaries schema repair — non-destructive and idempotent."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from data import download_schema_compat as compat
from data import historical_download_engine as engine


def _legacy_db(path: Path, *, with_date: bool = False) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, high REAL, "
            "low REAL, close REAL, volume INTEGER)"
        )
        conn.executemany(
            "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("2019-01-02 09:15:00", 1, 2, 0.5, 1.5, 10),
                ("2025-03-04 15:00:00", 2, 3, 1.5, 2.5, 20),
            ],
        )
        cols = "boundary_type TEXT PRIMARY KEY"
        if with_date:
            cols += ", boundary_date TEXT"
        conn.execute(f"CREATE TABLE history_boundaries ({cols})")
        if with_date:
            conn.execute("INSERT INTO history_boundaries VALUES ('LISTING_START', '2020-02-03')")


def _columns(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(history_boundaries)")}


def test_legacy_schema_is_repaired_without_losing_candles(tmp_path: Path) -> None:
    db = tmp_path / "LEGACY.db"
    _legacy_db(db)

    assert compat.ensure_history_boundaries(db) is True
    assert {"boundary_type", "boundary_date", "verified", "verified_at"} <= _columns(db)
    assert (tmp_path / (db.name + compat._BACKUP_SUFFIX)).is_file()

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM history_boundaries").fetchone()[0] == 0

    # The engine can now read boundaries; no date was invented.
    candle_db = engine.CandleDB(str(db))
    candle_db.connect()
    assert candle_db.get_boundary("LISTING_START") is None
    earliest = candle_db.earliest()
    assert earliest is not None
    assert earliest.date().isoformat() == "2019-01-02"
    candle_db.close()


def test_migration_is_idempotent_and_preserves_a_valid_boundary(tmp_path: Path) -> None:
    db = tmp_path / "PARTIAL.db"
    _legacy_db(db, with_date=True)

    assert compat.ensure_history_boundaries(db) is True
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT boundary_date, verified FROM history_boundaries "
            "WHERE boundary_type='LISTING_START'"
        ).fetchone()
    assert row[0] == "2020-02-03"
    assert row[1] == 0  # unverified is honest for a legacy row

    backup = tmp_path / (db.name + compat._BACKUP_SUFFIX)
    assert backup.is_file()
    assert compat.ensure_history_boundaries(db) is False
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_directory_helper_reports_honest_counts(tmp_path: Path) -> None:
    _legacy_db(tmp_path / "A.db")
    _legacy_db(tmp_path / "B.db", with_date=True)
    summary = compat.ensure_download_schema(str(tmp_path))
    assert summary["checked"] == 2
    assert len(summary["repaired"]) == 2
    assert summary["failed"] == []
