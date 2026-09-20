"""Snapshot tests — deterministic machine-readable architecture view."""

import json

from core.system.snapshot import build_snapshot
from core.system.system_model import SystemModel
from core.tests.helpers import build_system


def snapshot():
    return build_snapshot(SystemModel(build_system()))


def test_snapshot_contains_components() -> None:
    snap = snapshot()
    names = [component["name"] for component in snap.components]
    assert names == ["historical_data", "market"]
    data = snap.components[0]
    assert data["type"] == "ingest"
    assert data["capabilities"] == [
        "historical_data.download",
        "historical_data.coverage",
        "historical_data.status",
    ]
    assert data["capabilities_consumed"] == []
    assert data["dependencies"] == ["core"]
    assert data["events_produced"] == [
        "DownloadStarted",
        "DownloadProgress",
        "DownloadCompleted",
        "DownloadFailed",
        "DownloadCoverage",
    ]


def test_snapshot_capabilities_map() -> None:
    snap = snapshot()
    assert snap.capabilities["data.query.candles"] == ["market"]
    assert snap.capabilities["historical_data.download"] == ["historical_data"]


def test_snapshot_events_map() -> None:
    snap = snapshot()
    assert snap.events["DataLoaded"]["producers"] == ["market"]
    assert snap.events["DataLoaded"]["consumers"] == []
    assert snap.events["DownloadRequest"]["producers"] == []
    assert snap.events["DownloadRequest"]["consumers"] == ["historical_data"]
    assert "WindowRendered" not in snap.events


def test_snapshot_gaps() -> None:
    snap = snapshot()
    assert snap.gaps["unresolved_consumers"] == []
    assert snap.gaps["unproduced_events"] == [
        "CancelDownload",
        "CoverageRequest",
        "DownloadRequest",
        "ListSymbols",
        "ListTimeframes",
        "LoadSymbol",
        "TimeframeChanged",
    ]
    assert snap.gaps["unconsumed_events"] == [
        "DataLoaded",
        "DownloadCompleted",
        "DownloadCoverage",
        "DownloadFailed",
        "DownloadProgress",
        "DownloadStarted",
        "QuotesLoaded",
        "SymbolsListed",
        "TimeframesListed",
    ]
    assert snap.gaps["dependency_cycles"] == []


def test_snapshot_json_round_trip() -> None:
    snap = snapshot()
    data = json.loads(snap.to_json())
    assert data["components"][0]["name"] == "historical_data"
    assert "DownloadCompleted" in data["gaps"]["unconsumed_events"]


def test_snapshot_render() -> None:
    text = snapshot().render()
    assert "- historical_data v1.0.0 [ingest]" in text
    assert "capability historical_data.download -> historical_data" in text
    # Empty consumers render as a bare trailing space (verified format).
    assert "event DataLoaded: produced by market; consumed by \n" in text
    assert "gap (produced events with no consumer): DataLoaded," in text
    assert "gap (consumed events with no producer): CancelDownload," in text
