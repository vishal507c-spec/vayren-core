"""Analyzer: real AST verdicts, honest refusals, Slint exclusion."""

from __future__ import annotations

from scripts.migration.agent.analyzer import (
    analyze,
    check_slint_exclusion,
    io_markers,
    pristine_source,
)


def test_risk_slice_is_migratable() -> None:
    report = analyze("risk.engine.evaluate", pristine_source("07_risk/risk/engine.py"))
    assert report.migratable, report.reason
    assert len(report.kernel_checks) == 13
    assert len(report.orchestration_checks) == 5
    assert "order_qty" in report.kernel_checks
    assert "broker_health" in report.kernel_checks
    assert "kill_switch" in report.orchestration_checks
    assert "duplicate" in report.orchestration_checks
    assert "instrument" in report.orchestration_checks


def test_glue_units_refused_with_policy_reason() -> None:
    for unit in (
        "core.event_bus.dispatch",
        "data.download.orchestration",
        "market.storage.sqlite",
    ):
        report = analyze(unit)
        assert not report.migratable, unit
        assert "glue" in report.reason.lower() or "IO" in report.reason, report.reason


def test_session_lifecycle_refused_on_venue_scope() -> None:
    from scripts.migration.agent.analyzer import scope_markers

    report = analyze("execution.session.lifecycle")
    assert not report.migratable
    assert "broker" in report.reason
    markers = scope_markers("08_execution/execution/runtime/session.py")
    assert {"broker", "journal", "checkpoint"} <= set(markers)


def test_qt_coupled_viewport_refused() -> None:
    report = analyze("chart.viewport.math")
    assert not report.migratable
    assert "Qt" in report.reason or "extraction" in report.reason


def test_slint_surfaces_excluded() -> None:
    excluded, reason = check_slint_exclusion("04_chart/chart/widgets/candle_chart_widget.py")
    assert excluded
    assert "excluded" in reason
    excluded, _ = check_slint_exclusion("07_risk/risk/engine.py")
    assert not excluded


def test_io_markers_detected() -> None:
    assert io_markers("02_data/data/downloader/engine.py") != []
    assert "sqlite3" in " ".join(io_markers("03_market/market/database/sqlite.py"))


def test_unknown_unit_refused() -> None:
    report = analyze("no.such.unit")
    assert not report.migratable
