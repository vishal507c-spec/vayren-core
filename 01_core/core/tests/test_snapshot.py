"""Snapshot tests — deterministic machine-readable architecture view."""

import json

from core.system.snapshot import build_snapshot
from core.system.system_model import SystemModel
from core.tests.test_component_poc import build_system


def snapshot():
    return build_snapshot(SystemModel(build_system()))


def test_snapshot_contains_components() -> None:
    snap = snapshot()
    names = [component["name"] for component in snap.components]
    assert names == ["chart", "market"]
    chart = snap.components[0]
    assert chart["type"] == "presentation"
    assert chart["capabilities"] == ["chart.render"]
    assert chart["capabilities_consumed"] == ["data.query.candles"]
    assert chart["dependencies"] == ["core", "market"]
    assert chart["events_produced"] == ["ChartReady", "WindowRendered"]


def test_snapshot_capabilities_map() -> None:
    snap = snapshot()
    assert snap.capabilities["data.query.candles"] == ["market"]
    assert snap.capabilities["chart.render"] == ["chart"]


def test_snapshot_events_map() -> None:
    snap = snapshot()
    assert snap.events["DataLoaded"]["producers"] == ["market"]
    assert snap.events["DataLoaded"]["consumers"] == ["chart"]
    assert snap.events["WindowRendered"]["producers"] == ["chart"]
    assert snap.events["WindowRendered"]["consumers"] == []


def test_snapshot_gaps() -> None:
    snap = snapshot()
    assert snap.gaps["unresolved_consumers"] == []
    assert snap.gaps["unproduced_events"] == [
        "ListSymbols",
        "ListTimeframes",
        "LoadSymbol",
        "TimeframeChanged",
    ]
    assert snap.gaps["unconsumed_events"] == [
        "ChartReady",
        "QuotesLoaded",
        "SymbolsListed",
        "TimeframesListed",
        "WindowRendered",
    ]
    assert snap.gaps["dependency_cycles"] == []


def test_snapshot_json_round_trip() -> None:
    snap = snapshot()
    data = json.loads(snap.to_json())
    assert data["components"][0]["name"] == "chart"
    assert "WindowRendered" in data["gaps"]["unconsumed_events"]


def test_snapshot_render() -> None:
    text = snapshot().render()
    assert "- chart v1.0.0 [presentation]" in text
    assert "capability chart.render -> chart" in text
    assert "event DataLoaded: produced by market; consumed by chart" in text
    assert "gap (produced events with no consumer): ChartReady," in text
    assert "gap (consumed events with no producer): ListSymbols," in text
