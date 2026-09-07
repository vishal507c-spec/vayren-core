"""Broker selector UI (M4): pure view over injected registry facts.

The widget never validates or persists — it shows choices, emits the
requested name, and renders whatever the composition root feeds back.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.event_bus.event_bus import EventBus

from data.ui.historical_panel import HistoricalDownloadPanel

_CHOICES = (
    {
        "name": "paper",
        "display_name": "Paper",
        "domains": {"historical_data": False, "market_data": False, "trading": True},
        "capabilities": ("orders.market",),
    },
    {
        "name": "sandbox",
        "display_name": "Sandbox",
        "domains": {"historical_data": False, "market_data": False, "trading": True},
        "capabilities": ("orders.market",),
    },
    {
        "name": "zerodha",
        "display_name": "Zerodha",
        "domains": {"historical_data": True, "market_data": False, "trading": False},
        "capabilities": ("historical_data.candles",),
    },
)


@dataclass
class _FakeSelection:
    name: str
    environment: str
    reason: str


def _panel() -> HistoricalDownloadPanel:
    return HistoricalDownloadPanel(EventBus())


def test_selector_populated_from_registry_facts() -> None:
    panel = _panel()
    panel.set_broker_choices(_CHOICES)
    combo = panel.status._broker_combo
    assert combo.count() == 3
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Paper",
        "Sandbox",
        "Zerodha",
    ]


def test_selection_display_shows_capabilities_and_reason() -> None:
    panel = _panel()
    panel.set_broker_choices(_CHOICES)
    panel.set_broker_selection(_FakeSelection("zerodha", "paper", "user-selected"))
    assert panel.status._broker_combo.currentText() == "Zerodha"
    caps_text = panel.status._broker_caps.text()
    assert "Historical ✓" in caps_text
    assert "Trading ✗" in caps_text  # never shows unsupported as available
    assert "user-selected" in caps_text


def test_user_choice_emits_requested_name() -> None:
    panel = _panel()
    panel.set_broker_choices(_CHOICES)
    received: list[str] = []
    panel.broker_selected.connect(received.append)
    panel.status._on_broker_activated(1)  # Sandbox row
    assert received == ["sandbox"]


def test_rejected_selection_shown_previous_kept() -> None:
    panel = _panel()
    panel.set_broker_choices(_CHOICES)
    panel.set_broker_selection(_FakeSelection("sandbox", "sandbox", "user-selected"))
    panel.show_broker_error("unknown broker 'ghost'")
    assert panel.status._broker_error.isVisible() or panel.status._broker_error.text()
    assert "unknown broker" in panel.status._broker_error.text()
    # The combo still shows the previous valid selection (not reset).
    assert panel.status._broker_combo.currentText() == "Sandbox"


def test_clearing_error_hides_label() -> None:
    panel = _panel()
    panel.set_broker_choices(_CHOICES)
    panel.show_broker_error("boom")
    panel.show_broker_error("")
    assert panel.status._broker_error.text() == ""
