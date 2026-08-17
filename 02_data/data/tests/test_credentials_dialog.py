"""ProviderCredentialsDialog — masked fields, eye reveal, test/save/clear flows."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLineEdit

from data.provider.credentials_store import FileCredentialStore
from data.provider.manager import ProviderCredentialsManager
from data.provider.zerodha.adapter import ZerodhaProvider
from data.tests.conftest import make_settings
from data.tests.test_credentials_manager import StubProvider, _values
from data.ui.credentials_dialog import ProviderCredentialsDialog, _SecretEdit


def make_dialog(
    tmp_path, provider=None
) -> tuple[ProviderCredentialsDialog, ProviderCredentialsManager]:
    settings = make_settings(tmp_path)
    provider = provider or StubProvider()
    manager = ProviderCredentialsManager(settings, provider, store=FileCredentialStore(tmp_path))
    return ProviderCredentialsDialog(manager), manager


# ── structure ────────────────────────────────────────────────────────────────


def test_dialog_title_uses_provider_display_name(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    assert dialog.windowTitle() == "Configure Stub"


def test_dialog_renders_provider_fields_dynamically(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    assert list(dialog._edits) == ["api_key", "api_secret", "user_id"]


def test_secret_fields_start_masked(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    secret: QLineEdit = dialog._edits["api_secret"]
    assert isinstance(secret, _SecretEdit)
    assert secret.echoMode() == QLineEdit.EchoMode.Password
    assert not secret.is_revealed()


def test_non_secret_field_is_plain(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    assert dialog._edits["api_key"].echoMode() == QLineEdit.EchoMode.Normal


def test_dialog_prefills_stored_values(tmp_path) -> None:
    dialog, manager = make_dialog(tmp_path)
    manager.save(_values())
    dialog2, _ = make_dialog(tmp_path)
    assert dialog2._edits["api_key"].text() == "key123"
    assert dialog2._edits["api_secret"].text() == "secret-abc"


def test_clear_button_visible_only_when_values_stored(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    assert dialog._clear_button.isHidden()
    make_manager_with_saved_values(tmp_path)
    dialog2, _ = make_dialog(tmp_path)
    assert not dialog2._clear_button.isHidden()


def make_manager_with_saved_values(tmp_path) -> ProviderCredentialsManager:
    settings = make_settings(tmp_path)
    manager = ProviderCredentialsManager(
        settings, StubProvider(), store=FileCredentialStore(tmp_path)
    )
    manager.save(_values())
    return manager


# ── eye reveal / re-mask ────────────────────────────────────────────────────


def test_eye_toggle_reveals_then_hides_secret(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    secret = dialog._edits["api_secret"]
    eye = dialog._eyes["api_secret"]
    eye.click()
    assert secret.echoMode() == QLineEdit.EchoMode.Normal
    eye.click()
    assert secret.echoMode() == QLineEdit.EchoMode.Password


def test_focus_out_re_masks_revealed_secret(tmp_path) -> None:
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    dialog, _ = make_dialog(tmp_path)
    secret = dialog._edits["api_secret"]
    assert isinstance(secret, _SecretEdit)
    secret.set_revealed(True)
    assert secret.echoMode() == QLineEdit.EchoMode.Normal
    secret.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert not secret.is_revealed()
    assert secret.echoMode() == QLineEdit.EchoMode.Password


# ── test connection ─────────────────────────────────────────────────────────


def test_test_connection_success_shows_success_text(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    dialog._edits["api_key"].setText("key123")
    dialog._edits["api_secret"].setText("secret-abc")
    dialog._on_test()
    assert dialog._status_line.text() == "✓ Connection successful"
    assert "ready" in dialog._status_sub.text()


def test_test_connection_failure_shows_generic_message(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path, provider=StubProvider(ready=False))
    dialog._edits["api_key"].setText("key123")
    dialog._edits["api_secret"].setText("secret-abc")  # valid values, provider unavailable
    dialog._on_test()
    assert dialog._status_line.text() == "✕ Connection failed"
    assert "Authentication failed" in dialog._status_sub.text()
    assert dialog._edits["api_secret"].text() not in dialog._status_sub.text()


def test_test_connection_validates_required_fields_first(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    dialog._edits["api_key"].setText("  ")
    dialog._on_test()
    assert dialog._status_line.text() == "✕ API Key is required."


# ── save & connect ──────────────────────────────────────────────────────────


def test_save_and_connect_persists_and_accepts(tmp_path) -> None:
    dialog, manager = make_dialog(tmp_path)
    dialog._edits["api_key"].setText("  k  ")
    dialog._edits["api_secret"].setText("  s  ")
    dialog._on_save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert manager.load_values()["api_key"] == "k"
    assert manager.load_values()["api_secret"] == "s"


def test_save_with_missing_required_keeps_dialog_open(tmp_path) -> None:
    dialog, manager = make_dialog(tmp_path)
    dialog._edits["api_key"].setText("k")
    dialog._edits["api_secret"].setText("")
    dialog._on_save()
    assert dialog._status_line.text() == "✕ API Secret is required."
    assert not manager.has_stored()


def test_save_never_shows_secret_in_error(tmp_path) -> None:
    dialog, _ = make_dialog(tmp_path)
    dialog._edits["api_key"].setText("SUPERSECRETVALUE")
    dialog._edits["api_secret"].setText("")
    dialog._on_save()
    assert "SUPERSECRETVALUE" not in dialog._status_line.text()


# ── clear credentials ───────────────────────────────────────────────────────


def test_clear_requires_confirmation(tmp_path) -> None:
    dialog, manager = make_dialog(tmp_path)
    manager.save(_values())
    dialog._confirm_clear = lambda: False
    dialog._on_clear()
    assert manager.has_stored()


def test_clear_removes_values_after_confirmation(tmp_path) -> None:
    dialog, manager = make_dialog(tmp_path)
    manager.save(_values())
    dialog._confirm_clear = lambda: True
    dialog._on_clear()
    assert not manager.has_stored()
    assert dialog._status_line.text() == "● Not Configured"
    assert dialog._edits["api_secret"].text() == ""
    assert dialog._clear_button.isHidden()


def test_clear_does_not_touch_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_ZERODHA_API_KEY", "env-key")
    monkeypatch.setenv("VAYREN_ZERODHA_API_SECRET", "env-secret")
    monkeypatch.setattr(
        "data.provider.credentials_store.default_store",
        lambda _data_dir: FileCredentialStore(tmp_path),
    )
    dialog, manager = make_dialog(tmp_path, provider=ZerodhaProvider(make_settings(tmp_path)))
    manager.save(_values())
    dialog._confirm_clear = lambda: True
    dialog._on_clear()
    assert not manager.has_stored()
    assert manager.reload()[0] is True  # env fallback untouched


# ── status refresh contract ─────────────────────────────────────────────────


def test_status_view_restores_provider_after_dialog(tmp_path) -> None:
    """After the dialog closes, the status card reflects persisted state."""
    from data.ui.status_view import StatusView

    settings = make_settings(tmp_path)
    provider = StubProvider()
    manager = ProviderCredentialsManager(settings, provider, store=FileCredentialStore(tmp_path))
    view = StatusView()
    view.set_credentials_manager(manager)
    manager.save(_values())
    # simulate the post-dialog reload path used by _show_credentials_dialog
    ready, reason = manager.reload()
    view.set_provider(ready, reason)
    assert view._provider_status.text() == "● Connected"
    assert not view._configure_button.isVisible()
