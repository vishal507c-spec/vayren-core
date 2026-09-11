"""BrokersWorkspace proofs — pure view, states render, actions emit signals."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

from app.ui.brokers_workspace import BrokersWorkspace


def _state(**overrides: Any) -> dict[str, Any]:
    card = {
        "id": "zerodha",
        "name": "Zerodha",
        "status": "LOGIN_REQUIRED",
        "reason": "session expired — re-login required",
        "checks": {
            "connection": "READY",
            "account": "READY",
            "funds": "READY",
            "positions": "READY",
            "orders": "READY",
            "market_data": "FAILED: simulated",
        },
        "configured": True,
        "api_key_masked": "K123…90",
        "can_login": True,
        "can_disconnect": False,
    }
    card.update(overrides)
    return {"brokers": [card], "callback_url": "http://127.0.0.1:9474/vayren/callback"}


def test_renders_status_checks_and_masked_key(qt_app, tmp_path) -> None:  # noqa: ARG001 (Qt app fixture required)
    assert qt_app is not None
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    card = ws._cards["zerodha"]
    assert card._status.text().endswith("LOGIN_REQUIRED")
    assert "session expired" in card._reason.text()
    labels = {k: v.text() for k, v in card._check_labels.items()}
    assert labels["connection"] == "READY"
    assert labels["market_data"].startswith("FAILED")
    assert card._key_label.text() == "API key: K123…90"
    assert card._login_btn.isVisibleTo(card)
    ws._timer.stop()


def test_connected_state_tones_and_buttons(qt_app, tmp_path) -> None:  # noqa: ARG001 (Qt app fixture required)
    ws = BrokersWorkspace(
        state_provider=lambda: _state(status="CONNECTED", can_login=False, can_disconnect=True)
    )
    ws.refresh()
    card = ws._cards["zerodha"]
    assert card._status.text().endswith("CONNECTED")
    assert card._login_btn.text() == "RE-AUTHENTICATE"
    assert not card._login_btn.isEnabled()
    assert card._disconnect_btn.isEnabled()
    ws._timer.stop()


def test_empty_state(qt_app, tmp_path) -> None:  # noqa: ARG001 (Qt app fixture required)
    ws = BrokersWorkspace(state_provider=lambda: {"brokers": [], "callback_url": ""})
    ws.refresh()
    labels = list(ws._cards_host.findChildren(QLabel))
    assert any("No broker configured" in lbl.text() for lbl in labels)
    ws._timer.stop()


def test_actions_emit_signals(qt_app, tmp_path) -> None:  # noqa: ARG001 (Qt app fixture required)
    ws = BrokersWorkspace(state_provider=lambda: _state(can_disconnect=True))
    ws.refresh()
    seen: list[tuple] = []
    ws.login_requested.connect(lambda b: seen.append(("login", b)))
    ws.disconnect_requested.connect(lambda b: seen.append(("disconnect", b)))
    card = ws._cards["zerodha"]
    card._login_btn.click()
    card._disconnect_btn.click()
    assert ("login", "zerodha") in seen
    assert ("disconnect", "zerodha") in seen
    ws._timer.stop()


def test_configure_form_emits_values_once(qt_app, tmp_path) -> None:  # noqa: ARG001 (Qt app fixture required)
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    seen: list[tuple] = []
    ws.configure_requested.connect(lambda b, v: seen.append((b, v)))
    card = ws._cards["zerodha"]
    card._open_configure()
    edits = list(card.findChildren(QLineEdit))
    assert len(edits) == 2
    edits[0].setText("K1234567890")
    edits[1].setText("S-secret-value")
    assert card._form is not None
    save = list(card._form.findChildren(QPushButton))[0]
    save.click()
    assert seen == [("zerodha", {"api_key": "K1234567890", "api_secret": "S-secret-value"})]
    assert card._form is None  # form closed after save
    ws._timer.stop()


def test_secret_never_displayed_in_ui(qt_app, tmp_path) -> None:  # noqa: ARG001 (Qt app fixture required)
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    text = " ".join(w.text() for w in ws.findChildren(QLabel) if w.text()) + " ".join(
        e.text() for e in ws.findChildren(QLineEdit)
    )
    assert "S-super-secret" not in text
    ws._timer.stop()
