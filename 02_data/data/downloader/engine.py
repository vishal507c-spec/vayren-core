"""HistoricalDownloadEngine — MODE 1: download historical OHLCV only.

Execution flow (preserved from the original engine):
      START
        ↓ Scan Candle DBs
        ↓ Determine Historical Remaining Coverage
        ↓ Build Download Queue
        ↓ Download Missing History
        ↓ Update DB
        ↓ Move To Next Symbol
      END

NEVER performs: audit, repair, verification, non-trading validation.

Integration adaptations (behaviour-preserving):
- ``run_download`` downloads one explicit symbol/interval/date range (the UI
  path); ``run_batch`` keeps the original multi-pass queue flow.
- Console printing is replaced by a :class:`DownloadReporter`.
- Hard ``sys.exit`` calls became normal returns / exceptions.
- Cancellation replaces SIGINT; coverage scans run without the engine lock.
- The provider (``data.provider.contract.Provider``) is injected — the engine
  never constructs or imports a concrete provider. Its vocabulary is
  canonical: symbols, ``CANONICAL_INTERVALS``, normalized ``ProviderError``
  codes and contract sentinels. Broker specifics (SDKs, credentials, tokens,
  interval ids, errors, rate limits) live in the provider adapter only.
"""

from __future__ import annotations

import contextlib
import logging
import random
import threading
import time
from datetime import datetime
from functools import partial
from typing import Any

from data.calendar import is_market_open, ist_now
from data.downloader.queue import DownloadQueue
from data.downloader.sweep import forward_sweep
from data.lock import EngineLock
from data.models import DLState, SymbolInfo
from data.native_download import chunk_count
from data.provider.contract import (
    ERR_AUTHENTICATION_FAILED,
    Provider,
    ProviderError,
)
from data.reporter import NullReporter
from data.settings import DownloadSettings
from data.storage.candle_db import CandleDB, db_path
from data.storage.scanner import DatabaseScanner, cleanup_candle_dbs
from data.symbols import resolve_symbols

log = logging.getLogger("HistDownloadEngine")


class HistoricalDownloadEngine:
    """MODE 1 — HISTORICAL DOWNLOAD ENGINE (download-only)."""

    def __init__(
        self,
        settings: DownloadSettings,
        reporter=None,
        provider: Provider | None = None,
    ) -> None:
        if provider is None:
            raise TypeError(
                "HistoricalDownloadEngine requires a provider — "
                "resolve the selected broker's historical face through "
                "the unified broker registry (broker.registry)"
            )
        self._settings = settings
        self._report = reporter if reporter is not None else NullReporter()
        self._provider = provider
        self._scanner = DatabaseScanner(settings)
        self._abort = False

    # ── control ──────────────────────────────────────────────────────────────

    def cancel(self) -> None:
        """Request a stop between chunks / between symbols."""
        self._abort = True

    def reset(self) -> None:
        """Clear a previous cancel request (called before a new run starts)."""
        self._abort = False

    def set_reporter(self, reporter) -> None:
        """Attach (or replace) the observation boundary."""
        self._report = reporter

    def _abort_check(self) -> bool:
        return self._abort

    # ── coverage (no network, no lock) ───────────────────────────────────────

    def scan_symbol(self, symbol: str, interval: str) -> SymbolInfo:
        """Coverage of one symbol, derived from its candle DB."""
        return self._scanner.scan_one(symbol, interval)

    def scan_all(self, symbols: list[dict[str, str]]) -> list[SymbolInfo]:
        return self._scanner.scan(symbols)

    def discover_symbols(self) -> list[dict[str, str]]:
        """CSV when present, otherwise database-derived symbols."""
        return resolve_symbols(self._settings)

    def provider_available(self) -> tuple[bool, str]:
        """(available, reason) — SDKs and credentials ready?"""
        return self._provider.available()

    def status(self) -> dict[str, Any]:
        """Architecture-facing status (no credentials, no secrets).

        ``provider`` is a derived display value: it mirrors the
        authoritative ``BrokerSelection.name`` (copied into
        ``DownloadSettings.provider`` by the composition root) and is
        never an independent selection source.
        """
        available, reason = self.provider_available()
        return {
            "provider": self._settings.provider,
            "exchange": self._settings.exchange,
            "data_dir": str(self._settings.data_dir),
            "provider_ready": available,
            "provider_status": reason if not available else "ready",
        }

    # ── single download (UI path) ────────────────────────────────────────────

    def run_download(
        self, symbol: str, interval: str, from_dt: datetime, to_dt: datetime
    ) -> dict[str, Any]:
        """Download one explicit range for one symbol; returns a summary."""
        settings = self._settings
        if self._abort:
            return {"aborted": True}

        if is_market_open(settings):
            now = ist_now()
            self._report.on_status(
                "⚠  MARKET IS OPEN — HISTORICAL DOWNLOAD DISABLED"
                f" (IST {now.strftime('%Y-%m-%d %H:%M:%S')})"
            )
            return {"blocked": True, "reason": "market-open"}

        lock = EngineLock(settings.lock_file, settings.lock_stale_seconds)
        if not lock.try_acquire():
            message = "Engine already running (lock file held by another process)."
            self._report.on_error(symbol, interval, message)
            return {"locked": True}
        heartbeat_stop = self._start_heartbeat(lock)
        try:
            return self._download_one(symbol, interval, from_dt, to_dt)
        finally:
            heartbeat_stop.set()
            lock.release()

    def _download_one(
        self, symbol: str, interval: str, from_dt: datetime, to_dt: datetime
    ) -> dict[str, Any]:
        settings = self._settings
        try:
            known = self._provider.symbols()
        except ProviderError as exc:
            if exc.code != ERR_AUTHENTICATION_FAILED:
                raise
            self._report.on_error(symbol, interval, f"authentication failed: {exc}")
            return {"ok": False, "error": "auth", "message": str(exc)}

        if symbol not in known:
            self._report.on_error(symbol, interval, "symbol not found in NSE instrument map")
            return {"ok": False, "error": "unknown-symbol"}

        cdb = CandleDB(db_path(settings.data_dir, symbol, interval))
        cdb.connect()
        try:
            total_chunks = self._count_chunks(from_dt, to_dt)
            self._report.on_symbol_started(
                symbol, interval, "explicit range", from_dt, to_dt, total_chunks
            )
            self._provider.new_session()
            fetch_chunk = partial(self._provider.fetch_candles, symbol, interval)
            ok, new_rows, _ = forward_sweep(
                fetch_chunk,
                cdb,
                symbol,
                interval,
                from_dt,
                to_dt,
                settings,
                reporter=self._report,
                is_head_sweep=False,
                should_abort=self._abort_check,
            )

            if not ok and not self._abort:
                # Token expired or rate-limited → renew once, retry once
                # (original batch behaviour).
                try:
                    self._provider.renew()
                    ok2, new_rows2, _ = forward_sweep(
                        fetch_chunk,
                        cdb,
                        symbol,
                        interval,
                        from_dt,
                        to_dt,
                        settings,
                        reporter=self._report,
                        is_head_sweep=False,
                        should_abort=self._abort_check,
                    )
                    ok, new_rows = ok2, new_rows + new_rows2
                except Exception as exc:
                    log.error(f"[HistDL] Token renewal failed: {exc}")
                    self._report.on_error(symbol, interval, f"token renewal failed: {exc}")
                    self._abort = True
                    return {"ok": False, "error": "auth"}

            db_total = cdb.count()
            trading_days = cdb.trading_day_count()
            if ok:
                self._report.on_symbol_finished(symbol, interval, new_rows, db_total, trading_days)
                return {
                    "ok": True,
                    "symbol": symbol,
                    "interval": interval,
                    "new_rows": new_rows,
                    "db_total": db_total,
                    "trading_days": trading_days,
                }
            self._report.on_error(
                symbol, interval, "download aborted (rate-limit, token or cancel)"
            )
            self._abort = True
            return {"ok": False}
        finally:
            cdb.close()

    # ── batch flow (original engine preserved) ───────────────────────────────

    def run_batch(self, symbols: list[dict[str, str]] | None = None) -> dict[str, Any]:
        """Original multi-pass queue flow over the whole symbol list."""
        settings = self._settings
        if self._abort:
            return {"aborted": True}
        symbols = list(symbols) if symbols is not None else self.discover_symbols()
        if not symbols:
            return {"ok": False, "error": "no-symbols"}

        lock = EngineLock(settings.lock_file, settings.lock_stale_seconds)
        if not lock.try_acquire():
            message = "Engine already running (lock file held by another process)."
            self._report.on_status(f"⚠ {message}")
            return {"locked": True}
        heartbeat_stop = self._start_heartbeat(lock)
        try:
            return self._run_batch_inner(symbols)
        finally:
            heartbeat_stop.set()
            lock.release()

    def _run_batch_inner(self, symbols: list[dict[str, str]]) -> dict[str, Any]:
        settings = self._settings

        if is_market_open(settings):
            now = ist_now()
            self._report.on_status(
                "⚠  MARKET IS OPEN — HISTORICAL DOWNLOAD DISABLED"
                f" (IST {now.strftime('%Y-%m-%d %H:%M:%S')})"
            )
            for info in self.scan_all(symbols):
                self._report.on_coverage(info)
            return {"blocked": True, "reason": "market-open"}

        self._report.on_status(
            "Historical Download Engine — MODE 1\n"
            "Purpose  : Download historical OHLCV data only\n"
            "Recovery : State derived from candle DBs — no progress files"
        )

        try:
            known = self._provider.symbols()
        except ProviderError as exc:
            if exc.code != ERR_AUTHENTICATION_FAILED:
                raise
            self._report.on_status(f"⚠ Authentication failed: {exc}")
            return {"ok": False, "error": "auth"}

        for pass_num in range(1, settings.max_passes + 1):
            if self._abort:
                break
            log.info(f"[HistDL] === PASS {pass_num}/{settings.max_passes} ===")
            infos = self.scan_all(symbols)
            cleanup_candle_dbs(settings, infos)
            for info in infos:
                self._report.on_coverage(info)

            remaining = [si for si in infos if si.state != DLState.DOWNLOAD_COMPLETE]
            if not remaining:
                self._report.on_status("✔  All symbols DOWNLOAD_COMPLETE.")
                return {"ok": True}

            queue = DownloadQueue()
            queue.build_from_scan(infos, known, settings)
            if queue.total() == 0:
                self._report.on_status("✔  No download jobs — all ranges covered.")
                return {"ok": True}

            self._execute_queue(queue, infos, known)

        for info in self.scan_all(symbols):
            self._report.on_coverage(info)
        return {"ok": True}

    def _execute_queue(
        self, queue: DownloadQueue, _infos: list[SymbolInfo], known: set[str]
    ) -> None:
        settings = self._settings
        ordered = queue.symbols_in_order()

        for idx, (symbol, interval) in enumerate(ordered):
            if self._abort:
                break

            if symbol not in known:
                log.warning(f"[HistDL] {symbol}: not in provider universe — skip.")
                continue

            ok = self._process_symbol(symbol, interval, queue)

            if not ok:
                try:
                    self._provider.renew()
                except Exception as exc:
                    log.error(f"[HistDL] Token renewal failed: {exc}")
                    self._abort = True
                    break
                ok = self._process_symbol(symbol, interval, queue)
                if not ok:
                    self._abort = True
                    break

            if not self._abort and idx < len(ordered) - 1:
                _jitter_sleep(settings.symbol_delay_min, settings.symbol_delay_max)

    def _process_symbol(self, symbol: str, interval: str, queue: DownloadQueue) -> bool:
        """Download all missing ranges for one symbol (original logic)."""
        settings = self._settings
        jobs = queue.jobs_for(symbol, interval)
        if not jobs:
            return True

        cdb = CandleDB(db_path(settings.data_dir, symbol, interval))
        cdb.connect()

        total_new = 0
        try:
            for job in jobs:
                is_head = "head" in job.reason.lower()
                total_chunks = self._count_chunks(job.from_dt, job.to_dt)
                self._report.on_symbol_started(
                    symbol, interval, job.reason, job.from_dt, job.to_dt, total_chunks
                )
                self._provider.new_session()
                fetch_chunk = partial(self._provider.fetch_candles, symbol, interval)
                ok, new_rows, all_chunks_zero = forward_sweep(
                    fetch_chunk,
                    cdb,
                    symbol,
                    interval,
                    job.from_dt,
                    job.to_dt,
                    settings,
                    reporter=self._report,
                    is_head_sweep=is_head,
                    should_abort=self._abort_check,
                )
                total_new += new_rows

                if not ok:
                    return False

                # ── Listing boundary: write when head sweep found nothing ──────
                if is_head and all_chunks_zero:
                    earliest_now = cdb.earliest()
                    if earliest_now is not None:
                        boundary_date = earliest_now.strftime("%Y-%m-%d")
                        cdb.set_boundary("LISTING_START", boundary_date, verified=1)
                        self._report.on_status(
                            f"✔  LISTING_START boundary written: {boundary_date}"
                        )
                # ────────────────────────────────────────────────────────────────

            db_total = cdb.count()
            trading_days = cdb.trading_day_count()
            self._report.on_symbol_finished(symbol, interval, total_new, db_total, trading_days)
        finally:
            cdb.close()

        return True

    # ── helpers ──────────────────────────────────────────────────────────────

    def _count_chunks(self, from_dt: datetime, to_dt: datetime) -> int:
        return chunk_count(from_dt, to_dt, self._settings.chunk_days)

    def _start_heartbeat(self, lock: EngineLock) -> threading.Event:
        stop = threading.Event()

        def _beat() -> None:
            while not stop.wait(30):
                with contextlib.suppress(Exception):
                    lock.heartbeat()

        threading.Thread(target=_beat, daemon=True).start()
        return stop


def _jitter_sleep(lo: float, hi: float) -> None:
    if hi > 0:
        time.sleep(random.uniform(lo, hi))
