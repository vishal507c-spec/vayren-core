"""CandleDB — one symbol's SQLite database (preserved from the original engine).

The schema is identical to the original engine and stays compatible with
03_market's ``OhlcvCandleDatabase``: table ``ohlcv`` keyed by ``candle_time``,
plus the ``non_trading`` and ``history_boundaries`` tables. Writes are
idempotent (``INSERT OR IGNORE``), so an interrupted run resumes from the
database state without any progress files.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from data.native_download import candle_time, candle_time_for, filename_token

log = logging.getLogger("HistDownloadEngine")

_OHLCV_DDL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    candle_time  TEXT PRIMARY KEY,
    open         REAL    NOT NULL,
    high         REAL    NOT NULL,
    low          REAL    NOT NULL,
    close        REAL    NOT NULL,
    volume       INTEGER NOT NULL
);
"""
_OHLCV_IDX = "CREATE INDEX IF NOT EXISTS idx_ct ON ohlcv (candle_time);"

_NON_TRADING_DDL = """
CREATE TABLE IF NOT EXISTS non_trading (
    date_str       TEXT    NOT NULL,
    interval       TEXT    NOT NULL,
    confirmed_at   TEXT    NOT NULL,
    reason         TEXT    NOT NULL DEFAULT 'API_CONFIRMED_NO_DATA',
    before_count   INTEGER,
    expected_count INTEGER,
    PRIMARY KEY (date_str, interval)
);
"""

_HISTORY_BOUNDARIES_DDL = """
CREATE TABLE IF NOT EXISTS history_boundaries (
    boundary_type TEXT PRIMARY KEY,
    boundary_date TEXT NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 1,
    verified_at   TEXT NOT NULL
);
"""

_SQLITE_INT_MAX = (1 << 63) - 1


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    cleaned = candle_time(s)
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def db_path(data_dir: str | Path, symbol: str, _interval: str) -> str:
    """Per-symbol database file path.

    NOTE: the interval label is deliberately not part of the filename —
    preserved exactly as the original engine behaved (one DB per symbol).
    """
    return str(Path(data_dir) / f"{filename_token(symbol)}.db")


class CandleDB:
    """Owns one symbol's .db file: connect, upsert, boundaries, migrations."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_OHLCV_DDL)
        self._conn.execute(_OHLCV_IDX)
        self._conn.execute(_NON_TRADING_DDL)
        self._conn.execute(_HISTORY_BOUNDARIES_DDL)
        self._migrate_non_trading_schema()
        self._conn.commit()

    def _migrate_non_trading_schema(self) -> None:
        assert self._conn is not None
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(non_trading)").fetchall()}
        if "interval" not in cols:
            try:
                self._conn.execute(
                    "ALTER TABLE non_trading ADD COLUMN interval TEXT NOT NULL DEFAULT ''"
                )
                self._conn.execute(
                    "ALTER TABLE non_trading ADD COLUMN reason TEXT NOT NULL "
                    "DEFAULT 'API_CONFIRMED_NO_DATA'"
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS non_trading_v9 (
                        date_str       TEXT    NOT NULL,
                        interval       TEXT    NOT NULL DEFAULT '',
                        confirmed_at   TEXT    NOT NULL,
                        reason         TEXT    NOT NULL DEFAULT 'API_CONFIRMED_NO_DATA',
                        before_count   INTEGER,
                        expected_count INTEGER,
                        PRIMARY KEY (date_str, interval)
                    )
                    """
                )
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO non_trading_v9
                        (date_str, interval, confirmed_at, reason)
                    SELECT date_str, '', confirmed_at, 'API_CONFIRMED_NO_DATA'
                    FROM non_trading
                    """
                )
                self._conn.execute("DROP TABLE IF EXISTS non_trading")
                self._conn.execute("ALTER TABLE non_trading_v9 RENAME TO non_trading")
                self._conn.commit()
                return
            except Exception as exc:
                log.warning(f"[CandleDB] non_trading V9 migration: {exc}")
            cols = {
                row[1] for row in self._conn.execute("PRAGMA table_info(non_trading)").fetchall()
            }
        for col, ddl in [
            ("before_count", "ALTER TABLE non_trading ADD COLUMN before_count   INTEGER"),
            ("expected_count", "ALTER TABLE non_trading ADD COLUMN expected_count INTEGER"),
        ]:
            if col not in cols:
                try:
                    self._conn.execute(ddl)
                    self._conn.commit()
                except Exception as exc:
                    log.warning(f"[CandleDB] V9.1 migration '{col}': {exc}")

    # ── History boundary helpers ─────────────────────────────────────────────

    def get_boundary(self, boundary_type: str) -> dict[str, Any] | None:
        """Return {'boundary_date', 'verified', 'verified_at'} or None."""
        assert self._conn is not None
        r = self._conn.execute(
            "SELECT boundary_date, verified, verified_at "
            "FROM history_boundaries WHERE boundary_type = ?",
            (boundary_type,),
        ).fetchone()
        if r is None:
            return None
        return {"boundary_date": r[0], "verified": r[1], "verified_at": r[2]}

    def set_boundary(self, boundary_type: str, boundary_date: str, verified: int = 1) -> None:
        """Upsert a history boundary record."""
        assert self._conn is not None
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            "INSERT OR REPLACE INTO history_boundaries "
            "(boundary_type, boundary_date, verified, verified_at) "
            "VALUES (?, ?, ?, ?)",
            (boundary_type, boundary_date, verified, now_str),
        )
        self._conn.commit()
        log.info(
            f"[CandleDB] Boundary written: type={boundary_type!r} "
            f"date={boundary_date!r} verified={verified}"
        )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def earliest(self) -> datetime | None:
        assert self._conn is not None
        r = self._conn.execute("SELECT MIN(candle_time) FROM ohlcv").fetchone()
        return parse_dt(r[0]) if r and r[0] else None

    def latest(self) -> datetime | None:
        assert self._conn is not None
        r = self._conn.execute("SELECT MAX(candle_time) FROM ohlcv").fetchone()
        return parse_dt(r[0]) if r and r[0] else None

    def count(self) -> int:
        assert self._conn is not None
        return self._conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]

    def trading_day_count(self) -> int:
        assert self._conn is not None
        r = self._conn.execute("SELECT COUNT(DISTINCT DATE(candle_time)) FROM ohlcv").fetchone()
        return r[0] if r and r[0] else 0

    def corruption_count(self) -> int:
        try:
            assert self._conn is not None
            return self._conn.execute(
                "SELECT COUNT(*) FROM ohlcv "
                "WHERE open IS NULL OR high IS NULL OR low IS NULL "
                "    OR close IS NULL OR volume IS NULL"
            ).fetchone()[0]
        except Exception:
            return 0

    def upsert(self, candles: list) -> int:
        """INSERT OR IGNORE; returns the number of newly inserted rows."""
        if not candles:
            return 0
        assert self._conn is not None
        rows: list[tuple] = []
        for c in candles:
            try:
                date = c["date"]
                ts = candle_time_for(date) if isinstance(date, datetime) else candle_time(str(date))
                vol = int(c["volume"])
                if vol > _SQLITE_INT_MAX:
                    vol = _SQLITE_INT_MAX
                elif vol < 0:
                    vol = 0
                rows.append(
                    (
                        ts,
                        float(c["open"]),
                        float(c["high"]),
                        float(c["low"]),
                        float(c["close"]),
                        vol,
                    )
                )
            except Exception as e:
                log.warning(f"Bad candle skipped: {e}")
        if not rows:
            return 0
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO ohlcv "
            "(candle_time,open,high,low,close,volume) VALUES (?,?,?,?,?,?)",
            rows,
        )
        self._conn.commit()
        return self._conn.total_changes - before

    def remove_duplicates(self) -> int:
        before = self.count()
        assert self._conn is not None
        self._conn.execute(
            """
            DELETE FROM ohlcv WHERE rowid NOT IN (
                SELECT MIN(rowid) FROM ohlcv GROUP BY candle_time
            )
            """
        )
        self._conn.commit()
        return before - self.count()

    def remove_corruption(self) -> int:
        n = self.corruption_count()
        if n:
            assert self._conn is not None
            self._conn.execute(
                "DELETE FROM ohlcv "
                "WHERE open IS NULL OR high IS NULL OR low IS NULL "
                "    OR close IS NULL OR volume IS NULL"
            )
            self._conn.commit()
        return n

    def migrate_normalise_timestamps(self) -> int:
        assert self._conn is not None
        bad_rows = self._conn.execute(
            "SELECT candle_time FROM ohlcv "
            "WHERE substr(candle_time, 18, 2) != '00' "
            "   OR instr(candle_time, 'T') > 0"
        ).fetchall()
        if not bad_rows:
            return 0
        updates: list[tuple[str, str]] = []
        for (raw_ts,) in bad_rows:
            try:
                normalised = candle_time(raw_ts)
                if normalised != raw_ts:
                    updates.append((normalised, raw_ts))
            except Exception as exc:
                log.warning(f"[Migration] Cannot normalise {raw_ts!r}: {exc}")
        if not updates:
            return 0
        replaced = 0
        try:
            self._conn.execute("BEGIN")
            for normalised, raw_ts in updates:
                row = self._conn.execute(
                    "SELECT open, high, low, close, volume FROM ohlcv WHERE candle_time = ?",
                    (raw_ts,),
                ).fetchone()
                if row is None:
                    continue
                self._conn.execute(
                    "INSERT OR IGNORE INTO ohlcv "
                    "(candle_time, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (normalised, row["open"], row["high"], row["low"], row["close"], row["volume"]),
                )
                self._conn.execute("DELETE FROM ohlcv WHERE candle_time = ?", (raw_ts,))
                replaced += 1
            self._conn.execute("COMMIT")
        except Exception as exc:
            self._conn.execute("ROLLBACK")
            log.error(f"[Migration] Rolled back: {exc}")
            return 0
        if replaced:
            log.info(f"[Migration] Normalised {replaced} candle_time row(s).")
        return replaced
