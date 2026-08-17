"""DownloadQueue — jobs built only from missing coverage."""

from datetime import datetime

from data.downloader.queue import DownloadQueue
from data.models import DLState, SymbolInfo
from data.tests.conftest import make_settings


def make_info(
    symbol: str,
    state: DLState,
    earliest: datetime | None = None,
    latest: datetime | None = None,
    missing_head: bool = False,
    missing_tail: bool = False,
    listing_start_verified: bool = False,
) -> SymbolInfo:
    return SymbolInfo(
        symbol=symbol,
        interval="15m",
        state=state,
        earliest=earliest,
        latest=latest,
        row_count=10,
        trading_days=10,
        missing_head=missing_head,
        missing_tail=missing_tail,
        listing_start_verified=listing_start_verified,
    )


def test_not_started_gets_full_range_job(tmp_path) -> None:
    queue = DownloadQueue()
    queue.build_from_scan(
        [make_info("NEW", DLState.NOT_STARTED)],
        {"NEW"},
        make_settings(tmp_path),
    )
    assert queue.total() == 1
    job = queue.jobs_for("NEW", "15m")[0]
    assert "full history" in job.reason
    assert job.from_dt < job.to_dt


def test_partial_gets_head_and_tail_jobs(tmp_path) -> None:
    settings = make_settings(tmp_path)
    queue = DownloadQueue()
    queue.build_from_scan(
        [
            make_info(
                "GAP",
                DLState.PARTIAL_DOWNLOAD,
                earliest=datetime(2024, 1, 1, 9, 15),
                latest=datetime(2026, 7, 1, 9, 15),
                missing_head=True,
                missing_tail=True,
            )
        ],
        {"GAP"},
        settings,
    )
    reasons = {job.reason for job in queue.jobs_for("GAP", "15m")}
    assert "PARTIAL: missing head coverage" in reasons
    assert "PARTIAL: missing tail coverage" in reasons
    assert queue.total() == 2


def test_verified_listing_boundary_skips_head_job(tmp_path) -> None:
    queue = DownloadQueue()
    queue.build_from_scan(
        [
            make_info(
                "BOUND",
                DLState.PARTIAL_DOWNLOAD,
                earliest=datetime(2018, 6, 1, 9, 15),
                latest=datetime(2026, 8, 1, 9, 15),
                missing_head=True,
                missing_tail=False,
                listing_start_verified=True,
            )
        ],
        {"BOUND"},
        make_settings(tmp_path),
    )
    assert queue.total() == 0


def test_complete_symbols_are_skipped(tmp_path) -> None:
    queue = DownloadQueue()
    queue.build_from_scan(
        [make_info("DONE", DLState.DOWNLOAD_COMPLETE)],
        {"DONE"},
        make_settings(tmp_path),
    )
    assert queue.total() == 0


def test_symbols_outside_universe_are_skipped(tmp_path) -> None:
    queue = DownloadQueue()
    queue.build_from_scan(
        [make_info("NOTOKEN", DLState.NOT_STARTED)],
        set(),
        make_settings(tmp_path),
    )
    assert queue.total() == 0


def test_partial_symbols_are_queued_before_not_started(tmp_path) -> None:
    queue = DownloadQueue()
    queue.build_from_scan(
        [
            make_info("NEW", DLState.NOT_STARTED),
            make_info(
                "GAP",
                DLState.PARTIAL_DOWNLOAD,
                earliest=datetime(2024, 1, 1),
                latest=datetime(2026, 7, 1),
                missing_head=True,
                missing_tail=False,
            ),
        ],
        {"NEW", "GAP"},
        make_settings(tmp_path),
    )
    ordered = queue.symbols_in_order()
    assert ordered == [("GAP", "15m"), ("NEW", "15m")]
