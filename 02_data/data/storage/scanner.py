"""DatabaseScanner — derives download state from candle DBs (preserved).

State is derived, never stored: the scan reads the database, the Rust data
kernel decides coverage (`data.native_download.decide_coverage`), including
the head-gap suppression through a verified LISTING_START boundary and its
``<=`` rule that lets the boundary survive cleanup operations.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from data.calendar import target_start_dt, today_end_dt
from data.models import DLState, SymbolInfo
from data.native_download import decide_coverage
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

        target = target_start_dt(settings)
        today_end = today_end_dt()
        verdict = decide_coverage(
            row_count=row_count,
            trading_days=trading_days,
            corrupt=corrupt,
            earliest=earliest,
            latest=latest,
            boundary=listing_boundary,
            target_start_at=target,
            today_end_at=today_end,
            max_history_years=settings.max_history_years,
            head_tolerance_trading_days=settings.head_tolerance_trading_days,
            tail_lag_tolerance_days=settings.tail_lag_tolerance_days,
            holidays=settings.holidays,
        )

        if verdict.listing_start_verified and listing_boundary and earliest is not None:
            _log_key = f"{symbol}/{interval}"
            if _log_key not in self._logged_listing_starts:
                self._logged_listing_starts.add(_log_key)
                log.debug(
                    f"[Scanner] {symbol}/{interval}: LISTING_START boundary "
                    f"verified at {listing_boundary['boundary_date']} "
                    f"(earliest candle {earliest.date()}) — "
                    "suppressing head-gap download."
                )

        if verdict.state == DLState.NOT_STARTED.name:
            return SymbolInfo(
                symbol=symbol,
                interval=interval,
                state=DLState.NOT_STARTED,
                earliest=None,
                latest=None,
                row_count=row_count,
                trading_days=0,
            )

        return SymbolInfo(
            symbol=symbol,
            interval=interval,
            state=DLState[verdict.state],
            earliest=earliest,
            latest=latest,
            row_count=row_count,
            trading_days=trading_days,
            missing_head=verdict.missing_head,
            missing_tail=verdict.missing_tail,
            coverage_pct=verdict.coverage_pct,
            listing_start_verified=verdict.listing_start_verified,
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
