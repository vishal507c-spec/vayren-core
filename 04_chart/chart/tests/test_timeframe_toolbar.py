"""TimeframeToolbar tests — button population, selection and signal emission."""

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

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


def test_click_emits_selected_signal() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "1D"))
    captured: list[str] = []
    toolbar.timeframe_selected.connect(captured.append)
    toolbar._buttons["15m"].click()
    toolbar._buttons["1D"].click()
    assert captured == ["15m", "1D"]


def test_select_timeframe_checks_button_case_insensitive() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("1D", "1W"))
    toolbar.select_timeframe("1d")
    assert toolbar._buttons["1D"].isChecked()
    assert not toolbar._buttons["1W"].isChecked()


def test_select_timeframe_unmatched_checks_nothing() -> None:
    _app()
    toolbar = TimeframeToolbar()
    toolbar.set_timeframes(("15m", "1D"))
    toolbar.select_timeframe("1M")
    assert not any(button.isChecked() for button in toolbar._buttons.values())
