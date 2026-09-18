"""Indicator 3-action toolbar — eye / settings / delete behavior.

Covers the TradingView-style compact toolbar contract for OBR,
OBR-Visual-Probe and Stochastic: correct row wiring, real visibility
toggle, real settings storage + signal, and complete deletion that cannot
resurrect on refresh. One indicator's action must never affect another.

(Qt app bootstrap comes from this directory's conftest — no fixture needed.)
"""

from __future__ import annotations

import pytest

from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.indicator_settings_dialog import (
    PARAM_SPECS,
    build_indicator_settings_dialog,
    param_rows_for,
    read_indicator_settings,
)
from chart.widgets.indicator_visibility_panel import IndicatorVisibilityPanel

NAMES = ("OBR", "OBR-Visual-Probe", "Stochastic")


def _widget() -> CandleChartWidget:
    w = CandleChartWidget()
    for name in NAMES:
        w.add_indicator(name)
    return w


def test_rows_have_exactly_three_actions() -> None:
    panel = IndicatorVisibilityPanel()
    panel.add_indicator("OBR")
    row = panel.row("OBR")
    assert row is not None
    # no source / more controls exist at all
    assert not hasattr(row, "source_button")
    assert not hasattr(row, "more_button")
    assert getattr(panel, "source_requested", None) is None
    assert getattr(panel, "more_requested", None) is None


@pytest.mark.parametrize("name", NAMES)
def test_eye_toggles_visibility_and_stays_isolated(name: str) -> None:
    w = _widget()
    row = w.visibility_panel.row(name)
    assert row is not None and row.is_visible
    seen: list[tuple[str, bool]] = []
    w.visibility_panel.visibility_changed.connect(lambda n, v: seen.append((n, v)))
    row.eye_button.click()  # OFF
    assert not row.is_visible
    assert not w.is_indicator_visible(name)
    assert seen == [(name, False)]
    for other in NAMES:
        if other != name:
            assert w.is_indicator_visible(other) is True
    row.eye_button.click()  # ON
    assert row.is_visible and w.is_indicator_visible(name)


@pytest.mark.parametrize("name", NAMES)
def test_settings_opens_correct_indicator(name: str) -> None:
    # Signal-level: the row reports the exact indicator name. A bare panel is
    # used so the chart widget's dialog handler stays out of the loop (the
    # dialog itself is covered below with a stubbed exec).
    panel = IndicatorVisibilityPanel()
    for n in NAMES:
        panel.add_indicator(n)
    seen: list[str] = []
    panel.settings_requested.connect(seen.append)
    row = panel.row(name)
    assert row is not None
    row.settings_button.click()
    assert seen == [name]


def test_widget_settings_opens_dialog_and_stores(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    import chart.widgets.indicator_settings_dialog as dlg_mod

    opened: list[str] = []

    def _stub_build(name, stored=None, parent=None):  # noqa: ARG001
        opened.append(name)

        class _StubDialog:
            def exec(self):
                return QDialog.DialogCode.Accepted

        return _StubDialog()

    monkeypatch.setattr(dlg_mod, "build_indicator_settings_dialog", _stub_build)
    monkeypatch.setattr(dlg_mod, "read_indicator_settings", lambda _dlg: {"c1_thresh": 2.5})
    w = _widget()
    seen: list[tuple[str, dict]] = []
    w.indicator_settings_changed.connect(lambda n, p: seen.append((n, dict(p))))
    w._on_indicator_settings("OBR")
    assert opened == ["OBR"]
    assert w.indicator_settings_for("OBR") == {"c1_thresh": 2.5}
    assert seen and seen[-1][0] == "OBR"

    # rejected dialog stores nothing
    def _reject_build(name, stored=None, parent=None):  # noqa: ARG001
        opened.append(("reject", name))
        return _RejectDialog()

    class _RejectDialog:
        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(dlg_mod, "build_indicator_settings_dialog", _reject_build)
    w._on_indicator_settings("Stochastic")
    assert w.indicator_settings_for("Stochastic") == {}


@pytest.mark.parametrize("name", NAMES)
def test_delete_removes_indicator_completely(name: str) -> None:
    w = _widget()
    removed: list[str] = []
    w.visibility_panel.indicator_removed.connect(removed.append)
    row = w.visibility_panel.row(name)
    assert row is not None
    row.delete_button.click()
    assert name not in w.visibility_panel.indicators
    assert removed == [name]
    # others untouched
    for other in NAMES:
        if other != name:
            assert w.visibility_panel.has_indicator(other)


def test_deleted_indicator_cannot_resurrect() -> None:
    w = _widget()
    w.set_indicator_visible("OBR", False)
    w.remove_indicator("OBR")
    assert "OBR" not in w.visibility_panel.indicators
    # a rebuild driven by the current rows (the refresh/timeframe/symbol
    # path) must not re-list the deleted indicator
    w.set_indicators(tuple(w.visibility_panel.indicators))
    assert "OBR" not in w.visibility_panel.indicators
    assert w.visibility_panel.has_indicator("Stochastic")


def test_settings_storage_roundtrip_and_signal() -> None:
    w = _widget()
    seen: list[tuple[str, dict]] = []
    w.indicator_settings_changed.connect(lambda n, p: seen.append((n, dict(p))))
    w.set_indicator_settings("OBR", {"c1_thresh": 2.0, "rsi_thr": 70.0})
    assert w.indicator_settings_for("OBR") == {"c1_thresh": 2.0, "rsi_thr": 70.0}
    assert seen and seen[-1][0] == "OBR"
    # per-indicator isolation
    assert w.indicator_settings_for("Stochastic") == {}
    w.set_indicator_visible("OBR", False)
    assert w.indicator_settings_for("OBR") == {"c1_thresh": 2.0, "rsi_thr": 70.0}
    w.clear_indicator_settings("OBR")
    assert w.indicator_settings_for("OBR") == {}


def test_spec_rows_seed_defaults_and_overrides() -> None:
    rows = param_rows_for("OBR", None)
    by_key = {r["key"]: r for r in rows}
    assert by_key["c1_thresh"]["value"] == 1.25
    assert by_key["rsi_thr"]["value"] == 65.0
    rows = param_rows_for("OBR", {"c1_thresh": 3.5})
    assert {r["key"]: r for r in rows}["c1_thresh"]["value"] == 3.5
    # unknown strategy indicators honestly report no adjustable parameters
    assert param_rows_for("OBR-Visual-Probe", None) == ()
    # spec table keeps the keys the plot runner expects
    assert {s.key for s in PARAM_SPECS["OBR"]} == {
        "c1_thresh",
        "rsi_thr",
        "exit_hour",
        "exit_min",
    }
    assert {s.key for s in PARAM_SPECS["Stochastic"]} == {"k", "d", "smooth"}


def test_settings_dialog_returns_values() -> None:
    dlg = build_indicator_settings_dialog("Stochastic", {"k": 21.0})
    assert dlg.windowTitle() == "Stochastic — Settings"
    assert read_indicator_settings(dlg) == {"k": 21.0, "d": 3.0, "smooth": 3.0}
    dlg = build_indicator_settings_dialog("OBR-Visual-Probe", None)
    assert read_indicator_settings(dlg) == {}
