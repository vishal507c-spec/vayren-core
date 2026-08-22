"""BacktestWorker — runs BacktestRunner off the UI thread."""

from __future__ import annotations

import threading
from collections import deque

from PySide6.QtCore import QThread, Signal

from backtest.events.backtest_events import (
    BacktestCompleted,
    BacktestFailed,
    BacktestProgress,
    BacktestStarted,
    RunBacktest,
)
from backtest.models.config import BacktestConfig


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

    def __init__(self, runner) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._runner = runner
        self._queue: deque[RunBacktest] = deque()
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

    @property
    def busy(self) -> bool:
        """True while a run is in flight."""
        return self._busy

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Stop the loop and join the thread."""
        self._stop = True
        self._wake.set()
        if self.isRunning():
            self.wait(timeout_ms)

    def run(self) -> None:
        while not self._stop:
            self._wake.wait(timeout=0.2)
            self._wake.clear()
            while not self._stop:
                with self._lock:
                    if not self._queue:
                        break
                    request = self._queue.popleft()
                self._busy = True
                try:
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
