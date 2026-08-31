"""TimeframeToolbar tests — single-line row: 15m 30m 45m 1h 2h 4h ▾ (1D,1W).

No separators, no wrapping, one horizontal line. Visible timeframes are
buttons; overflow lives in the small ▾ dropdown. Selection and signals work
identically for both.
"""

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QFrame

from chart.widgets.timeframe_toolbar import TimeframeToolbar

_KEEP_APP: QCoreApplication | None = None


def _app() -> QApplication:
    global _KEEP_APP
    _KEEP_APP = QApplication.instance()
    if not isinstance(_KEEP_APP, QApplication):
        _KEEP_APP = QApplication([])
    return _KEEP_APP


def test_set_timeframes_creates_buttons() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("1m", "5m", "15m", "1D"))
    assert toolbar.timeframes == ("1m", "5m", "15m", "1D")


def test_set_timeframes_replaces_buttons() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("1m",))
    toolbar.set_timeframes(("1D", "1W"))
    assert toolbar.timeframes == ("1D", "1W")


def test_visible_order_is_tradingview_canonical() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("1W", "4h", "5m", "15m", "1h", "30m", "2h", "45m", "1D"))
    # Visible buttons follow the canonical order, not input order
    assert tuple(toolbar._buttons.keys()) == ("5m", "15m", "30m", "45m", "1h", "2h", "4h")
    # Overflow (1D/1W) lives only in the dropdown menu
    assert toolbar._overflow == ("1W", "1D")
    assert toolbar._dropdown is not None


def test_click_visible_emits_selected_signal() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "30m", "1D"))
    captured: list[str] = []
    toolbar.timeframe_selected.connect(captured.append)
    toolbar._buttons["15m"].click()
    assert captured == ["15m"]


def test_dropdown_overflow_emits_signal_1d_1w_work_as_before() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"))
    captured: list[str] = []
    toolbar.timeframe_selected.connect(captured.append)
    assert toolbar._menu is not None
    actions = {action.text(): action for action in toolbar._menu.actions()}
    assert set(actions) == {"1D", "1W"}
    actions["1D"].trigger()
    actions["1W"].trigger()
    assert captured == ["1D", "1W"]


def test_no_separator_lines_in_toolbar() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "30m", "45m", "1h", "2h", "4h"))
    frames = [f for f in toolbar.findChildren(QFrame) if f.objectName() != ""]
    separators = [f for f in frames if f.objectName() == "separator"]
    assert separators == []
    # No QFrame VLine widgets at all — no "|" dividers
    vlines = [f for f in toolbar.findChildren(QFrame) if f.frameShape() == QFrame.Shape.VLine]
    assert vlines == []


def test_select_visible_timeframe_checks_button_case_insensitive() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"))
    toolbar.select_timeframe("30M")
    assert toolbar._buttons["30m"].isChecked()
    assert not toolbar._buttons["15m"].isChecked()


def test_select_overflow_timeframe_highlights_dropdown() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"))
    toolbar.select_timeframe("1d")
    assert not any(button.isChecked() for button in toolbar._buttons.values())
    assert toolbar._dropdown is not None
    assert toolbar._dropdown.isChecked()


def test_select_unmatched_checks_nothing() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "30m"))
    toolbar.select_timeframe("1M")
    assert not any(button.isChecked() for button in toolbar._buttons.values())
    assert toolbar._dropdown is not None
    assert not toolbar._dropdown.isChecked()


def test_single_fixed_height_no_scrollbar() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"))
    assert toolbar.height() == 32
    assert toolbar.maximumHeight() == 32
