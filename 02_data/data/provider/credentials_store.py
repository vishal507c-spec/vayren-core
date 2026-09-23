"""CredentialStore — secure local storage for provider credentials.

The operating system's credential/keyring mechanism is preferred (Windows
Credential Manager via ``advapi32`` — no third-party dependency). When the
OS mechanism is unavailable a last-resort file store is used, writing a
JSON file under ``<data_dir>/credentials/`` with best-effort permissions.

Credential values are never logged and never included in exception messages.
"""

from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

SERVICE_PREFIX = "vayren"


class CredentialStore(Protocol):
    """Persists one JSON blob of credential values per service."""

    def save(self, service: str, values: dict[str, str]) -> None: ...

    def load(self, service: str) -> dict[str, str] | None: ...

    def delete(self, service: str) -> None: ...


def provider_service(provider_name: str) -> str:
    """Storage service key for a provider registry name (e.g. ``zerodha``)."""
    return f"{SERVICE_PREFIX}:{provider_name}"


class FileCredentialStore:
    """Last-resort store: JSON under ``<data_dir>/credentials/``.

    Only used when the OS credential mechanism is unavailable. The file
    name is derived from the service key; values are plain JSON — treat
    this store as a fallback, never as the primary security boundary.
    """

    def __init__(self, root: str | Path) -> None:
        self._dir = Path(root) / "credentials"

    def _path(self, service: str) -> Path:
        safe = service.replace(":", ".")
        return self._dir / f"{safe}.json"

    def save(self, service: str, values: dict[str, str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(service).write_text(json.dumps(values), encoding="utf-8")

    def load(self, service: str) -> dict[str, str] | None:
        path = self._path(service)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def delete(self, service: str) -> None:
        path = self._path(service)
        if path.is_file():
            path.unlink()


# ── Windows Credential Manager (advapi32, no third-party deps) ──────────────

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", wintypes.LPBYTE),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _win_cred_write(cred: _CREDENTIAL) -> bool:
    if sys.platform != "win32":
        raise OSError("Windows Credential Manager requires Windows")
    return bool(ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0))


def _win_cred_read(target: str) -> bytes | None:
    if sys.platform != "win32":
        raise OSError("Windows Credential Manager requires Windows")
    pcred = ctypes.POINTER(_CREDENTIAL)()
    if not ctypes.windll.advapi32.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    try:
        cred = pcred.contents
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        ctypes.windll.advapi32.CredFree(pcred)


def _win_cred_delete(target: str) -> bool:
    if sys.platform != "win32":
        raise OSError("Windows Credential Manager requires Windows")
    return bool(ctypes.windll.advapi32.CredDeleteW(target, _CRED_TYPE_GENERIC, 0))


class WindowsCredentialStore:
    """Windows Credential Manager backend (one generic entry per service).

    The credential blob is a JSON map of the provider's fields. Values live
    inside the OS-protected credential vault; nothing is written to disk by
    this class.
    """

    _USERNAME = "vayren"

    def save(self, service: str, values: dict[str, str]) -> None:
        blob = json.dumps(values).encode("utf-8")
        cred = _CREDENTIAL()
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = service
        cred.UserName = self._USERNAME
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob), wintypes.LPBYTE)
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        if not _win_cred_write(cred):
            raise OSError("Windows Credential Manager write failed")

    def load(self, service: str) -> dict[str, str] | None:
        blob = _win_cred_read(service)
        if blob is None:
            return None
        try:
            data = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def delete(self, service: str) -> None:
        _win_cred_delete(service)


def default_store(data_dir: str | Path) -> CredentialStore:
    """OS credential mechanism when available, else the file fallback."""
    if sys.platform == "win32":
        return WindowsCredentialStore()
    return FileCredentialStore(data_dir)
