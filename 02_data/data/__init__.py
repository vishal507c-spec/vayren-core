"""data — historical data ingest domain (MODE 1: download only).

Public API of the domain (imports must use these names, never internal
modules across packages):

    from data import (
        DownloadSettings, DownloadWorker, data_manifest,
        HistoricalDownloadEngine, DownloadRequest, DownloadCoverage,
        DownloadCompleted, DownloadFailed, DownloadProgress,
        DownloadStarted, CancelDownload, CoverageRequest,
    )

The engine is the preserved historical download engine (download-only,
idempotent, resume-from-database). It never performs audit, repair or
verification.
"""

from data.downloader.engine import HistoricalDownloadEngine
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
from data.manifest import data_manifest
from data.settings import DownloadSettings
from data.worker import DownloadWorker

__all__ = [
    "DownloadSettings",
    "HistoricalDownloadEngine",
    "DownloadWorker",
    "data_manifest",
    "DownloadRequest",
    "CoverageRequest",
    "CancelDownload",
    "DownloadStarted",
    "DownloadProgress",
    "DownloadCompleted",
    "DownloadFailed",
    "DownloadCoverage",
]
