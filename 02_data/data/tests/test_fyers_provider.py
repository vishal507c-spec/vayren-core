"""FYERS provider proofs — layered credentials, schema, fail-closed history.

Covers: env/store credential layering (store wins per-field), credential
schema ownership (App ID/Secret/Redirect/PIN — never another broker's
shape), secret-free reprs/messages, and the authentication-phase
fail-closed history face (available honest, fetch/symbols refuse with
PROVIDER_UNAVAILABLE). No network.
"""

from __future__ import annotations

import pytest

from data.provider.contract import ERR_PROVIDER_UNAVAILABLE, ProviderError
from data.provider.credentials_store import FileCredentialStore, provider_service
from data.provider.fyers.adapter import FyersProvider
from data.provider.fyers.credentials import FyersCredentials, load_fyers_credentials
from data.provider.fyers.live_auth import DEFAULT_REDIRECT_URL
from data.settings import DownloadSettings


def _settings(tmp_path) -> DownloadSettings:
    return DownloadSettings(data_dir=str(tmp_path), provider="fyers")


def test_credentials_default_to_unconfigured() -> None:
    creds = FyersCredentials("", "")
    assert not creds.configured
    assert not creds.auto_login_configured
    assert repr(creds) == "FyersCredentials(configured=False, auto_login=False)"


def test_credentials_repr_never_echoes_values() -> None:
    creds = FyersCredentials("APP-SECRET-1", "SHH-SECRET-9", pin="1234")
    blob = repr(creds)
    assert "APP-SECRET-1" not in blob
    assert "SHH-SECRET-9" not in blob
    assert "1234" not in blob
    assert creds.configured
    assert creds.redirect_uri == DEFAULT_REDIRECT_URL


def test_from_env_reads_fyers_vars_only(monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_FYERS_APP_ID", "  ENV-APP-1 ")
    monkeypatch.setenv("VAYREN_FYERS_SECRET", "ENV-SEC-9")
    monkeypatch.setenv("VAYREN_FYERS_REDIRECT_URI", "https://example.invalid/cb")
    monkeypatch.setenv("VAYREN_FYERS_PIN", "4321")
    creds = FyersCredentials.from_env()
    assert creds.app_id == "ENV-APP-1"
    assert creds.secret == "ENV-SEC-9"
    assert creds.redirect_uri == "https://example.invalid/cb"
    assert creds.pin == "4321"
    assert creds.configured


def test_store_beats_env_per_field(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    store = FileCredentialStore(tmp_path)
    store.save(
        provider_service("fyers"),
        {"app_id": "STORE-APP", "secret": "STORE-SEC", "redirect_uri": "", "pin": ""},
    )
    monkeypatch.setenv("VAYREN_FYERS_APP_ID", "ENV-APP")
    monkeypatch.setenv("VAYREN_FYERS_SECRET", "ENV-SEC")
    monkeypatch.setenv("VAYREN_FYERS_PIN", "9999")
    creds = load_fyers_credentials(settings, store)
    assert creds.app_id == "STORE-APP"
    assert creds.secret == "STORE-SEC"
    assert creds.pin == "9999"  # env fills only the gaps
    assert creds.redirect_uri == DEFAULT_REDIRECT_URL


def test_schema_is_fyers_owned() -> None:
    assert [field.key for field in FyersProvider.credential_fields] == [
        "app_id",
        "secret",
        "client_id",
        "totp_secret",
        "pin",
        "redirect_uri",
    ]
    assert FyersProvider.display_name == "Fyers"
    assert FyersProvider.name == "fyers"


def test_build_credentials_trims() -> None:
    creds = FyersProvider.build_credentials(
        {"app_id": "  A-1 ", "secret": " S-9 ", "redirect_uri": "", "pin": ""}
    )
    assert creds.app_id == "A-1"
    assert creds.secret == "S-9"
    assert creds.redirect_uri == DEFAULT_REDIRECT_URL


def test_available_is_honest_without_network(tmp_path) -> None:
    provider = FyersProvider(_settings(tmp_path), FyersCredentials("", ""))
    ready, reason = provider.available()
    assert not ready
    assert "not configured" in reason.lower()

    provider = FyersProvider(_settings(tmp_path), FyersCredentials("A-1", "S-9"))
    ready, reason = provider.available()
    assert not ready  # authentication phase: history genuinely unimplemented
    assert "not implemented" in reason.lower()


def test_history_calls_fail_closed_with_secret_free_reasons(tmp_path) -> None:
    provider = FyersProvider(_settings(tmp_path), FyersCredentials("A-1", "S-SSH"))
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch_candles("RELIANCE", "15m", None, None)  # type: ignore[arg-type]
    assert excinfo.value.code == ERR_PROVIDER_UNAVAILABLE
    assert "S-SSH" not in str(excinfo.value)
    try:
        provider.symbols()
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.code == ERR_PROVIDER_UNAVAILABLE
    try:
        provider.new_session()
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.code == ERR_PROVIDER_UNAVAILABLE
    try:
        provider.renew()
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.code == ERR_PROVIDER_UNAVAILABLE


def test_reload_credentials_falls_back_to_layered_loader(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    provider = FyersProvider(settings, FyersCredentials("A-OLD", "S-OLD"))
    monkeypatch.setenv("VAYREN_FYERS_APP_ID", "A-NEW")
    monkeypatch.setenv("VAYREN_FYERS_SECRET", "S-NEW")
    provider.reload_credentials()
    assert provider._credentials.app_id == "A-NEW"
    provider.reload_credentials(FyersCredentials("A-PIN", "S-PIN"))
    assert provider._credentials.app_id == "A-PIN"
