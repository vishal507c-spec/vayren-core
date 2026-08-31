"""Events — requests and facts are frozen, bus-compatible Event dataclasses."""

from dataclasses import FrozenInstanceError

from core.events.event import Event

from data.events import (
    CancelDownload,
    CoverageRequest,
    DownloadCompleted,
    DownloadCoverage,
    DownloadFailed,
    DownloadProgress,
    DownloadRequest,
    DownloadStarted,
)


def test_all_events_extend_core_event() -> None:
    for event in (
        DownloadRequest("A", "15m", "2026-01-01", "2026-01-05"),
        CoverageRequest("A", "15m"),
        CancelDownload(),
        DownloadStarted("A", "15m", "2026-01-01", "2026-01-05", 3),
        DownloadProgress("A", "15m", 1, 3, "2026-01-01", "2026-01-02", 100, 500),
        DownloadCompleted("A", "15m", 100, 500, 5),
        DownloadFailed("A", "15m", "rate limited"),
        DownloadCoverage(
            symbol="A",
            interval="15m",
            state="DOWNLOAD_COMPLETE",
            earliest="2025-01-01 09:15:00",
            latest="2026-01-05 15:30:00",
            row_count=500,
            trading_days=5,
            coverage_pct=100.0,
            missing_head=False,
            missing_tail=False,
            listing_start_verified=True,
        ),
    ):
        assert isinstance(event, Event)


def test_events_are_frozen() -> None:
    event = DownloadRequest("A", "15m", "2026-01-01", "2026-01-05")
    try:
        event.symbol = "B"  # type: ignore[misc]
        raise AssertionError("events must be immutable")
    except FrozenInstanceError:
        pass


def test_events_are_hashable() -> None:
    a = DownloadRequest("A", "15m", "2026-01-01", "2026-01-05")
    b = DownloadRequest("A", "15m", "2026-01-01", "2026-01-05")
    assert a == b
    assert hash(a) == hash(b)


def test_public_api_exports_events() -> None:
    import data

    assert data.DownloadRequest is DownloadRequest
    assert data.DownloadCoverage is DownloadCoverage
    assert data.CancelDownload is CancelDownload
