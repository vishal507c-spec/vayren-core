"""Router: request -> BEHAVIOR -> language, deterministic, never guessing.

Priority: canonical/domain/behavior ownership over existing file location. An
existing Python file never makes a UI task canonical Python.
"""

from __future__ import annotations

from scripts.migration.router import (
    build_architecture_manifest,
    route_request,
)

_STRATEGY_LAB_FILE = "00_app/app/ui/strategy_lab_workspace.py"


def test_redesign_strategy_lab_ui_routes_slint() -> None:
    record = route_request("Redesign Strategy Lab UI")
    assert record.canonical_language == "slint"
    assert record.detected_domain == "NATIVE_UI"
    assert record.confidence == "high"
    assert not record.decision_required
    assert "rust/vayren-shell/" in record.allowed_paths


def test_new_strategy_lab_layout_routes_slint() -> None:
    record = route_request("Add new Strategy Lab layout")
    assert record.canonical_language == "slint"


def test_strategy_lab_ui_interaction_routes_slint() -> None:
    record = route_request("Add Strategy Lab UI interaction")
    assert record.canonical_language == "slint"
    assert {layer.language for layer in record.layers} <= {"slint", "rust"}


def test_strategy_lab_calculation_routes_python() -> None:
    record = route_request("Improve Strategy Lab strategy calculation")
    assert record.canonical_language == "python"
    assert record.detected_domain == "STRATEGY"


def test_strategy_lab_state_performance_routes_rust() -> None:
    record = route_request("Optimize Strategy Lab application state/performance")
    assert record.canonical_language == "rust"
    assert record.python_role != "canonical"


def test_create_new_trading_strategy_routes_python() -> None:
    record = route_request("Create a new trading strategy")
    assert record.canonical_language == "python"
    assert record.detected_domain == "STRATEGY"


def test_chart_viewport_math_routes_rust() -> None:
    record = route_request("Add chart viewport math")
    assert record.canonical_language == "rust"
    assert record.detected_domain == "PRESENTATION_MODEL"


def test_chart_ui_control_routes_slint() -> None:
    record = route_request("Add chart UI control")
    assert record.canonical_language == "slint"
    assert {layer.language for layer in record.layers} <= {"slint", "rust"}


# ---- MANDATORY NEGATIVE TEST ------------------------------------------------
# The exact request that regressed before: the router must NOT hand the agent
# the existing Python UI file as a canonical implementation target.


def test_redesign_strategy_lab_ui_does_not_route_to_python_file() -> None:
    record = route_request("Redesign Strategy Lab UI")
    # No Python layer may exist for this request (calculation is not touched).
    assert all(layer.language != "python" for layer in record.layers)
    # The existing Qt file must never appear as a canonical/allowed target...
    assert _STRATEGY_LAB_FILE not in record.allowed_paths
    for allowed in record.allowed_paths:
        assert not allowed.endswith(".py")
    # ...and must be explicitly forbidden as canonical.
    assert any(
        _STRATEGY_LAB_FILE in path and "not a canonical target" in path
        for path in record.forbidden_paths
    )
    # Python is surfaced ONLY as explicitly-labelled legacy glue, with reason.
    assert record.python_role == "legacy_glue"
    glue_note = " ".join(record.notes)
    assert "LEGACY GLUE" in glue_note
    assert "never in Python" in glue_note
    assert record.canonical_language == "slint"


def test_redesign_with_qt_file_argument_still_slint() -> None:
    # Passing the current implementation as --file must not flip canonical.
    record = route_request("Redesign Strategy Lab UI", files=[_STRATEGY_LAB_FILE])
    assert record.canonical_language == "slint"
    assert all(layer.language != "python" for layer in record.layers)
    assert record.python_role == "legacy_glue"


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
    assert manifest["version"] == 2
    for unit in seed_units():
        entry = manifest["units"][unit.unit_id]
        assert entry["canonical_language"] in ("rust", "slint", "python")
        assert entry["migration_state"]
        assert entry["criticality"] in ("live-critical", "standard")
        assert entry["validation_requirements"]


def test_manifest_strategy_lab_ui_is_canonical_slint() -> None:
    """Generated manifest marks the Strategy Lab screen as Slint-canonical.

    The retained Qt file appears ONLY as legacy glue, never as a canonical
    Python target (router priority: behavior over existing file location).
    """
    manifest = build_architecture_manifest()
    screen = manifest["screens"]["strategy_lab_workspace"]
    assert screen["canonical_language"] == "slint"
    assert screen["canonical_path"] == "rust/vayren-shell/"
    assert screen["secondary_language"] == "rust"
    assert screen["python"]["role"] == "legacy_glue"
    assert screen["python"]["file"].endswith("strategy_lab_workspace.py")
    assert "00_app/app/ui/strategy_lab_workspace.py" in screen["forbidden_as_canonical"]
    assert screen["python"]["role"] != "canonical"


def test_manifest_risk_is_canonical_rust() -> None:
    manifest = build_architecture_manifest()
    entry = manifest["units"]["risk.engine.evaluate"]
    assert entry["canonical_language"] == "rust"
    assert entry["canonical_path"] == "rust/vayren-core/src/risk.rs"
    assert entry["python_allowed"] is False
    assert entry["criticality"] == "live-critical"
