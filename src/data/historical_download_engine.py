# pyright: reportOptionalMemberAccess=false, reportArgumentType=false
# =============================================================================
#  HISTORICAL DOWNLOAD ENGINE  —  MODE 1
#  ─────────────────────────────────────────────────────────────────────────────
#  PURPOSE : Download historical OHLCV data only.
#            Detects NOT_STARTED / PARTIAL_DOWNLOAD symbols and fills missing
#            head / tail / full-history coverage.
#
#  MUST NEVER DO:
#    ✗  Gap Detection / Day Coverage Audit
#    ✗  Repair or Repair Queue Creation
#    ✗  Verification
#    ✗  Non-Trading Validation
#
#  ARCHITECTURE RULE — CANDLE DATABASES ARE THE ONLY SOURCE OF TRUTH
#    ✓  Candle databases  (SYMBOL_INTERVAL.db — ohlcv + non_trading tables)
#    ✓  token.json        (auth token only — DISPOSABLE)
#    ✓  .lock file        (PID file — DISPOSABLE)
#    ✓  Log files         (observability only — DISPOSABLE)
#
#    ✗  engine.db / work_queue / progress files / audit files / repair files
#       symbol status / completion flags — FORBIDDEN
#
#  RECOVERY GUARANTEE
#    Deleting engine.lock, logs, or any metadata file NEVER loses progress.
#    Engine reconstructs complete state from candle DBs alone on every startup.
#
#  STARTUP SEQUENCE (every run, no exceptions)
#    1. Scan all candle DBs → derive SymbolInfo
#    2. Build in-memory download queue from scan
#    3. Execute queue
#    4. Repeat until all symbols reach DOWNLOAD_COMPLETE or MAX_PASSES reached
#
#  SAFETY RULES (inherited unchanged from V9.1)
#    Rule 1  – Market hours blocked (09:15–15:30 IST)
#    Rule 3  – Randomised delays + exponential backoff
#    Rule 4  – Emergency stop on 3 consecutive 429s
#    Rule 5  – Sequential requests only
#    Rule 6  – Incremental updates (never re-download healthy data)
#    Rule 7  – IST time gate at startup
#    Rule 8  – Hard stop on auth failure
#    Rule 11 – Global throttle (max 2 req/s)
#    Rule 12 – Finish one symbol fully before next
#    Rule 13 – Real Data Coverage Mode
# =============================================================================

import csv as _csv
import logging
import os
import platform
import random
import re
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Protocol, Set, Tuple

# ── Broker boundary (the ONLY changed area of this file) ───────────────────
# The original file performed its own Zerodha Selenium auto-login here. That
# is the single behaviour changed by this integration: authentication now
# comes from the broker CONNECTED in VAYREN's SYSTEM UI (see AuthEngine below).
# No download logic, database handling, recovery rule or execution sequence
# below this line was altered.
class KiteConnect(Protocol):
    """The two-call Kite surface consumed by FetchEngine / InstrumentResolver."""

    def instruments(self, exchange: str = "NSE") -> List[Dict]: ...

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> List[Dict]: ...


# =============================================================================
#  CONFIG
# =============================================================================

# Embedded credentials are deliberately NOT copied into the application.
# Authentication is supplied by the broker CONNECTED in VAYREN's SYSTEM UI:
# see AuthEngine below (the only behavioural change to this engine).
DATA_FOLDER = "D:\\ZerodhaTradingData"

LOCK_FILE   = os.path.join(DATA_FOLDER, "hist_engine.lock")

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
SYMBOLS_CSV   = os.path.join(SCRIPT_DIR, "symbols.csv")
LOGS_DIR      = os.path.join(SCRIPT_DIR, "logs")

CHUNK_DAYS        = 200
MAX_HISTORY_YEARS = 10
MAX_RETRIES       = 5
RETRY_DELAY_S     = 5

CHUNK_DELAY_MIN  = 1.0
CHUNK_DELAY_MAX  = 2.0
SYMBOL_DELAY_MIN = 2.0
SYMBOL_DELAY_MAX = 4.0

MAX_CONSECUTIVE_429 = 3

MARKET_OPEN_H,  MARKET_OPEN_M  = 9,  15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 40
IST = timezone(timedelta(hours=5, minutes=30))

MIN_INTER_CALL_SECONDS = 0.5

TAIL_LAG_TOLERANCE_DAYS     = 5
HEAD_TOLERANCE_TRADING_DAYS = 5

LOCK_STALE_SECONDS = 120

INTERVAL_LABEL: Dict[str, str] = {
    "minute":   "1m",
    "5minute":  "5m",
    "15minute": "15m",
    "30minute": "30m",
    "60minute": "60m",
}

INTERVAL_MINUTES: Dict[str, int] = {
    "minute":   1,
    "5minute":  5,
    "15minute": 15,
    "30minute": 30,
    "60minute": 60,
}

NSE_HOLIDAYS: Set[str] = {
    # 2024
    "2024-01-22","2024-01-26","2024-03-08","2024-03-25","2024-03-29",
    "2024-04-11","2024-04-14","2024-04-17","2024-04-21","2024-05-01",
    "2024-05-23","2024-06-17","2024-07-17","2024-08-15","2024-10-02",
    "2024-10-12","2024-10-14","2024-11-01","2024-11-15","2024-11-20",
    "2024-12-25",
    # 2025
    "2025-01-26","2025-02-26","2025-03-14","2025-03-31","2025-04-10",
    "2025-04-14","2025-04-18","2025-04-21","2025-05-01","2025-06-07",
    "2025-07-06","2025-08-15","2025-08-27","2025-10-02","2025-10-20",
    "2025-10-23","2025-11-05","2025-12-25",
    # 2026
    "2026-01-26","2026-03-03","2026-03-20","2026-04-02","2026-04-03",
    "2026-04-14","2026-05-01","2026-06-26","2026-07-25","2026-08-15",
    "2026-09-16","2026-10-02","2026-11-09","2026-11-14","2026-12-25",
}

_TOKEN_EXPIRED = object()
_RATE_LIMITED  = object()

W = 65  # console width


# =============================================================================
#  LOGGING
# =============================================================================

def _setup_logger() -> logging.Logger:
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(
        LOGS_DIR,
        datetime.now().strftime("%Y-%m-%d") + "_hist_download.log"
    )
    logger = logging.getLogger("HistDownloadEngine")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        logger.handlers.clear()
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt); ch.setFormatter(fmt)
    logger.addHandler(fh);  logger.addHandler(ch)
    return logger

log = _setup_logger()


# =============================================================================
#  GLOBAL API THROTTLE
# =============================================================================

class _Throttle:
    def __init__(self):
        self._last: float = 0.0

    def wait(self):
        gap = MIN_INTER_CALL_SECONDS - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

_throttle = _Throttle()


# =============================================================================
#  TIME / CALENDAR HELPERS
# =============================================================================

def ist_now() -> datetime:
    return datetime.now(tz=IST)

def is_market_open() -> bool:
    n = ist_now()
    if n.weekday() >= 5:
        return False
    mo = n.replace(hour=MARKET_OPEN_H,  minute=MARKET_OPEN_M,  second=0, microsecond=0)
    mc = n.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0)
    return mo <= n <= mc

def is_trading_day(d: datetime) -> bool:
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in NSE_HOLIDAYS

def target_start_dt() -> datetime:
    return (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=MAX_HISTORY_YEARS * 365))

def today_end_dt() -> datetime:
    return datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)

def count_trading_days(d1: datetime, d2: datetime) -> int:
    count, cur = 0, d1
    while cur < d2:
        if is_trading_day(cur):
            count += 1
        cur += timedelta(days=1)
    return count

def dt_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# =============================================================================
#  TIMESTAMP NORMALISATION
# =============================================================================

def normalise_ts_str(raw: str) -> str:
    s = raw[:19]
    s = s.replace("T", " ")
    if len(s) == 19:
        s = s[:17] + "00"
    return s

def normalise_ts_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:00")

def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    cleaned = normalise_ts_str(s)
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def db_path(symbol: str, interval: str) -> str:
    label = INTERVAL_LABEL.get(interval, interval)
    safe  = re.sub(r"[^A-Za-z0-9_]", "", symbol)
    return os.path.join(DATA_FOLDER, f"{safe}.db")


# =============================================================================
#  DOWNLOAD STATE  —  DERIVED ONLY, NEVER STORED
# =============================================================================

class DLState(Enum):
    """
    Download-only states. Derived from candle DB scan every run.
    No audit / repair / verification states here — those belong to gap_repair_engine.
    """
    NOT_STARTED       = 1   # No .db file or file is empty
    PARTIAL_DOWNLOAD  = 2   # Head or tail coverage missing
    DOWNLOAD_COMPLETE = 3   # Full historical range present


@dataclass
class SymbolInfo:
    symbol:       str
    interval:     str
    state:        DLState
    earliest:     Optional[datetime]
    latest:       Optional[datetime]
    row_count:    int
    trading_days: int
    missing_head: bool = False
    missing_tail: bool = False
    coverage_pct: float = 0.0
    listing_start_verified: bool = False   # True when LISTING_START boundary confirmed


# =============================================================================
#  ENGINE LOCK
# =============================================================================

class EngineLock:
    def __init__(self, path: str = LOCK_FILE):
        self._path = path
        self._pid  = os.getpid()

    def try_acquire(self) -> bool:
        existing = self._read()
        if existing:
            other_pid  = existing.get("pid", 0)
            other_host = existing.get("hostname", "")
            hb_str     = existing.get("heartbeat", "")

            if other_pid == self._pid and other_host == platform.node():
                self._write(); return True

            try:
                hb_dt   = datetime.fromisoformat(hb_str)
                age_sec = (datetime.now() - hb_dt).total_seconds()
            except Exception:
                age_sec = LOCK_STALE_SECONDS + 1

            pid_alive = False
            if other_host == platform.node():
                pid_alive = _pid_alive(other_pid)

            if not pid_alive or age_sec > LOCK_STALE_SECONDS:
                log.warning(f"[Lock] Stale lock (pid={other_pid}, age={age_sec:.0f}s). Clearing.")
                self._clear()
            else:
                log.error(f"[Lock] Engine already running (pid={other_pid}). Exiting.")
                return False

        self._write()
        log.info(f"[Lock] Acquired (pid={self._pid})")
        return True

    def release(self):
        self._clear()
        log.info("[Lock] Released.")

    def heartbeat(self):
        try:
            existing = self._read() or {}
            existing["heartbeat"] = datetime.now().isoformat()
            with open(self._path, "w") as f:
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")
        except Exception:
            pass

    def _write(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        now = datetime.now().isoformat()
        with open(self._path, "w") as f:
            f.write(f"pid={self._pid}\n")
            f.write(f"hostname={platform.node()}\n")
            f.write(f"started={now}\n")
            f.write(f"heartbeat={now}\n")

    def _read(self) -> Optional[Dict[str, str]]:
        if not os.path.isfile(self._path):
            return None
        try:
            data = {}
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip()
            return data if data else None
        except Exception:
            return None

    def _clear(self):
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass
        except Exception:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        if platform.system() == "Windows":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


# =============================================================================
#  CANDLE DATABASE
# =============================================================================

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


class CandleDB:
    """
    Owns one symbol's .db file.
    Historical Download Engine only uses: connect, upsert, earliest, latest,
    count, trading_day_count, remove_duplicates, remove_corruption,
    migrate_normalise_timestamps.
    """

    def __init__(self, path: str):
        self._path = path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
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

    def _migrate_non_trading_schema(self):
        cols = {row[1] for row in
                self._conn.execute("PRAGMA table_info(non_trading)").fetchall()}
        if "interval" not in cols:
            try:
                self._conn.execute(
                    "ALTER TABLE non_trading ADD COLUMN interval TEXT NOT NULL DEFAULT ''"
                )
                self._conn.execute(
                    "ALTER TABLE non_trading ADD COLUMN reason TEXT NOT NULL "
                    "DEFAULT 'API_CONFIRMED_NO_DATA'"
                )
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS non_trading_v9 (
                        date_str       TEXT    NOT NULL,
                        interval       TEXT    NOT NULL DEFAULT '',
                        confirmed_at   TEXT    NOT NULL,
                        reason         TEXT    NOT NULL DEFAULT 'API_CONFIRMED_NO_DATA',
                        before_count   INTEGER,
                        expected_count INTEGER,
                        PRIMARY KEY (date_str, interval)
                    )
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO non_trading_v9
                        (date_str, interval, confirmed_at, reason)
                    SELECT date_str, '', confirmed_at, 'API_CONFIRMED_NO_DATA'
                    FROM non_trading
                """)
                self._conn.execute("DROP TABLE IF EXISTS non_trading")
                self._conn.execute(
                    "ALTER TABLE non_trading_v9 RENAME TO non_trading"
                )
                self._conn.commit()
                return
            except Exception as exc:
                log.warning(f"[CandleDB] non_trading V9 migration: {exc}")
            cols = {row[1] for row in
                    self._conn.execute("PRAGMA table_info(non_trading)").fetchall()}
        for col, ddl in [
            ("before_count",   "ALTER TABLE non_trading ADD COLUMN before_count   INTEGER"),
            ("expected_count", "ALTER TABLE non_trading ADD COLUMN expected_count INTEGER"),
        ]:
            if col not in cols:
                try:
                    self._conn.execute(ddl)
                    self._conn.commit()
                except Exception as exc:
                    log.warning(f"[CandleDB] V9.1 migration '{col}': {exc}")

    # ── History Boundary helpers ───────────────────────────────────────────────

    def get_boundary(self, boundary_type: str) -> Optional[Dict]:
        """Return {'boundary_date': str, 'verified': int, 'verified_at': str} or None."""
        r = self._conn.execute(
            "SELECT boundary_date, verified, verified_at "
            "FROM history_boundaries WHERE boundary_type = ?",
            (boundary_type,)
        ).fetchone()
        if r is None:
            return None
        return {"boundary_date": r[0], "verified": r[1], "verified_at": r[2]}

    def set_boundary(self, boundary_type: str, boundary_date: str, verified: int = 1):
        """Upsert a history boundary record."""
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

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def earliest(self) -> Optional[datetime]:
        r = self._conn.execute("SELECT MIN(candle_time) FROM ohlcv").fetchone()
        return parse_dt(r[0]) if r and r[0] else None

    def latest(self) -> Optional[datetime]:
        r = self._conn.execute("SELECT MAX(candle_time) FROM ohlcv").fetchone()
        return parse_dt(r[0]) if r and r[0] else None

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]

    def trading_day_count(self) -> int:
        r = self._conn.execute(
            "SELECT COUNT(DISTINCT DATE(candle_time)) FROM ohlcv"
        ).fetchone()
        return r[0] if r and r[0] else 0

    def corruption_count(self) -> int:
        try:
            return self._conn.execute(
                "SELECT COUNT(*) FROM ohlcv "
                "WHERE open IS NULL OR high IS NULL OR low IS NULL "
                "    OR close IS NULL OR volume IS NULL"
            ).fetchone()[0]
        except Exception:
            return 0

    def upsert(self, candles: list) -> int:
        if not candles:
            return 0
        rows = []
        for c in candles:
            try:
                if isinstance(c["date"], datetime):
                    ts = normalise_ts_dt(c["date"])
                else:
                    ts = normalise_ts_str(str(c["date"]))
                vol = int(c["volume"])
                # SQLite INTEGER is signed 64-bit; clamp oversized values
                _SQLITE_INT_MAX = (1 << 63) - 1
                if vol > _SQLITE_INT_MAX:
                    vol = _SQLITE_INT_MAX
                elif vol < 0:
                    vol = 0
                rows.append((ts, float(c["open"]), float(c["high"]),
                             float(c["low"]), float(c["close"]), vol))
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
        self._conn.execute("""
            DELETE FROM ohlcv WHERE rowid NOT IN (
                SELECT MIN(rowid) FROM ohlcv GROUP BY candle_time
            )
        """)
        self._conn.commit()
        return before - self.count()

    def remove_corruption(self) -> int:
        n = self._conn.execute(
            "SELECT COUNT(*) FROM ohlcv "
            "WHERE open IS NULL OR high IS NULL OR low IS NULL "
            "    OR close IS NULL OR volume IS NULL"
        ).fetchone()[0]
        if n:
            self._conn.execute(
                "DELETE FROM ohlcv "
                "WHERE open IS NULL OR high IS NULL OR low IS NULL "
                "    OR close IS NULL OR volume IS NULL"
            )
            self._conn.commit()
        return n

    def migrate_normalise_timestamps(self) -> int:
        bad_rows = self._conn.execute(
            "SELECT candle_time FROM ohlcv "
            "WHERE substr(candle_time, 18, 2) != '00' "
            "   OR instr(candle_time, 'T') > 0"
        ).fetchall()
        if not bad_rows:
            return 0
        updates: List[Tuple[str, str]] = []
        for (raw_ts,) in bad_rows:
            try:
                normalised = normalise_ts_str(raw_ts)
                if normalised != raw_ts:
                    updates.append((normalised, raw_ts))
            except Exception as exc:
                log.warning(f"[Migration] Cannot normalise {raw_ts!r}: {exc}")
        if not updates:
            return 0
        try:
            self._conn.execute("BEGIN")
            replaced = 0
            for (normalised, raw_ts) in updates:
                row = self._conn.execute(
                    "SELECT open, high, low, close, volume FROM ohlcv "
                    "WHERE candle_time = ?", (raw_ts,)
                ).fetchone()
                if row is None:
                    continue
                self._conn.execute(
                    "INSERT OR IGNORE INTO ohlcv "
                    "(candle_time, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (normalised, row["open"], row["high"], row["low"],
                     row["close"], row["volume"])
                )
                self._conn.execute(
                    "DELETE FROM ohlcv WHERE candle_time = ?", (raw_ts,)
                )
                replaced += 1
            self._conn.execute("COMMIT")
        except Exception as exc:
            self._conn.execute("ROLLBACK")
            log.error(f"[Migration] Rolled back: {exc}")
            return 0
        if replaced:
            log.info(f"[Migration] Normalised {replaced} candle_time row(s).")
        return replaced


# =============================================================================
#  DATABASE SCANNER  —  DOWNLOAD STATE ONLY
# =============================================================================

class DatabaseScanner:
    """
    Derives DLState from candle DB scan.
    NEVER derives audit / repair / gap states.
    """

    def __init__(self):
        # Track symbols already logged for LISTING_START suppression
        # so the message prints only once per engine run (startup scan).
        self._logged_listing_starts: Set[str] = set()

    def scan(self, symbols: List[Dict]) -> List[SymbolInfo]:
        infos: List[SymbolInfo] = []
        for sym in symbols:
            info = self._scan_one(sym["trading_symbol"], sym["interval"])
            infos.append(info)
        return infos

    def _scan_one(self, symbol: str, interval: str) -> SymbolInfo:
        path = db_path(symbol, interval)

        if not os.path.isfile(path):
            return SymbolInfo(
                symbol=symbol, interval=interval,
                state=DLState.NOT_STARTED,
                earliest=None, latest=None,
                row_count=0, trading_days=0,
            )

        cdb = CandleDB(path)
        cdb.connect()
        earliest     = cdb.earliest()
        latest       = cdb.latest()
        row_count    = cdb.count()
        trading_days = cdb.trading_day_count()
        corrupt      = cdb.corruption_count()

        # Load LISTING_START boundary (if any)
        listing_boundary = cdb.get_boundary("LISTING_START")

        cdb.close()

        if earliest is None or latest is None or row_count == 0:
            return SymbolInfo(
                symbol=symbol, interval=interval,
                state=DLState.NOT_STARTED,
                earliest=None, latest=None,
                row_count=row_count, trading_days=0,
            )

        target    = target_start_dt()
        today_end = today_end_dt()
        target_td = MAX_HISTORY_YEARS * 252
        cov_pct   = min(100.0, trading_days / target_td * 100) if target_td else 0.0

        head_td_gap  = count_trading_days(target, earliest)
        missing_head = head_td_gap > HEAD_TOLERANCE_TRADING_DAYS

        # ── Listing-boundary check ────────────────────────────────────────────
        # If a verified LISTING_START boundary exists whose date is <= the
        # earliest candle date, head coverage is considered verified.
        # Using <= (not ==) makes the boundary survive cleanup operations
        # (duplicate removal, timestamp normalisation, data corrections) that
        # may shift the earliest candle forward by one or a few days without
        # invalidating the fact that the listing start was already confirmed.
        listing_start_verified = False
        if missing_head and listing_boundary:
            try:
                boundary_dt = datetime.strptime(
                    listing_boundary["boundary_date"], "%Y-%m-%d"
                ).date()
            except Exception:
                boundary_dt = None
            if (
                listing_boundary["verified"] == 1
                and boundary_dt is not None
                and boundary_dt <= earliest.date()
            ):
                missing_head           = False
                listing_start_verified = True
                _log_key = f"{symbol}/{interval}"
                if _log_key not in self._logged_listing_starts:
                    self._logged_listing_starts.add(_log_key)
                    log.debug(
                        f"[Scanner] {symbol}/{interval}: LISTING_START boundary "
                        f"verified at {listing_boundary['boundary_date']} "
                        f"(earliest candle {earliest.date()}) — "
                        "suppressing head-gap download."
                    )
        # ─────────────────────────────────────────────────────────────────────

        days_tail    = (today_end.date() - latest.date()).days
        missing_tail = days_tail > TAIL_LAG_TOLERANCE_DAYS

        if missing_head or missing_tail or corrupt > 0:
            state = DLState.PARTIAL_DOWNLOAD
        else:
            state = DLState.DOWNLOAD_COMPLETE

        return SymbolInfo(
            symbol=symbol, interval=interval,
            state=state,
            earliest=earliest, latest=latest,
            row_count=row_count, trading_days=trading_days,
            missing_head=missing_head, missing_tail=missing_tail,
            coverage_pct=cov_pct,
            listing_start_verified=listing_start_verified,
        )


# =============================================================================
#  IN-MEMORY DOWNLOAD QUEUE
# =============================================================================

@dataclass
class DownloadJob:
    symbol:   str
    interval: str
    from_dt:  datetime
    to_dt:    datetime
    reason:   str

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.interval}"


class DownloadQueue:
    """
    Pure in-memory queue. Never touches disk.
    Rebuilt from DB scan on every startup — losing it costs nothing.

    Historical Remaining Coverage Rule:
        Target Start = MAX_HISTORY_YEARS back from today
        If earliest DB candle is later than target → head gap exists.
        Only missing ranges are queued — already downloaded data is NEVER re-fetched.
    """

    def __init__(self):
        self._jobs: List[DownloadJob] = []

    def build_from_scan(
        self, infos: List[SymbolInfo], token_map: Dict[str, int]
    ):
        self._jobs = []
        today_end = today_end_dt()
        target    = target_start_dt()

        # Priority: PARTIAL first, then NOT_STARTED, skip DOWNLOAD_COMPLETE
        priority_map = {
            DLState.PARTIAL_DOWNLOAD: 1,
            DLState.NOT_STARTED:      2,
            DLState.DOWNLOAD_COMPLETE: 3,
        }
        sorted_infos = sorted(infos, key=lambda si: priority_map.get(si.state, 9))

        for si in sorted_infos:
            if si.state == DLState.DOWNLOAD_COMPLETE:
                # Nothing to download — skip entirely
                continue

            if token_map.get(si.symbol) is None:
                log.warning(f"[Queue] {si.symbol} not in NSE instrument map — skip.")
                continue

            if si.state == DLState.NOT_STARTED:
                # Full historical range
                self._jobs.append(DownloadJob(
                    symbol=si.symbol, interval=si.interval,
                    from_dt=target, to_dt=today_end,
                    reason="NOT_STARTED: full history",
                ))

            elif si.state == DLState.PARTIAL_DOWNLOAD:
                # Head gap: target_start → earliest DB candle
                # Skip entirely when LISTING_START boundary already verified.
                if si.missing_head and not si.listing_start_verified:
                    if si.earliest:
                        self._jobs.append(DownloadJob(
                            symbol=si.symbol, interval=si.interval,
                            from_dt=target,
                            to_dt=si.earliest - timedelta(minutes=1),
                            reason="PARTIAL: missing head coverage",
                        ))
                    else:
                        self._jobs.append(DownloadJob(
                            symbol=si.symbol, interval=si.interval,
                            from_dt=target, to_dt=today_end,
                            reason="PARTIAL: missing head (no earliest)",
                        ))

                # Tail gap: latest DB candle → today
                if si.missing_tail:
                    if si.latest:
                        self._jobs.append(DownloadJob(
                            symbol=si.symbol, interval=si.interval,
                            from_dt=si.latest + timedelta(minutes=1),
                            to_dt=today_end,
                            reason="PARTIAL: missing tail coverage",
                        ))
                    else:
                        self._jobs.append(DownloadJob(
                            symbol=si.symbol, interval=si.interval,
                            from_dt=target, to_dt=today_end,
                            reason="PARTIAL: missing tail (no latest)",
                        ))

        log.info(f"[DownloadQueue] Built {len(self._jobs)} job(s) from scan.")

    def symbols_in_order(self) -> List[Tuple[str, str]]:
        seen: List[Tuple[str, str]] = []
        for job in self._jobs:
            pair = (job.symbol, job.interval)
            if pair not in seen:
                seen.append(pair)
        return seen

    def jobs_for(self, symbol: str, interval: str) -> List[DownloadJob]:
        return [j for j in self._jobs
                if j.symbol == symbol and j.interval == interval]

    def total(self) -> int:
        return len(self._jobs)


# =============================================================================
#  FETCH ENGINE
# =============================================================================

class FetchEngine:

    def __init__(self, kite: KiteConnect):
        self._kite       = kite
        self._consec_429 = 0

    def fetch(self, token: int, interval: str,
              from_dt: datetime, to_dt: datetime, chunk_num: int = 0):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _throttle.wait()
                data = self._kite.historical_data(
                    instrument_token=token,
                    from_date=from_dt, to_date=to_dt,
                    interval=interval, continuous=False, oi=False,
                )
                self._consec_429 = 0
                return data or []

            except Exception as exc:
                msg = str(exc)
                if "TokenException" in msg or "invalid" in msg.lower():
                    log.error(f"[Fetch] Token expired chunk={chunk_num}: {exc}")
                    return _TOKEN_EXPIRED
                if "429" in msg or "rate" in msg.lower():
                    self._consec_429 += 1
                    if self._consec_429 >= MAX_CONSECUTIVE_429:
                        _print_box("⚠  RATE LIMIT — SYNC STOPPED",
                                   f"{self._consec_429} consecutive 429 errors.",
                                   "Wait a few minutes, then re-run.")
                        return _RATE_LIMITED
                    time.sleep(RETRY_DELAY_S * (2 ** attempt))
                    continue
                self._consec_429 = 0
                wait = RETRY_DELAY_S * attempt
                log.warning(f"[Fetch] attempt={attempt}: {exc} — retry in {wait}s")
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
                else:
                    log.error(f"[Fetch] All retries exhausted chunk={chunk_num}.")
                    return []
        return []


# =============================================================================
#  FORWARD SWEEP  —  CHUNKED DOWNLOAD HELPER
# =============================================================================

def forward_sweep(
    fetcher: FetchEngine,
    candle_db: CandleDB,
    token: int,
    symbol: str,
    interval: str,
    sweep_start: datetime,
    sweep_end: datetime,
    is_head_sweep: bool = False,
) -> Tuple[bool, int, bool]:
    """
    Returns (ok, total_new_candles, all_chunks_zero).

    all_chunks_zero is True only when is_head_sweep=True and every chunk
    returned 0 new candles AND the earliest candle in DB did not change.
    The caller uses this to write the LISTING_START boundary.
    """
    # Count total chunks for progress display
    _chunk_count = 0
    _c = sweep_start
    while _c <= sweep_end:
        _chunk_count += 1
        _c = min(_c + timedelta(days=CHUNK_DAYS), sweep_end) + timedelta(minutes=1)
    total_chunks = max(_chunk_count, 1)

    # Record earliest before sweep (used for boundary verification)
    earliest_before: Optional[datetime] = None
    if is_head_sweep:
        earliest_before = candle_db.earliest()

    chunk     = sweep_start
    total_new = 0
    n         = 0

    while chunk <= sweep_end:
        end    = min(chunk + timedelta(days=CHUNK_DAYS), sweep_end)
        n     += 1
        result = fetcher.fetch(token, interval, chunk, end, n)

        if result is _TOKEN_EXPIRED or result is _RATE_LIMITED:
            return False, total_new, False

        new = 0
        if result:
            new = candle_db.upsert(result)
            total_new += new
            log.info(f"  [{symbol}] chunk {n}: {chunk.date()}→{end.date()} +{new} rows")

        db_total = candle_db.count()
        pct      = n / total_chunks * 100
        print(f"  {n:02d}/{total_chunks:02d} │ "
              f"{chunk.strftime('%Y-%m-%d')}→{end.strftime('%Y-%m-%d')} │ "
              f"+{new:<6} │ DB={db_total:,} │ {pct:.1f}%",
              flush=True)

        chunk = end + timedelta(minutes=1)
        _jitter_sleep(CHUNK_DELAY_MIN, CHUNK_DELAY_MAX)

    # Determine whether the entire head sweep yielded nothing
    all_chunks_zero = False
    if is_head_sweep and total_new == 0:
        earliest_after = candle_db.earliest()
        # Earliest must still match the pre-sweep earliest (i.e. DB did not grow)
        if (
            earliest_before is not None
            and earliest_after is not None
            and earliest_after.date() == earliest_before.date()
        ):
            all_chunks_zero = True
            log.info(
                f"[Sweep] {symbol}/{interval}: head sweep returned 0 new candles. "
                f"Earliest unchanged at {earliest_before.date()}. "
                "Eligible for LISTING_START boundary."
            )

    return True, total_new, all_chunks_zero


# =============================================================================
#  INSTRUMENT RESOLVER
# =============================================================================

class InstrumentResolver:

    def __init__(self, kite: KiteConnect):
        self._kite = kite
        self._map: Dict[str, int] = {}

    def fetch(self):
        log.info("Fetching NSE instruments …")
        _throttle.wait()
        for inst in self._kite.instruments("NSE"):
            sym = inst.get("tradingsymbol", "").strip().upper()
            tok = inst.get("instrument_token")
            if sym and tok:
                self._map[sym] = int(tok)
        log.info(f"Instrument map: {len(self._map)} symbols.")

    def map(self) -> Dict[str, int]:
        return dict(self._map)


# =============================================================================
#  SYMBOLS CSV LOADER
# =============================================================================

def load_symbols() -> List[Dict]:
    if not os.path.exists(SYMBOLS_CSV):
        log.error(f"symbols.csv not found: {SYMBOLS_CSV}")
        sys.exit(1)
    symbols: List[Dict] = []
    with open(SYMBOLS_CSV, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            row = {k.strip(): v.strip() for k, v in row.items()}
            ts  = row.get("trading_symbol", "").strip()
            iv  = row.get("interval", "minute").strip()
            if ts:
                symbols.append({"trading_symbol": ts, "interval": iv})
    if not symbols:
        log.error("symbols.csv is empty.")
        sys.exit(1)
    log.info(f"Loaded {len(symbols)} symbol(s).")
    return symbols


# =============================================================================
#  AUTH ENGINE  —  VAYREN CONNECTED-BROKER SEAM
# =============================================================================
#  The ORIGINAL file authenticated with its own Zerodha credentials via
#  Selenium auto-login. That is the ONLY behaviour changed in this integration:
#  the broker currently authenticated and CONNECTED in VAYREN's SYSTEM UI now
#  supplies the same two-call KiteConnect surface through a broker adapter.
#  Nothing below this seam (scan / queue / fetch retries / sweep / boundaries /
#  execution sequence) was modified.
#
#  No credentials from the original file were copied into the application.
#  The broker manager and its secure credential store are injected by app
#  composition; this module only holds the injected reference.

_broker_context: Optional[Tuple] = None


def configure_broker_context(broker_manager, data_dir: Optional[str] = None) -> None:
    """Inject VAYREN's authoritative broker manager (app composition owns wiring)."""
    global _broker_context
    _broker_context = (broker_manager, data_dir)


class AuthEngine:
    def __init__(self):
        self._kite: Optional[KiteConnect] = None

    def get_kite(self) -> KiteConnect:
        if self._kite:
            return self._kite
        return self._fresh_kite()

    def renew(self) -> KiteConnect:
        log.warning("[Auth] Connected-broker session refresh requested …")
        self._kite = None
        return self._fresh_kite()

    def _fresh_kite(self) -> KiteConnect:
        if _broker_context is None:
            raise RuntimeError(
                "No VAYREN broker context configured — historical download "
                "requires the broker CONNECTED in the SYSTEM UI."
            )
        from data.download_broker_adapter import build_selected_broker_shim
        broker_manager, data_dir = _broker_context
        kite = build_selected_broker_shim(broker_manager, data_dir)
        log.info("[Auth] ✅ Using broker CONNECTED in VAYREN SYSTEM UI.")
        self._kite = kite
        return kite

# =============================================================================
#  CONSOLE HELPERS
# =============================================================================

def _print_box(*lines: str):
    print(f"\n{'=' * W}")
    for line in lines:
        print(f"  {line}")
    print(f"{'=' * W}\n")

def _jitter_sleep(lo: float, hi: float):
    time.sleep(random.uniform(lo, hi))

def _print_current_symbol_header(symbol: str, interval: str):
    lbl = INTERVAL_LABEL.get(interval, interval)
    print()
    print("═══════════════════════════════════════════════════════")
    print()
    print("CURRENT SYMBOL")
    print()
    print(f"  [{symbol}][{lbl}]")
    print()
    print("═══════════════════════════════════════════════════════")
    print()

def _print_download_status(infos: List[SymbolInfo]):
    total    = len(infos)
    complete = sum(1 for si in infos if si.state == DLState.DOWNLOAD_COMPLETE)
    partial  = sum(1 for si in infos if si.state == DLState.PARTIAL_DOWNLOAD)
    pending  = sum(1 for si in infos if si.state == DLState.NOT_STARTED)
    total_c  = sum(si.row_count for si in infos)
    target_td = MAX_HISTORY_YEARS * 252

    print()
    print("════════════════ HISTORICAL DOWNLOAD STATUS ════════════════")
    print()
    print(f"  TOTAL SYMBOLS        : {total}")
    print(f"  DOWNLOAD COMPLETE    : {complete}")
    print(f"  PARTIAL DOWNLOAD     : {partial}")
    print(f"  NOT STARTED          : {pending}")
    print()
    print(f"  TOTAL CANDLES STORED : {total_c:,}")
    print()
    print("═════════════════════════════════════════════════════════════")
    print()

    # Pending detail
    pending_list = [si for si in infos if si.state != DLState.DOWNLOAD_COMPLETE]
    if pending_list:
        print(f"  [PENDING DOWNLOAD] {len(pending_list)}/{total}")
        print()
        for si in sorted(pending_list, key=lambda x: x.symbol):
            lbl    = INTERVAL_LABEL.get(si.interval, si.interval)
            td     = si.trading_days
            miss   = max(0, target_td - td)
            if si.listing_start_verified:
                head_s = "HEAD VERIFIED (LISTING START) "
            else:
                head_s = "HEAD_GAP " if si.missing_head else ""
            tail_s = "TAIL_GAP"  if si.missing_tail else ""
            flags  = (head_s + tail_s).strip() or "FULL_DOWNLOAD"
            print(f"  {si.symbol:<12} [{lbl}] │ TD={td}/{target_td} │ "
                  f"Missing={miss} TD │ {flags}")
        print()

def _print_live_download_summary(infos: List[SymbolInfo]):
    total    = len(infos)
    complete = sum(1 for si in infos if si.state == DLState.DOWNLOAD_COMPLETE)
    partial  = sum(1 for si in infos if si.state == DLState.PARTIAL_DOWNLOAD)
    pending  = sum(1 for si in infos if si.state == DLState.NOT_STARTED)
    total_c  = sum(si.row_count for si in infos)

    print()
    print("═══════════════════════════════════════════════════════")
    print()
    print("LIVE DOWNLOAD SUMMARY")
    print()
    print(f"  Download Complete    : {complete} / {total}")
    print(f"  Partial / Not Started: {partial + pending}")
    print()
    print(f"  Total Candles Stored : {total_c:,}")
    print()
    print("═══════════════════════════════════════════════════════")
    print()


# =============================================================================
#  DB HOUSEKEEPING
# =============================================================================

def cleanup_candle_dbs(infos: List[SymbolInfo]):
    """
    Per-startup housekeeping:
      1. migrate_normalise_timestamps()
      2. remove_duplicates()
      3. remove_corruption()
    """
    total_m = total_d = total_c = 0
    for si in infos:
        path = db_path(si.symbol, si.interval)
        if not os.path.isfile(path):
            continue
        cdb = CandleDB(path)
        cdb.connect()
        m = cdb.migrate_normalise_timestamps()
        d = cdb.remove_duplicates()
        c = cdb.remove_corruption()
        cdb.close()
        total_m += m; total_d += d; total_c += c
        if m: log.info(f"[Cleanup] {si.symbol}: normalised {m} timestamp(s).")
        if d: log.info(f"[Cleanup] {si.symbol}: removed {d} duplicates.")
        if c: log.info(f"[Cleanup] {si.symbol}: removed {c} corrupted rows.")
    parts = []
    if total_m: parts.append(f"{total_m} timestamp(s) normalised")
    if total_d: parts.append(f"{total_d} duplicate(s) removed")
    if total_c: parts.append(f"{total_c} corrupted row(s) removed")
    if parts:
        print(f"  Startup cleanup: {', '.join(parts)}.\n")


# =============================================================================
#  HISTORICAL DOWNLOAD ENGINE  —  MAIN ORCHESTRATOR
# =============================================================================

class HistoricalDownloadEngine:
    """
    MODE 1 — HISTORICAL DOWNLOAD ENGINE

    Execution Flow:
      START
        ↓ Scan Candle DBs
        ↓ Determine Historical Remaining Coverage
        ↓ Build Download Queue
        ↓ Download Missing History
        ↓ Update DB
        ↓ Move To Next Symbol
      END

    NEVER performs: audit, repair, verification, non-trading validation.
    """

    MAX_PASSES = 5

    def __init__(self):
        self._auth:     AuthEngine             = AuthEngine()
        self._lock:     EngineLock             = EngineLock()
        self._scanner:  DatabaseScanner        = DatabaseScanner()
        self._kite:     Optional[KiteConnect]  = None
        self._fetcher:  Optional[FetchEngine]  = None
        self._resolver: Optional[InstrumentResolver] = None
        self._abort     = False
        self._symbols_ref: List[Dict] = []

    def run(self):
        os.makedirs(DATA_FOLDER, exist_ok=True)

        if not self._lock.try_acquire():
            sys.exit(1)

        self._start_heartbeat()
        try:
            self._run_inner()
        finally:
            self._lock.release()

    def _run_inner(self):
        if is_market_open():
            now = ist_now()
            _print_box(
                "⚠  MARKET IS OPEN — HISTORICAL DOWNLOAD DISABLED",
                f"Current IST : {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
                f"Re-run after {MARKET_CLOSE_H:02d}:{MARKET_CLOSE_M:02d} IST.",
            )
            try:
                _syms  = load_symbols()
                _infos = self._scanner.scan(_syms)
                _print_download_status(_infos)
            except Exception:
                pass
            return

        _print_box(
            "AFML HISTORICAL DOWNLOAD ENGINE  —  MODE 1",
            ist_now().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "Purpose  : Download historical OHLCV data only",
            "Recovery : State derived from candle DBs — no progress files",
            f"History  : {MAX_HISTORY_YEARS} years back from today",
            "Note     : Run gap_repair_engine.py separately for gap repair",
        )
        symbols = load_symbols()
        infos = self._scanner.scan(symbols)

        TARGET_TRADING_DAYS = MAX_HISTORY_YEARS * 252

        print()
        print("═" * 100)
        print()
        print("LOCAL DATABASES")
        print()
        print("─" * 100)
        print()

        db_count = 0
        total_candles = 0

        for si in infos:

            if si.row_count <= 0:
                continue

            db_count += 1
            total_candles += si.row_count

            pct = min(
                100.0,
                (si.trading_days / TARGET_TRADING_DAYS) * 100
            )

            bar_width = 25
            filled = int((pct / 100.0) * bar_width)

            bar = "█" * filled + "░" * (bar_width - filled)

            print(
                f"{si.symbol:<12} "
                f"{si.trading_days:>5} trading days   "
                f"{si.row_count:>12,} candles   "
                f"{bar}"
            )

        print()
        print("─" * 100)
        print()

        print(f"Total Databases : {db_count}")
        print(f"Total Candles   : {total_candles:,}")

        print()
        print("═" * 100)
        print()

        print("  Authenticating …", end="", flush=True)
        self._kite = self._auth.get_kite()
        print(" ✓")

        print("  Loading instruments …", end="", flush=True)
        self._resolver = InstrumentResolver(self._kite)
        self._resolver.fetch()
        print(" ✓\n")

        self._fetcher = FetchEngine(self._kite)
        symbols       = load_symbols()
        self._symbols_ref = symbols
        token_map     = self._resolver.map()

        for pass_num in range(1, self.MAX_PASSES + 1):
            log.info(f"[HistDL] === PASS {pass_num}/{self.MAX_PASSES} ===")

            print(f"  Scanning candle databases (pass {pass_num}) …", flush=True)
            infos = self._scanner.scan(symbols)

            cleanup_candle_dbs(infos)

            _print_download_status(infos)

            remaining = [si for si in infos
                         if si.state != DLState.DOWNLOAD_COMPLETE]
            if not remaining:
                print()
                print("  ✔  All symbols DOWNLOAD_COMPLETE.")
                print("     Run gap_repair_engine.py to audit and repair gaps.")
                print()
                log.info("[HistDL] All symbols DOWNLOAD_COMPLETE.")
                return

            queue = DownloadQueue()
            queue.build_from_scan(infos, token_map)

            if queue.total() == 0:
                print("  ✔  No download jobs — all historical ranges covered.")
                return

            print(f"  Download queue: {queue.total()} job(s)\n")

            self._execute_queue(queue, infos, token_map)

            if self._abort:
                print("\n  ⚠  Engine aborted (rate-limit or token). Re-run to continue.")
                break

        # Final scan
        final_infos = self._scanner.scan(symbols)
        _print_download_status(final_infos)

    def _execute_queue(
        self,
        queue:     DownloadQueue,
        infos:     List[SymbolInfo],
        token_map: Dict[str, int],
    ):
        ordered = queue.symbols_in_order()

        for idx, (symbol, interval) in enumerate(ordered):
            if self._abort:
                break

            token = token_map.get(symbol)
            if token is None:
                log.warning(f"[HistDL] {symbol}: token not found — skip.")
                continue

            ok = self._process_symbol(symbol, interval, token, queue)

            if not ok:
                if not self._try_renew_token():
                    self._abort = True
                    break
                ok2 = self._process_symbol(symbol, interval, token, queue)
                if not ok2:
                    self._abort = True
                    break

            # Live summary after every symbol
            fresh_infos = self._scanner.scan(self._symbols_ref)
            _print_live_download_summary(fresh_infos)

            if not self._abort and idx < len(ordered) - 1:
                _jitter_sleep(SYMBOL_DELAY_MIN, SYMBOL_DELAY_MAX)

    def _process_symbol(
        self,
        symbol:   str,
        interval: str,
        token:    int,
        queue:    DownloadQueue,
    ) -> bool:
        """
        Download all missing ranges for one symbol.
        NEVER audits, repairs, or verifies.
        """
        jobs = queue.jobs_for(symbol, interval)
        if not jobs:
            return True

        cdb = CandleDB(db_path(symbol, interval))
        cdb.connect()

        total_new = 0

        try:
            _print_current_symbol_header(symbol, interval)

            for job in jobs:
                print(f"  Reason : {job.reason}")
                print(f"  Range  : {job.from_dt.strftime('%Y-%m-%d')} → "
                      f"{job.to_dt.strftime('%Y-%m-%d')}")
                print()

                # Detect head-gap jobs so we can apply listing-boundary logic
                is_head = "head" in job.reason.lower()

                ok, new_rows, all_chunks_zero = forward_sweep(
                    self._fetcher, cdb, token, symbol, interval,
                    job.from_dt, job.to_dt,
                    is_head_sweep=is_head,
                )
                total_new += new_rows
                self._lock.heartbeat()

                if not ok:
                    print()
                    return False

                # ── Listing boundary: write when head sweep found nothing ──────
                if is_head and all_chunks_zero:
                    earliest_now = cdb.earliest()
                    if earliest_now is not None:
                        boundary_date = earliest_now.strftime("%Y-%m-%d")
                        cdb.set_boundary("LISTING_START", boundary_date, verified=1)
                        print(
                            f"\n  ✔  LISTING_START boundary written: {boundary_date}"
                            f"\n     No historical data exists before earliest candle."
                            f"\n     Future runs will skip head-gap download for this symbol.\n"
                        )
                        log.info(
                            f"[HistDL] {symbol}/{interval}: LISTING_START={boundary_date} stored."
                        )
                # ────────────────────────────────────────────────────────────────

            # Symbol summary
            lbl    = INTERVAL_LABEL.get(interval, interval)
            td     = cdb.trading_day_count()
            total_c = cdb.count()
            target_td = MAX_HISTORY_YEARS * 252
            print()
            print(f"[{symbol}][{lbl}]")
            print()
            print(f"  Downloaded : +{total_new:,} new candles")
            print(f"  DB Total   : {total_c:,} candles")
            print(f"  Trading Days: {td}/{target_td}")
            print()

        finally:
            cdb.close()

        return True

    def _try_renew_token(self) -> bool:
        try:
            new_kite      = self._auth.renew()
            self._kite    = new_kite
            self._fetcher = FetchEngine(new_kite)
            log.info("[HistDL] Token renewed.")
            return True
        except Exception as e:
            log.error(f"[HistDL] Token renewal failed: {e}")
            return False

    def _start_heartbeat(self):
        import threading
        def _beat():
            while True:
                time.sleep(30)
                try:
                    self._lock.heartbeat()
                except Exception:
                    pass
        threading.Thread(target=_beat, daemon=True).start()

# =============================================================================
#  MAIN
# =============================================================================

def main():
    os.makedirs(DATA_FOLDER, exist_ok=True)
    os.makedirs(LOGS_DIR,    exist_ok=True)

    def _sigint(sig, frame):
        print("\n\n  Interrupted. Re-run to resume from DB state.\n")
        sys.exit(0)
    try:
        signal.signal(signal.SIGINT, _sigint)
    except Exception:
        pass

    HistoricalDownloadEngine().run()


if __name__ == "__main__":
    main()
