"""DownloadWorker — the QThread bridge between bus and engine."""

from typing import cast

from PySide6.QtWidgets import QApplication

from data.downloader.engine import HistoricalDownloadEngine
from data.events import (
    CancelDownload,
    CoverageRequest,
    DownloadCompleted,
    DownloadCoverage,
    DownloadFailed,
    DownloadRequest,
    DownloadStarted,
)
from data.provider.zerodha import AuthEngine, ZerodhaProvider
from data.tests.conftest import FakeAuth, FakeKite, candle, make_settings
from data.worker import DownloadWorker


def make_worker(tmp_path) -> tuple[DownloadWorker, FakeKite]:
    settings = make_settings(tmp_path)
    kite = FakeKite(
        candles=[candle(__import__("datetime").datetime(2026, 1, 1, 9, 15))],
        instruments=[{"tradingsymbol": "TEST", "instrument_token": 777}],
    )
    provider = ZerodhaProvider(settings, auth=cast(AuthEngine, FakeAuth(kite=kite)))
    engine = HistoricalDownloadEngine(settings, provider=provider)
    worker = DownloadWorker(engine)
    engine.set_reporter(worker)
    return worker, kite


def _drain(worker: DownloadWorker, timeout_ms: int = 5000) -> None:
    import time

    deadline = timeout_ms
    while deadline > 0:
        QApplication.processEvents()
        if not worker.isRunning():
            break
        time.sleep(0.01)
        deadline -= 10
    QApplication.processEvents()


def test_worker_download_emits_full_event_chain(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    started, progressed, completed, failed = [], [], [], []
    worker.started.connect(started.append)
    worker.progress.connect(progressed.append)
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)

    worker.on_download_request(DownloadRequest("TEST", "15m", "2026-01-01", "2026-01-02"))
    _drain(worker)
    worker.shutdown()

    assert len(started) == 1
    assert isinstance(started[0], DownloadStarted)
    assert started[0].total_chunks == 1
    assert len(completed) == 1
    assert isinstance(completed[0], DownloadCompleted)
    assert completed[0].new_rows == 1
    assert failed == []


def test_worker_rejects_invalid_date_range(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    failed: list[DownloadFailed] = []
    worker.failed.connect(failed.append)
    worker.on_download_request(DownloadRequest("TEST", "15m", "not-a-date", "2026-01-02"))
    _drain(worker)
    worker.shutdown()
    assert len(failed) == 1
    assert "invalid date range" in failed[0].reason


def test_worker_cancel_while_idle_is_harmless(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    worker.on_cancel_download(CancelDownload())
    completed: list[DownloadCompleted] = []
    worker.completed.connect(completed.append)
    worker.on_download_request(DownloadRequest("TEST", "15m", "2026-01-01", "2026-01-02"))
    _drain(worker)
    worker.shutdown()
    # A fresh run resets the abort flag; cancel only stops an in-flight run.
    assert len(completed) == 1


def test_worker_coverage_emits_coverage_event(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    coverages: list[DownloadCoverage] = []
    worker.coverage.connect(coverages.append)
    worker.on_coverage_request(CoverageRequest("TEST", "15m"))
    _drain(worker)
    worker.shutdown()
    assert len(coverages) == 1
    assert coverages[0].symbol == "TEST"
    assert coverages[0].state == "NOT_STARTED"
    assert coverages[0].row_count == 0


def test_worker_log_lines_are_emitted(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    lines: list[str] = []
    worker.log.connect(lines.append)
    worker.on_status("hello from the engine")
    assert lines == ["hello from the engine"]
    worker.shutdown()


def test_worker_busy_tracks_runs(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    assert not worker.busy
    worker.on_download_request(DownloadRequest("TEST", "15m", "2026-01-01", "2026-01-02"))
    _drain(worker)
    assert not worker.busy
    worker.shutdown()


def test_worker_shutdown_is_idempotent(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    worker.shutdown()
    worker.shutdown()  # second call must not raise
    assert not worker.isRunning()


def test_worker_sequences_two_downloads(tmp_path) -> None:
    worker, _kite = make_worker(tmp_path)
    completed: list[DownloadCompleted] = []
    worker.completed.connect(completed.append)
    worker.on_download_request(DownloadRequest("TEST", "15m", "2026-01-01", "2026-01-02"))
    worker.on_download_request(DownloadRequest("TEST", "15m", "2026-01-03", "2026-01-04"))
    _drain(worker)
    worker.shutdown()
    assert len(completed) == 2
