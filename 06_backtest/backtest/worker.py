"""BacktestWorker — runs BacktestRunner off the UI thread."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from backtest.events.backtest_events import (
    BacktestCompleted,
    BacktestFailed,
    BacktestProgress,
    BacktestStarted,
    RunBacktest,
)
from backtest.models.config import BacktestConfig


@dataclass(frozen=True)
class BatchEnqueued:
    """One multi-symbol batch job for the worker queue (plain data only)."""

    request_id: str
    strategy_id: str
    symbols: tuple[str, ...]
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    slippage_pct: float
    commission_pct: float
    max_workers: int = 0  # 0 = derive from the machine


class BacktestWorker(QThread):
    """QThread bridge for the backtest runner, mirroring DownloadWorker.

    Bus request handlers enqueue work on the main thread; the thread body
    drains the queue running each request through the runner and emitting
    Qt signals that bootstrap bridges to ``bus.publish``. Only the worker
    thread touches the runner and repository I/O.
    """

    started = Signal(object)  # BacktestStarted
    progress = Signal(object)  # BacktestProgress
    completed = Signal(object)  # BacktestCompleted
    failed = Signal(object)  # BacktestFailed
    batch_progress = Signal(object)  # (request_id, completed, total) — stock-level only
    batch_done = Signal(object)  # (request_id, tuple[SymbolBatchResult, ...])
    batch_failed = Signal(object)  # (request_id, reason)

    def __init__(self, runner) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._runner = runner
        self._queue: deque[RunBacktest] = deque()
        self._batch_queue: deque[BatchEnqueued] = deque()
        self._batch_cancel = threading.Event()
        self._wake = threading.Event()
        self._stop = False
        self._busy = False
        self._lock = threading.Lock()

    def on_run_backtest(self, event: RunBacktest) -> None:
        """Enqueue a backtest request from the bus."""
        with self._lock:
            self._queue.append(event)
            self._wake.set()
        if not self.isRunning():
            self.start()

    def enqueue_batch(self, job: BatchEnqueued) -> None:
        """Enqueue one multi-symbol batch (driven by the batch coordinator)."""
        with self._lock:
            self._batch_queue.append(job)
            self._wake.set()
        if not self.isRunning():
            self.start()

    def cancel_batch(self) -> None:
        """Ask the in-flight batch to stop after the current symbols."""
        self._batch_cancel.set()
        with self._lock:
            self._batch_queue.clear()
            self._wake.set()

    @property
    def busy(self) -> bool:
        """True while a run is in flight."""
        return self._busy

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Stop the loop and join the thread."""
        self._stop = True
        self._batch_cancel.set()
        self._wake.set()
        if self.isRunning():
            self.wait(timeout_ms)

    def run(self) -> None:
        while not self._stop:
            self._wake.wait(timeout=0.2)
            self._wake.clear()
            while not self._stop:
                with self._lock:
                    if self._batch_queue:
                        job = self._batch_queue.popleft()
                        request = None
                    elif self._queue:
                        request = self._queue.popleft()
                        job = None
                    else:
                        break
                self._busy = True
                try:
                    if job is not None:
                        self._execute_batch(job)
                    elif request is not None:
                        self._execute(request)
                finally:
                    self._busy = False

    def _execute(self, request: RunBacktest) -> None:
        self.started.emit(
            BacktestStarted(
                request_id=request.request_id,
                strategy_ids=request.strategy_ids,
                symbol=request.symbol,
                timeframe=request.timeframe,
            )
        )
        config = BacktestConfig(
            symbol=request.symbol,
            timeframe=request.timeframe,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_capital=request.initial_capital,
            slippage_pct=request.slippage_pct,
            commission_pct=request.commission_pct,
        )

        def _on_progress(processed: int, total: int) -> None:
            self.progress.emit(
                BacktestProgress(request_id=request.request_id, processed=processed, total=total)
            )

        try:
            result = self._runner.run(config, request.strategy_ids, on_progress=_on_progress)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(
                BacktestFailed(request_id=request.request_id, reason=str(exc) or "backtest error")
            )
            return

        if result.has_error and not result.results:
            self.failed.emit(
                BacktestFailed(
                    request_id=request.request_id, reason=result.error_detail or "no results"
                )
            )
            return
        self.completed.emit(BacktestCompleted(request_id=request.request_id, result=result))

    def _execute_batch(self, job: BatchEnqueued) -> None:
        """Run one multi-symbol batch with bounded parallelism (off UI thread).

        Progress is stock-level only — one ``batch_progress`` per completed
        symbol, never per bar. The strategy record resolves exactly like the
        single path (``get_strategy_by_id`` then ``load_strategy_record``);
        per-symbol failures stay inside the outcome tuple.
        """
        from backtest.runner import BatchSpec, run_symbol_batch

        # Market data ALWAYS comes from the runner's repository directory
        # (per-stock SQLite files). The runner's `_data_dir` is the strategy
        # library — using it for market reads addresses stock DBs that do
        # not exist there (every symbol fails with "database not found").
        market_dir: str | None = None
        repository = getattr(self._runner, "_repository", None)
        for attr in ("directory", "_directory"):
            candidate = getattr(repository, attr, None)
            if candidate is not None:
                market_dir = str(candidate)
                break
        if not market_dir:
            self.batch_failed.emit((job.request_id, "no market data directory"))
            return
        strategy_dir = getattr(self._runner, "_data_dir", None)
        rec = None
        try:
            from strategy.language.storage import get_strategy_by_id, load_strategy_record

            rec = get_strategy_by_id(job.strategy_id, strategy_dir)
            if rec is None:
                rec = load_strategy_record(job.strategy_id, strategy_dir)
        except Exception:  # noqa: BLE001
            rec = None
        if rec is None:
            self.batch_failed.emit((job.request_id, f"unknown strategy {job.strategy_id}"))
            return

        spec = BatchSpec(
            strategy_id=rec.id,
            strategy_name=rec.name,
            strategy_version=rec.version,
            strategy_code=rec.code,
            symbols=job.symbols,
            timeframe=job.timeframe,
            start_date=job.start_date,
            end_date=job.end_date,
            initial_capital=job.initial_capital,
            slippage_pct=job.slippage_pct,
            commission_pct=job.commission_pct,
            max_workers=job.max_workers,
        )
        self._batch_cancel.clear()

        def _on_stock(done: int, total: int) -> None:
            self.batch_progress.emit((job.request_id, done, total))

        try:
            outcomes = run_symbol_batch(
                market_dir,
                spec,
                on_stock=_on_stock,
                should_cancel=self._batch_cancel.is_set,
            )
        except Exception as exc:  # noqa: BLE001
            self.batch_failed.emit((job.request_id, str(exc) or "batch error"))
            return
        self.batch_done.emit((job.request_id, outcomes))
