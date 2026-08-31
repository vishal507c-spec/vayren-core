"""CredentialStore — file fallback round-trips + Windows backend structure."""

from __future__ import annotations

import ctypes
import json

import pytest

from data.provider.credentials_store import (
    FileCredentialStore,
    WindowsCredentialStore,
    provider_service,
)

_VALUES = {"api_key": "key123", "api_secret": "secret-abc", "totp_secret": "TOTP"}


# ── file fallback store ──────────────────────────────────────────────────────


def test_file_store_save_load_roundtrip(tmp_path) -> None:
    store = FileCredentialStore(tmp_path)
    store.save("vayren:zerodha", _VALUES)
    assert store.load("vayren:zerodha") == _VALUES


def test_file_store_load_missing_returns_none(tmp_path) -> None:
    assert FileCredentialStore(tmp_path).load("vayren:zerodha") is None


def test_file_store_delete_removes_values(tmp_path) -> None:
    store = FileCredentialStore(tmp_path)
    store.save("vayren:zerodha", _VALUES)
    store.delete("vayren:zerodha")
    assert store.load("vayren:zerodha") is None


def test_file_store_services_are_isolated(tmp_path) -> None:
    store = FileCredentialStore(tmp_path)
    store.save("vayren:zerodha", {"api_key": "a"})
    assert store.load("vayren:brokerb") is None


def test_file_store_corrupt_file_returns_none(tmp_path) -> None:
    store = FileCredentialStore(tmp_path)
    store.save("vayren:zerodha", _VALUES)
    path = store._path("vayren:zerodha")
    path.write_text("{not json", encoding="utf-8")
    assert store.load("vayren:zerodha") is None


def test_provider_service_key() -> None:
    assert provider_service("zerodha") == "vayren:zerodha"


# ── Windows Credential Manager backend (structure only — faked winapi) ───────


class _FakeWinApi:
    """Records calls the same way advapi32 credential functions are used."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.writes: list[tuple[str, bytes]] = []
        self.deletes: list[str] = []

    def cred_write(self, cred) -> bool:
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        self.blobs[cred.TargetName] = blob
        self.writes.append((cred.TargetName, blob))
        return True

    def cred_read(self, target: str) -> bytes | None:
        return self.blobs.get(target)

    def cred_delete(self, target: str) -> bool:
        self.deletes.append(target)
        self.blobs.pop(target, None)
        return True


@pytest.fixture
def fake_win(monkeypatch) -> _FakeWinApi:
    fake = _FakeWinApi()
    monkeypatch.setattr("data.provider.credentials_store._win_cred_write", fake.cred_write)
    monkeypatch.setattr("data.provider.credentials_store._win_cred_read", fake.cred_read)
    monkeypatch.setattr("data.provider.credentials_store._win_cred_delete", fake.cred_delete)
    return fake


def test_windows_store_save_load_delete_roundtrip(fake_win) -> None:  # noqa: ARG001
    store = WindowsCredentialStore()
    store.save("vayren:zerodha", _VALUES)
    assert store.load("vayren:zerodha") == _VALUES
    store.delete("vayren:zerodha")
    assert store.load("vayren:zerodha") is None


def test_windows_store_load_missing_returns_none(fake_win) -> None:  # noqa: ARG001
    assert WindowsCredentialStore().load("vayren:zerodha") is None


def test_windows_store_blob_is_json_of_values(fake_win) -> None:
    store = WindowsCredentialStore()
    store.save("vayren:zerodha", _VALUES)
    target, blob = fake_win.writes[0]
    assert target == "vayren:zerodha"
    assert json.loads(blob.decode("utf-8")) == _VALUES


def test_windows_store_delete_target(fake_win) -> None:
    WindowsCredentialStore().delete("vayren:zerodha")
    assert fake_win.deletes == ["vayren:zerodha"]
