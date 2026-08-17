"""DatabaseScanner — derives download state from candle DBs (preserved).

State is derived, never stored. Head-gap suppression via verified
LISTING_START boundaries is preserved exactly, including the ``<=`` rule
that lets the boundary survive cleanup operations.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from data.calendar import count_trading_days, target_start_dt, today_end_dt
from data.models import DLState, SymbolInfo
from data.storage.candle_db import CandleDB, db_path

log = logging.getLogger("HistDownloadEngine")


class DatabaseScanner:
    """Derives DLState from candle DB scan. NEVER derives audit/repair states."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._logged_listing_starts: set[str] = set()

    def scan(self, symbols: list[dict[str, Any]]) -> list[SymbolInfo]:
        infos: list[SymbolInfo] = []
        for sym in symbols:
            infos.append(self.scan_one(sym["trading_symbol"], sym["interval"]))
        return infos

    def scan_one(self, symbol: str, interval: str) -> SymbolInfo:
        return self._scan_one(symbol, interval)

    def _scan_one(self, symbol: str, interval: str) -> SymbolInfo:
        settings = self._settings
        path = db_path(settings.data_dir, symbol, interval)

        if not os.path.isfile(path):
            return SymbolInfo(
                symbol=symbol,
                interval=interval,
                state=DLState.NOT_STARTED,
                earliest=None,
                latest=None,
                row_count=0,
                trading_days=0,
            )

        cdb = CandleDB(path)
        cdb.connect()
        earliest = cdb.earliest()
        latest = cdb.latest()
        row_count = cdb.count()
        trading_days = cdb.trading_day_count()
        corrupt = cdb.corruption_count()
        listing_boundary = cdb.get_boundary("LISTING_START")
        cdb.close()

        if earliest is None or latest is None or row_count == 0:
            return SymbolInfo(
                symbol=symbol,
                interval=interval,
                state=DLState.NOT_STARTED,
                earliest=None,
                latest=None,
                row_count=row_count,
                trading_days=0,
            )

        target = target_start_dt(settings)
        today_end = today_end_dt()
        target_td = settings.max_history_years * 252
        cov_pct = min(100.0, trading_days / target_td * 100) if target_td else 0.0

        head_td_gap = count_trading_days(target, earliest, settings)
        missing_head = head_td_gap > settings.head_tolerance_trading_days

        # ── Listing-boundary check (preserved) ───────────────────────────────
        # A verified LISTING_START boundary whose date is <= the earliest
        # candle date means head coverage is verified. Using <= (not ==) lets
        # the boundary survive cleanup operations (duplicate removal,
        # timestamp normalisation) that may shift the earliest candle forward.
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
                missing_head = False
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

        days_tail = (today_end.date() - latest.date()).days
        missing_tail = days_tail > settings.tail_lag_tolerance_days

        if missing_head or missing_tail or corrupt > 0:
            state = DLState.PARTIAL_DOWNLOAD
        else:
            state = DLState.DOWNLOAD_COMPLETE

        return SymbolInfo(
            symbol=symbol,
            interval=interval,
            state=state,
            earliest=earliest,
            latest=latest,
            row_count=row_count,
            trading_days=trading_days,
            missing_head=missing_head,
            missing_tail=missing_tail,
            coverage_pct=cov_pct,
            listing_start_verified=listing_start_verified,
        )


def cleanup_candle_dbs(settings, infos: list[SymbolInfo]) -> None:
    """Per-startup housekeeping: timestamp normalise → dedupe → corruption delete."""
    total_m = total_d = total_c = 0
    for si in infos:
        path = db_path(settings.data_dir, si.symbol, si.interval)
        if not os.path.isfile(path):
            continue
        cdb = CandleDB(path)
        cdb.connect()
        m = cdb.migrate_normalise_timestamps()
        d = cdb.remove_duplicates()
        c = cdb.remove_corruption()
        cdb.close()
        total_m += m
        total_d += d
        total_c += c
        if m:
            log.info(f"[Cleanup] {si.symbol}: normalised {m} timestamp(s).")
        if d:
            log.info(f"[Cleanup] {si.symbol}: removed {d} duplicates.")
        if c:
            log.info(f"[Cleanup] {si.symbol}: removed {c} corrupted rows.")
    parts = []
    if total_m:
        parts.append(f"{total_m} timestamp(s) normalised")
    if total_d:
        parts.append(f"{total_d} duplicate(s) removed")
    if total_c:
        parts.append(f"{total_c} corrupted row(s) removed")
    if parts:
        log.info(f"Startup cleanup: {', '.join(parts)}.")
