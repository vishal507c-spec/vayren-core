"""HistoricalDownloadPanel — events in, requests out (bus stays untouched)."""

from core.event_bus.event_bus import EventBus
from PySide6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

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
from data.ui.historical_panel import HistoricalDownloadPanel


def test_panel_publishes_download_request() -> None:
    bus = EventBus()
    panel = HistoricalDownloadPanel(bus)
    received: list[DownloadRequest] = []
    bus.subscribe(DownloadRequest, received.append)

    panel.panel.download_requested.emit("RELIANCE", "15m", "2026-01-01", "2026-01-05")
    assert len(received) == 1
    event = received[0]
    assert event.symbol == "RELIANCE"
    assert event.interval == "15m"
    assert event.from_date == "2026-01-01"
    assert event.to_date == "2026-01-05"


def test_panel_publishes_coverage_request() -> None:
    bus = EventBus()
    panel = HistoricalDownloadPanel(bus)
    received: list[CoverageRequest] = []
    bus.subscribe(CoverageRequest, received.append)
    panel.panel.coverage_requested.emit("TCS", "15m")
    assert received == [CoverageRequest("TCS", "15m")]


def test_panel_publishes_cancel() -> None:
    bus = EventBus()
    panel = HistoricalDownloadPanel(bus)
    received: list[CancelDownload] = []
    bus.subscribe(CancelDownload, received.append)
    panel.panel.cancel_requested.emit()
    assert received == [CancelDownload()]


def test_panel_handles_full_fact_chain() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.show()
    QApplication.processEvents()
    panel.on_download_started(DownloadStarted("RELIANCE", "15m", "2026-01-01", "2026-01-05", 3))
    assert panel.panel.busy
    assert "RELIANCE" in panel.log.toPlainText()

    panel.on_download_progress(
        DownloadProgress("RELIANCE", "15m", 1, 3, "2026-01-01", "2026-01-02", 100, 400)
    )
    assert "+100" in panel.log.toPlainText()

    panel.on_download_completed(DownloadCompleted("RELIANCE", "15m", 400, 800, 8))
    assert not panel.panel.busy
    assert "800" in panel.log.toPlainText()


def test_panel_handles_failure_and_release() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.on_download_started(DownloadStarted("RELIANCE", "15m", "2026-01-01", "2026-01-05", 1))
    assert panel.panel.busy
    panel.on_download_failed(DownloadFailed("RELIANCE", "15m", "rate limited"))
    assert not panel.panel.busy
    assert "rate limited" in panel.log.toPlainText()


def test_panel_renders_coverage_facts() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.on_download_coverage(
        DownloadCoverage(
            symbol="RELIANCE",
            interval="15m",
            state="DOWNLOAD_COMPLETE",
            earliest="2025-01-01 09:15:00",
            latest="2026-08-01 15:30:00",
            row_count=5000,
            trading_days=500,
            coverage_pct=100.0,
            missing_head=False,
            missing_tail=False,
            listing_start_verified=True,
        )
    )
    text = panel.status._coverage_label.text()
    assert "100.0%" in text
    assert "5000 rows" in text


def test_panel_provider_line() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.set_provider(False, "kiteconnect is not installed")
    assert "not ready" in panel.status._provider_label.text()


def test_panel_has_header_with_title_and_close_control() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    header = panel.findChild(QWidget, "panelHeader")
    assert header is not None
    assert [label.text() for label in header.findChildren(QLabel)] == [
        "Historical Download",
        "Market Data Acquisition",
    ]
    close = header.findChild(QToolButton, "panelClose")
    assert close is not None
    assert close.toolTip() == "Close panel"
    emitted: list[bool] = []
    panel.close_requested.connect(lambda: emitted.append(True))
    close.click()
    assert emitted == [True]


def test_download_without_selection_is_ignored_by_panel() -> None:
    bus = EventBus()
    panel = HistoricalDownloadPanel(bus)
    received: list[DownloadRequest] = []
    bus.subscribe(DownloadRequest, received.append)
    # Guard lives in the panel: clicking download with no stock selected is a no-op.
    panel.panel._download_button.click()
    assert received == []


def test_download_uses_first_selected_stock() -> None:
    bus = EventBus()
    panel = HistoricalDownloadPanel(bus)
    received: list[DownloadRequest] = []
    bus.subscribe(DownloadRequest, received.append)
    panel.panel.set_symbols(("AMBUJACEM", "BPCL"))
    panel.panel._stocks.select_all()
    panel.panel._download_button.click()
    assert len(received) == 1
    assert received[0].symbol == "AMBUJACEM"


def test_coverage_uses_first_selected_stock() -> None:
    bus = EventBus()
    panel = HistoricalDownloadPanel(bus)
    received: list[CoverageRequest] = []
    bus.subscribe(CoverageRequest, received.append)
    panel.panel.set_symbols(("AMBUJACEM", "BPCL"))
    panel.panel._stocks.select_all()
    panel.panel._coverage_button.click()
    assert received == [CoverageRequest("AMBUJACEM", "15m")]


def test_panel_busy_disables_stock_checklist() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    assert panel.panel._stocks.isEnabled()
    panel.show()
    QApplication.processEvents()
    panel.on_download_started(DownloadStarted("RELIANCE", "15m", "2026-01-01", "2026-01-05", 1))
    assert panel.panel.busy
    assert not panel.panel._stocks.isEnabled()
    assert panel.panel._cancel_button.isVisible()
    panel.on_download_completed(DownloadCompleted("RELIANCE", "15m", 100, 100, 5))
    assert panel.panel._stocks.isEnabled()
    assert not panel.panel._cancel_button.isVisible()


def test_quick_range_buttons_set_from_and_to() -> None:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QPushButton

    panel = HistoricalDownloadPanel(EventBus())
    buttons = {
        button.text(): button
        for button in panel.panel.findChildren(QPushButton)
        if button.text() in {"1M", "3M", "6M", "1Y", "MAX"}
    }
    assert set(buttons) == {"1M", "3M", "6M", "1Y", "MAX"}
    today = QDate.currentDate()
    buttons["1M"].click()
    assert panel.panel._from_edit.date() == today.addMonths(-1)
    assert panel.panel._to_edit.date() == today
    buttons["MAX"].click()
    assert panel.panel._from_edit.date() == today.addYears(-10)
    assert panel.panel._to_edit.date() == today


def test_plan_tracks_selection_interval_and_range() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.panel.set_symbols(("AMBUJACEM", "BPCL"))
    assert panel.panel._plan_stocks.text() == "0"
    panel.panel._stocks.select_all()
    assert panel.panel._plan_stocks.text() == "2"
    assert panel.panel._plan_interval.text() == "15m"
    index = panel.panel._interval.findData("1m")
    panel.panel._interval.setCurrentIndex(index)
    assert panel.panel._plan_interval.text() == "1m"
    from PySide6.QtCore import QDate

    panel.panel._from_edit.setDate(QDate(2026, 1, 1))
    panel.panel._to_edit.setDate(QDate(2026, 1, 30))
    assert panel.panel._plan_range.text() == "01 Jan 2026 → 30 Jan 2026"
    assert panel.panel._plan_days.text() != "—"
    assert panel.panel._plan_rows.text().startswith("~")


def test_default_dates_are_2017_to_today() -> None:
    from PySide6.QtCore import QDate

    panel = HistoricalDownloadPanel(EventBus())
    today = QDate.currentDate()
    assert panel.panel._from_edit.date() == QDate(2017, 1, 1)
    assert panel.panel._to_edit.date() == today
    assert panel.panel._from_edit.text() == "01 Jan 2017"
    assert panel.panel._from_edit.date().toString("yyyy-MM-dd") == "2017-01-01"
    assert panel.panel._to_edit.date().toString("yyyy-MM-dd") == today.toString("yyyy-MM-dd")
    assert panel.panel._plan_range.text() == f"01 Jan 2017 → {today.toString('dd MMM yyyy')}"
    assert panel.panel._plan_range.text().startswith("01 Jan 2017")


def test_panel_is_one_scrollable_workspace() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea, QWidget

    panel = HistoricalDownloadPanel(EventBus())
    panel.show()
    QApplication.processEvents()
    mains = [s for s in panel.findChildren(QScrollArea) if s.parent() is panel]
    assert len(mains) == 1
    scroll = mains[0]
    content = scroll.widget()
    assert content is not None
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    names = {w.objectName() for w in content.findChildren(QWidget)}
    assert "panelHeader" in names

    panel.setFixedSize(220, 500)
    QApplication.processEvents()
    vbar = scroll.verticalScrollBar()
    assert vbar.maximum() > 0
    vbar.setValue(vbar.maximum())
    QApplication.processEvents()
    vp = scroll.viewport().height()
    assert panel.status.y() + panel.status.height() - vbar.value() <= vp
    assert panel.log_panel.y() + panel.log_panel.height() - vbar.value() <= vp


def test_invalid_date_range_disables_download() -> None:
    from PySide6.QtCore import QDate

    panel = HistoricalDownloadPanel(EventBus())
    panel.panel._from_edit.setDate(QDate(2026, 8, 17))
    panel.panel._to_edit.setDate(QDate(2026, 1, 1))
    assert not panel.panel._download_button.isEnabled()
    assert panel.panel._plan_error.text() == "From date is after To date"
    panel.panel._to_edit.setDate(QDate(2026, 8, 18))
    assert panel.panel._download_button.isEnabled()
    assert panel.panel._plan_error.text() == ""


def test_chips_show_compact_selection_and_count_note_beyond_limit() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.show()
    QApplication.processEvents()
    symbols = tuple(f"S{i:02d}" for i in range(1, 11))
    panel.panel.set_symbols(symbols)
    for symbol in symbols[:3]:
        panel.panel._stocks.set_selected(symbol, True)
    assert panel.panel._stocks._chips.isVisible()
    panel.panel._stocks.select_all()
    assert not panel.panel._stocks._chips.isVisible()
    assert panel.panel._stocks._selected_note.text() == "10 stocks selected"
    panel.panel._stocks._chips.remove_requested.emit("S01")
    assert panel.panel._stocks.is_selected(0) is False


def test_completion_card_offers_view_coverage() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.show()
    QApplication.processEvents()
    panel.on_download_started(DownloadStarted("RELIANCE", "15m", "2026-01-01", "2026-01-05", 3))
    panel.on_download_progress(
        DownloadProgress("RELIANCE", "15m", 2, 3, "2026-01-01", "2026-01-02", 100, 400)
    )
    panel.on_download_completed(DownloadCompleted("RELIANCE", "15m", 400, 800, 8))
    status = panel.status
    assert status._complete_card.isVisible()
    assert "✓ RELIANCE processed" in status._complete_status.text()
    assert status._complete_rows.text() == "800"
    received: list[CoverageRequest] = []
    panel._bus.subscribe(CoverageRequest, received.append)
    status._complete_coverage_button.click()
    assert received == [CoverageRequest("RELIANCE", "15m")]


def test_failed_card_retry_reuses_last_range() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.show()
    QApplication.processEvents()
    panel.on_download_started(DownloadStarted("TCS", "5minute", "2026-02-01", "2026-02-10", 2))
    panel.on_download_failed(DownloadFailed("TCS", "5minute", "API rate limit reached"))
    status = panel.status
    assert status._error_card.isVisible()
    assert "rate limit" in status._error_line.text()
    received: list[DownloadRequest] = []
    panel._bus.subscribe(DownloadRequest, received.append)
    status._retry_button.click()
    assert received == [DownloadRequest("TCS", "5minute", "2026-02-01", "2026-02-10")]


def test_provider_card_is_compact_and_hides_raw_reason() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.show()
    QApplication.processEvents()
    panel.set_provider(
        False,
        "Zerodha API credentials not configured — set the VAYREN_ZERODHA_* environment variables",
    )
    status = panel.status
    assert "not ready" in status._provider_label.text()
    assert status._provider_status.text() == "● Not Configured"
    assert status._configure_button.isVisible()
    assert "VAYREN_ZERODHA_*" not in status._provider_label.text()
    assert "VAYREN_ZERODHA_*" in status._provider_detail.toPlainText()
    panel.set_provider(True, "ready")
    assert status._provider_status.text() == "● Connected"
    assert not status._configure_button.isVisible()


def test_log_panel_collapses_and_clears() -> None:
    panel = HistoricalDownloadPanel(EventBus())
    panel.show()
    QApplication.processEvents()
    log_panel = panel.log_panel
    assert not log_panel.expanded
    assert not log_panel.log.isVisible()
    panel.log.append_line("09:31:02  INFO  Download started")
    assert "Download started" in panel.log.toPlainText()
    log_panel._toggle.click()
    assert log_panel.expanded
    assert log_panel.log.isVisible()
    log_panel._clear_button.click()
    assert panel.log.toPlainText() == ""
