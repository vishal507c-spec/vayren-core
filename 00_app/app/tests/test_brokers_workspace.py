"""BrokersWorkspace proofs — control-center hierarchy, states, actions emit signals."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QPushButton

from app.ui.brokers_workspace import BrokersWorkspace


def _state(**overrides: Any) -> dict[str, Any]:
    card = {
        "id": "zerodha",
        "name": "Zerodha",
        "venue_subtitle": "Kite Connect",
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
        "account_id": "",
        "funds": {"available": None, "used": None, "total": None},
        "positions_open": None,
        "orders_open": None,
        "last_sync": "08:49:32",
        "can_refresh": True,
    }
    card.update(overrides)
    return {"brokers": [card], "callback_url": "http://127.0.0.1:9474/vayren/callback"}


def _close_dialogs(ws: BrokersWorkspace) -> None:
    for dialog in list(ws.findChildren(QDialog)):
        dialog.close()


def test_hero_answers_connection_first(qt_app, tmp_path) -> None:  # noqa: ARG001
    assert qt_app is not None
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    card = ws._cards["zerodha"]
    assert card._status.text() == "○ LOGIN REQUIRED"
    assert "log in to reconnect" in card._reason.text()
    assert "session expired" in card._reason.text()
    assert card._name_label.text() == "ZERODHA"
    assert "Kite Connect" in card._sub_label.text()
    assert card._hero_conn_value.text() == "Not connected"
    assert card._hero_sync_value.text() == "08:49:32"
    assert card._login_btn.isVisibleTo(card)
    assert card._login_btn.isEnabled()
    assert card._login_btn.text().startswith("CONNECT")
    ws._timer.stop()
    _close_dialogs(ws)


def test_health_grid_semantics_not_color_only(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    card = ws._cards["zerodha"]
    labels = {k: v.text() for k, v in card._check_labels.items()}
    assert labels["connection"] == "✓ Ready"
    assert labels["market_data"] == "⚠ Check failed"
    assert card._health_summary.text() == "⚠ PARTIAL"
    ws._timer.stop()
    _close_dialogs(ws)


def test_connected_state_metrics_and_primary(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(
        state_provider=lambda: _state(
            status="CONNECTED",
            reason="all checks passed",
            checks={
                "connection": "READY",
                "account": "READY",
                "funds": "READY",
                "positions": "READY",
                "orders": "READY",
                "market_data": "READY",
            },
            can_login=False,
            can_disconnect=True,
            account_id="AB1234",
            funds={"available": 124500.0, "used": 10000.0, "total": 134500.0},
            positions_open=3,
            orders_open=2,
        )
    )
    ws.refresh()
    card = ws._cards["zerodha"]
    assert card._status.text() == "● CONNECTED"
    assert card._hero_account_value.text() == "AB1234"
    assert card._hero_conn_value.text() == "Healthy"
    assert card._health_summary.text() == "✓ HEALTHY"
    assert card._metric_account.text() == "AB1234"
    assert card._metric_funds.text() == "₹124,500"
    assert card._metric_positions.text() == "3"
    assert card._metric_positions_sub.text() == "3 open"
    assert card._metric_orders.text() == "2"
    assert card._login_btn.text() == "RE-AUTHENTICATE"
    assert not card._login_btn.isEnabled()
    assert card._disconnect_btn.isEnabled()
    assert card._refresh_btn.isEnabled()
    assert "00C7B7" in card._refresh_btn.styleSheet()
    ws._timer.stop()
    _close_dialogs(ws)


def test_not_configured_connect_primary(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(
        state_provider=lambda: _state(
            status="NOT_CONFIGURED",
            reason="no configuration saved",
            checks={},
            configured=False,
            api_key_masked="",
            can_login=False,
            can_disconnect=False,
            last_sync="",
            can_refresh=False,
        )
    )
    ws.refresh()
    card = ws._cards["zerodha"]
    assert card._status.text() == "○ NOT CONFIGURED"
    assert "Connect a broker" in card._reason.text()
    assert card._login_btn.text() == "CONNECT ZERODHA"
    assert card._login_btn.isEnabled()
    assert "00C7B7" in card._login_btn.styleSheet()
    assert not card._refresh_btn.isVisibleTo(card)
    assert not card._disconnect_btn.isVisibleTo(card)
    assert "Not configured" in card._key_label.text()
    assert card._metric_funds_sub.text() == "Not available"
    ws._timer.stop()
    _close_dialogs(ws)


def test_transient_states_show_progress(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: _state(status="AUTHENTICATING", reason="waiting"))
    ws.refresh()
    card = ws._cards["zerodha"]
    assert card._status.text() == "◐ CONNECTING"
    assert card._login_btn.text() == "CONNECTING…"
    assert not card._login_btn.isEnabled()
    assert not card._refresh_btn.isEnabled()
    ws._timer.stop()
    _close_dialogs(ws)


def test_degraded_partial_summary(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(
        state_provider=lambda: _state(
            status="ACCOUNT_NOT_READY",
            reason="checks failed: market_data",
            can_login=False,
            can_disconnect=False,
        )
    )
    ws.refresh()
    card = ws._cards["zerodha"]
    assert card._status.text() == "◐ DEGRADED"
    assert card._health_summary.text() == "⚠ PARTIAL"
    assert card._hero_conn_value.text() == "Degraded"
    ws._timer.stop()
    _close_dialogs(ws)


def test_empty_state(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: {"brokers": [], "callback_url": ""})
    ws.refresh()
    assert ws._empty_host.isVisibleTo(ws)
    assert ws._empty_label is not None
    assert "No brokers connected" in ws._empty_label.text()
    labels = list(ws._cards_host.findChildren(QLabel))
    assert not any("No broker configured" in lbl.text() for lbl in labels)
    ws._timer.stop()
    _close_dialogs(ws)


def test_header_and_callback_not_prominent(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    titles = [w.text() for w in ws.findChildren(QLabel) if "SYSTEM" in w.text()]
    assert any("SYSTEM / BROKERS" in title for title in titles)
    subtitles = [w.text() for w in ws.findChildren(QLabel) if "infrastructure" in w.text()]
    assert subtitles
    assert "127.0.0.1" not in " ".join(subtitles)
    assert ws._add_btn.text() == "+ ADD BROKER"
    card = ws._cards["zerodha"]
    assert not card._integration_host.isVisible()
    assert "127.0.0.1" in card._callback_label.text()
    ws._timer.stop()
    _close_dialogs(ws)


def test_actions_emit_signals(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: _state(can_disconnect=True))
    ws.refresh()
    seen: list[tuple] = []
    ws.login_requested.connect(lambda b: seen.append(("login", b)))
    ws.refresh_requested.connect(lambda b: seen.append(("refresh", b)))
    ws.disconnect_requested.connect(lambda b: seen.append(("disconnect", b)))
    ws.remove_requested.connect(lambda b: seen.append(("remove", b)))
    card = ws._cards["zerodha"]
    card._login_btn.click()
    card._refresh_btn.click()
    card._disconnect_btn.click()
    card._remove_btn.click()
    assert ("login", "zerodha") in seen
    assert ("refresh", "zerodha") in seen
    assert ("disconnect", "zerodha") in seen
    assert ("remove", "zerodha") in seen
    ws._timer.stop()
    _close_dialogs(ws)


def test_login_opens_step_dialog(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: _state(can_disconnect=True))
    ws.refresh()
    card = ws._cards["zerodha"]
    card._login_btn.click()
    assert "zerodha" in ws._connect_dialogs
    ws._timer.stop()
    _close_dialogs(ws)


def test_configure_form_emits_values_once(qt_app, tmp_path) -> None:  # noqa: ARG001
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
    _close_dialogs(ws)


def test_add_dialog_lists_available_brokers(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    ws._open_add_dialog()
    dialogs = ws.findChildren(QDialog)
    assert dialogs
    text = " ".join(w.text() for d in dialogs for w in d.findChildren(QLabel))
    assert "ZERODHA" in text
    ws._timer.stop()
    _close_dialogs(ws)


def test_secret_never_displayed_in_ui(qt_app, tmp_path) -> None:  # noqa: ARG001
    ws = BrokersWorkspace(state_provider=lambda: _state())
    ws.refresh()
    text = " ".join(w.text() for w in ws.findChildren(QLabel) if w.text()) + " ".join(
        e.text() for e in ws.findChildren(QLineEdit)
    )
    assert "S-super-secret" not in text
    assert "K123…90" in text
    ws._timer.stop()
    _close_dialogs(ws)
