"""Router: request -> domain -> language, deterministic, never guessing."""

from __future__ import annotations

from scripts.migration.router import (
    build_architecture_manifest,
    route_request,
)


def test_risk_routes_to_rust() -> None:
    record = route_request("Add a new risk validation.")
    assert record.canonical_language == "rust"
    assert record.detected_domain == "RISK"
    assert record.confidence == "high"
    assert not record.decision_required
    assert any("07_risk/risk/" in path for path in record.allowed_paths)
    assert any("engine.py" in path for path in record.forbidden_paths)


def test_execution_routes_to_rust() -> None:
    record = route_request("Order execution fast karo.")
    assert record.canonical_language == "rust"
    assert not record.decision_required


def test_storage_routes_to_rust() -> None:
    record = route_request("SQLite storage optimize karo.")
    assert record.canonical_language == "rust"
    assert record.detected_domain == "MARKET_DATA"


def test_backtest_routes_to_rust() -> None:
    record = route_request("Backtest engine improve karo.")
    assert record.canonical_language == "rust"


def test_event_bus_routes_to_rust() -> None:
    record = route_request("Event bus optimize karo.")
    assert record.canonical_language == "rust"


def test_strategy_routes_to_python() -> None:
    record = route_request("New trading strategy banao.")
    assert record.canonical_language == "python"
    assert not record.decision_required


def test_research_routes_to_python() -> None:
    record = route_request("AI research experiment banao.")
    assert record.canonical_language == "python"


def test_chart_zoom_splits_slint_and_rust() -> None:
    record = route_request("Add chart zoom and pan.")
    assert record.canonical_language == "split"
    languages = {layer.language for layer in record.layers}
    assert languages == {"slint", "rust"}


def test_risk_panel_splits_slint_and_rust() -> None:
    record = route_request("Risk panel UI banao.")
    assert record.canonical_language == "split"
    languages = {layer.language for layer in record.layers}
    assert languages == {"slint", "rust"}


def test_gibberish_demands_decision() -> None:
    record = route_request("asdkfjhasd qwerty zzz")
    assert record.decision_required
    assert record.canonical_language == "unknown"
    assert any("ARCHITECTURE DECISION REQUIRED" in note for note in record.notes)


def test_empty_request_demands_decision() -> None:
    record = route_request("")
    assert record.decision_required


def test_file_mention_routes_by_path() -> None:
    record = route_request("fix this", files=["07_risk/risk/engine.py"])
    assert record.canonical_language == "rust"
    assert record.detected_domain == "RISK"
    assert record.confidence == "high"


def test_live_critical_requires_full_gates() -> None:
    record = route_request("Order execution fast karo.")
    assert "shadow validation" in record.validation_required
    assert "explicit promotion" in record.validation_required


def test_manifest_covers_all_units() -> None:
    from scripts.migration.registry import seed_units

    manifest = build_architecture_manifest()
    assert manifest["version"] == 1
    for unit in seed_units():
        entry = manifest["units"][unit.unit_id]
        assert entry["canonical_language"] in ("rust", "slint", "python")
        assert entry["migration_state"]
        assert entry["criticality"] in ("live-critical", "standard")
        assert entry["validation_requirements"]


def test_manifest_risk_is_canonical_rust() -> None:
    manifest = build_architecture_manifest()
    entry = manifest["units"]["risk.engine.evaluate"]
    assert entry["canonical_language"] == "rust"
    assert entry["canonical_path"] == "rust/vayren-core/src/risk.rs"
    assert entry["python_allowed"] is False
    assert entry["criticality"] == "live-critical"
