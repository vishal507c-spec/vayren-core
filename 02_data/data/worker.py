"""DownloadWorker — runs the download engine off the UI thread.

The engine is strictly single-threaded (sequential API calls by design). This
QThread owns the engine, processes download/coverage requests one at a time,
and emits Qt signals carrying the bus event objects. Bootstrap connects those
signals to ``EventBus.publish`` (delivering the events on the Qt thread) and
the requests arrive here through bus subscriptions.

Only the worker thread touches the engine; ``cancel`` is just a flag set from
the main thread and is honoured between chunks.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Any

from PySide6.QtCore import QThread, Signal

from data.events.cancel_download import CancelDownload
from data.events.coverage_request import CoverageRequest
from data.events.download_completed import DownloadCompleted
from data.events.download_coverage import DownloadCoverage
from data.events.download_failed import DownloadFailed
from data.events.download_progress import DownloadProgress
from data.events.download_request import DownloadRequest
from data.events.download_started import DownloadStarted

_DATE_FMT = "%Y-%m-%d"


class DownloadWorker(QThread):
    """Event bridge between the bus and the download engine."""

    started = Signal(object)  # DownloadStarted
    progress = Signal(object)  # DownloadProgress
    completed = Signal(object)  # DownloadCompleted
    failed = Signal(object)  # DownloadFailed
    coverage = Signal(object)  # DownloadCoverage
    log = Signal(str)  # engine status messages

    def __init__(self, engine) -> None:
        super().__init__()
        self._engine = engine
        self._ops: deque[tuple[str, Any]] = deque()
        self._wake = threading.Event()
        self._stop = False
        self._busy = False
        self._lock = threading.Lock()

    # ── bus request handlers (main thread) ───────────────────────────────────

    def on_download_request(self, event: DownloadRequest) -> None:
        self._submit(("download", event))

    def on_coverage_request(self, event: CoverageRequest) -> None:
        self._submit(("coverage", event))

    def on_cancel_download(self, _event: CancelDownload) -> None:
        self._engine.cancel()

    def _submit(self, op: tuple[str, Any]) -> None:
        with self._lock:
            self._ops.append(op)
            self._wake.set()
        if not self.isRunning():
            self.start()

    @property
    def busy(self) -> bool:
        return self._busy

    def shutdown(self, timeout_ms: int = 3000) -> None:
        """Stop the worker loop and join the thread (safe to call twice)."""
        self._stop = True
        self._wake.set()
        if self.isRunning():
            self.wait(timeout_ms)

    # ── thread body ──────────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop:
            self._wake.wait(timeout=0.2)
            self._wake.clear()
            while not self._stop:
                with self._lock:
                    if not self._ops:
                        break
                    kind, event = self._ops.popleft()
                self._busy = True
                try:
                    if kind == "download":
                        self._run_download(event)
                    elif kind == "coverage":
                        self._run_coverage(event)
                finally:
                    self._busy = False

    def _run_download(self, event: DownloadRequest) -> None:
        try:
            from_dt = datetime.strptime(event.from_date, _DATE_FMT)
            to_dt = datetime.strptime(event.to_date, _DATE_FMT).replace(
                hour=23, minute=59, second=59
            )
        except ValueError:
            self.failed.emit(DownloadFailed(event.symbol, event.interval, "invalid date range"))
            return
        if from_dt > to_dt:
            self.failed.emit(
                DownloadFailed(event.symbol, event.interval, "from date after to date")
            )
            return
        self._engine.reset()
        self._engine.run_download(event.symbol, event.interval, from_dt, to_dt)

    def _run_coverage(self, event: CoverageRequest) -> None:
        try:
            info = self._engine.scan_symbol(event.symbol, event.interval)
        except Exception as exc:  # noqa: BLE001 — the bus swallows handler errors
            self.failed.emit(
                DownloadFailed(event.symbol, event.interval, f"coverage scan failed: {exc}")
            )
            return
        self.on_coverage(info)

    # ── DownloadReporter implementation → Qt signals ─────────────────────────

    def on_status(self, message: str) -> None:
        self.log.emit(message)

    def on_symbol_started(
        self,
        symbol: str,
        interval: str,
        _reason: str,
        from_dt: datetime,
        to_dt: datetime,
        total_chunks: int,
    ) -> None:
        self.started.emit(
            DownloadStarted(
                symbol=symbol,
                interval=interval,
                from_date=from_dt.strftime(_DATE_FMT),
                to_date=to_dt.strftime(_DATE_FMT),
                total_chunks=total_chunks,
            )
        )

    def on_chunk(
        self,
        symbol: str,
        interval: str,
        chunk: int,
        total_chunks: int,
        chunk_start: datetime,
        chunk_end: datetime,
        new_rows: int,
        db_total: int,
    ) -> None:
        self.progress.emit(
            DownloadProgress(
                symbol=symbol,
                interval=interval,
                chunk=chunk,
                total_chunks=total_chunks,
                chunk_start=chunk_start.strftime(_DATE_FMT),
                chunk_end=chunk_end.strftime(_DATE_FMT),
                new_rows=new_rows,
                db_total=db_total,
            )
        )

    def on_symbol_finished(
        self, symbol: str, interval: str, new_rows: int, db_total: int, trading_days: int
    ) -> None:
        self.completed.emit(
            DownloadCompleted(
                symbol=symbol,
                interval=interval,
                new_rows=new_rows,
                db_total=db_total,
                trading_days=trading_days,
            )
        )

    def on_coverage(self, info) -> None:
        self.coverage.emit(
            DownloadCoverage(
                symbol=info.symbol,
                interval=info.interval,
                state=info.state.name,
                earliest=info.earliest.strftime("%Y-%m-%d %H:%M:%S") if info.earliest else None,
                latest=info.latest.strftime("%Y-%m-%d %H:%M:%S") if info.latest else None,
                row_count=info.row_count,
                trading_days=info.trading_days,
                coverage_pct=info.coverage_pct,
                missing_head=info.missing_head,
                missing_tail=info.missing_tail,
                listing_start_verified=info.listing_start_verified,
            )
        )

    def on_error(self, symbol: str, interval: str, message: str) -> None:
        self.failed.emit(DownloadFailed(symbol=symbol, interval=interval, reason=message))
