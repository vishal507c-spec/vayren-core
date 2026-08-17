"""ProviderCredentialsManager — validation, storage layering, provider reload.

The manager is provider-agnostic: every test drives it through a small
contract-shaped provider stub (no Zerodha code), plus the real layered
loader tests against the actual Zerodha credential object.
"""

from __future__ import annotations

from typing import Any

import pytest

from data.provider.credentials import CredentialField, ProviderConfigError
from data.provider.credentials_store import FileCredentialStore
from data.provider.manager import ProviderCredentialsManager
from data.provider.zerodha.adapter import ZerodhaProvider
from data.provider.zerodha.credentials import load_zerodha_credentials
from data.tests.conftest import make_settings


class StubProvider:
    """Contract-shaped provider with the manager's credential surface."""

    display_name = "Stub"
    credential_fields = (
        CredentialField("api_key", "API Key", secret=False, required=True),
        CredentialField("api_secret", "API Secret", secret=True, required=True),
        CredentialField("user_id", "User ID", secret=True, required=False),
    )

    def __init__(self, ready: bool = True) -> None:
        self.loaded: list[dict[str, str] | None] = []
        self._ready = ready

    def available(self) -> tuple[bool, str]:
        if not self._ready:
            return False, "stub not ready"
        if not self.loaded or not self.loaded[-1]:
            return False, "no credentials"
        values = self.loaded[-1]
        if not (values.get("api_key") and values.get("api_secret")):
            return False, "no credentials"
        return True, "ready"

    def build_credentials(self, values: dict[str, str]) -> dict[str, str]:
        return {k: v.strip() for k, v in values.items()}

    def reload_credentials(self, credentials: dict[str, str] | None = None) -> None:
        if credentials is not None:
            self.loaded.append(credentials)
        elif not self.loaded:
            self.loaded.append(None)


def make_manager(tmp_path, provider: Any | None = None) -> ProviderCredentialsManager:
    settings = make_settings(tmp_path, provider="zerodha")
    return ProviderCredentialsManager(
        settings, provider or StubProvider(), store=FileCredentialStore(tmp_path)
    )


def _values(**overrides: str) -> dict[str, str]:
    values = {"api_key": " key123 ", "api_secret": " secret-abc ", "user_id": ""}
    values.update(overrides)
    return values


# ── validation ───────────────────────────────────────────────────────────────


def test_validate_returns_none_when_required_present(tmp_path) -> None:
    assert make_manager(tmp_path).validate(_values()) is None


def test_validate_reports_missing_required_field_label_only(tmp_path) -> None:
    manager = make_manager(tmp_path)
    assert manager.validate(_values(api_key="  ")) == "API Key is required."
    assert manager.validate(_values(api_secret="")) == "API Secret is required."
    # optional fields never block
    assert manager.validate(_values(user_id="")) is None


# ── save / load / clear round-trip ──────────────────────────────────────────


def test_save_persists_trimmed_values_and_reports_ready(tmp_path) -> None:
    manager = make_manager(tmp_path)
    ready, reason = manager.save(_values())
    assert ready is True
    assert reason == "ready"
    assert manager.load_values() == {
        "api_key": "key123",
        "api_secret": "secret-abc",
        "user_id": "",
    }


def test_save_with_empty_required_raises_without_values(tmp_path) -> None:
    manager = make_manager(tmp_path)
    with pytest.raises(ProviderConfigError, match="API Key is required."):
        manager.save(_values(api_key=""))
    assert manager.load_values()["api_key"] == ""


def test_clear_removes_stored_values(tmp_path, monkeypatch) -> None:
    for var in (
        "VAYREN_ZERODHA_API_KEY",
        "VAYREN_ZERODHA_API_SECRET",
        "VAYREN_ZERODHA_USER_ID",
        "VAYREN_ZERODHA_PASSWORD",
        "VAYREN_ZERODHA_TOTP_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "data.provider.credentials_store.default_store",
        lambda _data_dir: FileCredentialStore(tmp_path),
    )
    settings = make_settings(tmp_path)
    provider = ZerodhaProvider(settings)
    manager = ProviderCredentialsManager(settings, provider, store=FileCredentialStore(tmp_path))
    manager.save(_values())
    assert manager.has_stored()
    ready, reason = manager.clear()
    assert ready is False
    assert "no credentials" in reason or "not configured" in reason
    assert not manager.has_stored()


def test_clear_keeps_environment_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_ZERODHA_API_KEY", "env-key")
    monkeypatch.setenv("VAYREN_ZERODHA_API_SECRET", "env-secret")
    monkeypatch.setattr(
        "data.provider.credentials_store.default_store",
        lambda _data_dir: FileCredentialStore(tmp_path),
    )
    settings = make_settings(tmp_path)
    provider = ZerodhaProvider(settings)
    manager = ProviderCredentialsManager(settings, provider, store=FileCredentialStore(tmp_path))
    manager.save(_values())
    manager.clear()
    # env fallback still provides the key pair → provider reports ready
    assert manager.reload()[0] is True


# ── layered loading (store → env → not configured) ──────────────────────────


def test_loader_prefers_stored_values_over_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_ZERODHA_API_KEY", "env-key")
    monkeypatch.setenv("VAYREN_ZERODHA_API_SECRET", "env-secret")
    monkeypatch.setenv("VAYREN_ZERODHA_USER_ID", "env-user")
    store = FileCredentialStore(tmp_path)
    store.save("vayren:zerodha", {"api_key": "stored-key", "api_secret": "stored-secret"})
    settings = make_settings(tmp_path)
    creds = load_zerodha_credentials(settings, store)
    assert creds.api_key == "stored-key"
    assert creds.api_secret == "stored-secret"
    # missing store field falls back to env
    assert creds.user_id == "env-user"


def test_loader_falls_back_to_env_when_store_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_ZERODHA_API_KEY", "env-key")
    monkeypatch.setenv("VAYREN_ZERODHA_API_SECRET", "env-secret")
    settings = make_settings(tmp_path)
    creds = load_zerodha_credentials(settings, FileCredentialStore(tmp_path))
    assert creds.api_key == "env-key"
    assert creds.api_secret == "env-secret"
    assert creds.configured


def test_loader_unconfigured_when_nothing_available(tmp_path, monkeypatch) -> None:
    for var in (
        "VAYREN_ZERODHA_API_KEY",
        "VAYREN_ZERODHA_API_SECRET",
        "VAYREN_ZERODHA_USER_ID",
        "VAYREN_ZERODHA_PASSWORD",
        "VAYREN_ZERODHA_TOTP_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = make_settings(tmp_path)
    creds = load_zerodha_credentials(settings, FileCredentialStore(tmp_path))
    assert not creds.configured


# ── provider reload surface ─────────────────────────────────────────────────


def test_build_credentials_trims_and_maps_values() -> None:
    creds = ZerodhaProvider.build_credentials(
        {"api_key": " k ", "api_secret": " s ", "user_id": " u ", "totp_secret": " t "}
    )
    assert (creds.api_key, creds.api_secret, creds.user_id, creds.totp_secret) == (
        "k",
        "s",
        "u",
        "t",
    )


def test_reload_credentials_rebuilds_auth_with_values(tmp_path) -> None:
    settings = make_settings(tmp_path)
    provider = ZerodhaProvider(settings)
    creds = ZerodhaProvider.build_credentials({"api_key": "k", "api_secret": "s"})
    provider.reload_credentials(creds)
    assert provider._auth._credentials is creds


def test_reload_credentials_without_values_uses_layered_loader(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_ZERODHA_API_KEY", "env-key")
    monkeypatch.setenv("VAYREN_ZERODHA_API_SECRET", "env-secret")
    monkeypatch.setattr(
        "data.provider.credentials_store.default_store",
        lambda _data_dir: FileCredentialStore(tmp_path),
    )
    settings = make_settings(tmp_path)
    provider = ZerodhaProvider(settings)
    provider.reload_credentials()
    assert provider._auth._credentials.api_key == "env-key"
    assert provider._auth._credentials.configured
    assert provider.available() == (True, "ready")


def test_credentials_never_leak_through_repr() -> None:
    creds = ZerodhaProvider.build_credentials({"api_key": "topsecret", "api_secret": "x"})
    assert "topsecret" not in repr(creds)
    assert str(creds).count("topsecret") == 0


def test_manager_schema_from_provider(tmp_path) -> None:
    manager = make_manager(tmp_path)
    assert [f.label for f in manager.fields] == ["API Key", "API Secret", "User ID"]
    assert manager.display_name == "Stub"


def test_zerodha_adapter_advertises_credential_schema() -> None:
    assert ZerodhaProvider.display_name == "Zerodha"
    assert [f.key for f in ZerodhaProvider.credential_fields] == [
        "api_key",
        "api_secret",
        "user_id",
        "password",
        "totp_secret",
    ]
    secret_fields = [f for f in ZerodhaProvider.credential_fields if f.secret]
    assert [f.key for f in secret_fields] == ["api_secret", "user_id", "password", "totp_secret"]
