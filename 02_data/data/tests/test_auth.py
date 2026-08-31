"""AuthEngine — availability, token file handling (no network)."""

import pytest

from data.provider.zerodha.auth import AuthEngine, AuthError
from data.provider.zerodha.credentials import ZerodhaCredentials
from data.tests.conftest import make_settings


class _NoKiteConnect:
    def __init__(self, credentials, settings) -> None:
        self._settings = settings
        self._credentials = credentials


def _auth(settings, creds) -> AuthEngine:
    return AuthEngine(settings, creds)


def test_available_false_without_sdk(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(
        "data.provider.zerodha.auth._load_kiteconnect",
        lambda: (_ for _ in ()).throw(AuthError("kiteconnect is not installed")),
    )
    creds = type("C", (), {"configured": True})()
    ready, reason = _auth(settings, creds).available()
    assert not ready
    assert "kiteconnect" in reason


def test_available_false_without_credentials(tmp_path) -> None:
    settings = make_settings(tmp_path)
    creds = type("C", (), {"configured": False})()
    ready, reason = _auth(settings, creds).available()
    assert not ready
    assert "Historical Download panel" in reason
    assert "VAYREN_ZERODHA" in reason


def test_load_token_returns_none_when_missing(tmp_path) -> None:
    settings = make_settings(tmp_path)
    auth = _auth(settings, object())
    assert auth._load_token() is None


def test_save_and_load_token_roundtrip(tmp_path) -> None:
    from pathlib import Path

    settings = make_settings(tmp_path)
    assert settings.token_file is not None
    auth = _auth(settings, object())
    auth._save_token("abc123")
    assert auth._load_token() == "abc123"
    assert Path(settings.token_file).is_file()


def test_load_token_ignores_corrupt_file(tmp_path) -> None:
    from pathlib import Path

    settings = make_settings(tmp_path)
    assert settings.token_file is not None
    Path(settings.token_file).write_text("not json{")
    auth = _auth(settings, object())
    assert auth._load_token() is None


def test_fresh_kite_requires_configured_credentials(tmp_path) -> None:
    creds = ZerodhaCredentials.from_env({})
    auth = _auth(make_settings(tmp_path), creds)
    with pytest.raises(AuthError):
        auth.get_kite()


def test_credentials_from_env() -> None:
    creds = ZerodhaCredentials.from_env(
        {
            "VAYREN_ZERODHA_API_KEY": " key ",
            "VAYREN_ZERODHA_API_SECRET": "secret",
            "VAYREN_ZERODHA_USER_ID": "AB1234",
            "VAYREN_ZERODHA_PASSWORD": "pw",
            "VAYREN_ZERODHA_TOTP_SECRET": "totp",
        }
    )
    assert creds.configured
    assert creds.api_key == "key"
    assert creds.api_secret == "secret"
    assert creds.user_id == "AB1234"
    assert creds.password == "pw"
    assert creds.totp_secret == "totp"


def test_credentials_unconfigured_without_key_pair() -> None:
    creds = ZerodhaCredentials.from_env({})
    assert not creds.configured


def test_credentials_repr_never_leaks_values() -> None:
    creds = ZerodhaCredentials.from_env(
        {"VAYREN_ZERODHA_API_KEY": "secretkey", "VAYREN_ZERODHA_API_SECRET": "supersecret"}
    )
    text = repr(creds)
    assert "secretkey" not in text
    assert "supersecret" not in text
