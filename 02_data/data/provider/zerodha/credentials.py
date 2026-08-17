"""ZerodhaCredentials — Zerodha API credentials, never stored in settings.

Credential loading is layered (in-app secure store first, then the
``VAYREN_ZERODHA_*`` environment variables as a backward-compatible
fallback). This is the ONLY place the provider's world knows about the
environment variables.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.provider.credentials_store import CredentialStore
    from data.settings import DownloadSettings


class ZerodhaCredentials:
    """Provider credentials, read from the environment — never stored in
    settings, never logged, never exposed through events or the system model.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        user_id: str = "",
        password: str = "",
        totp_secret: str = "",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._user_id = user_id
        self._password = password
        self._totp_secret = totp_secret

    @classmethod
    def from_env(cls, env: os._Environ | dict[str, str] | None = None) -> ZerodhaCredentials:
        """Build credentials from VAYREN_ZERODHA_* environment variables."""
        source = os.environ if env is None else env
        return cls(
            api_key=source.get("VAYREN_ZERODHA_API_KEY", "").strip(),
            api_secret=source.get("VAYREN_ZERODHA_API_SECRET", "").strip(),
            user_id=source.get("VAYREN_ZERODHA_USER_ID", "").strip(),
            password=source.get("VAYREN_ZERODHA_PASSWORD", "").strip(),
            totp_secret=source.get("VAYREN_ZERODHA_TOTP_SECRET", "").strip(),
        )

    @property
    def configured(self) -> bool:
        """True when the API key pair is present (auto-login also needs the
        rest, but download works with just a valid token file)."""
        return bool(self._api_key and self._api_secret)

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def api_secret(self) -> str:
        return self._api_secret

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def password(self) -> str:
        return self._password

    @property
    def totp_secret(self) -> str:
        return self._totp_secret

    def __repr__(self) -> str:
        # Never leak values; repr is used in error messages and logs.
        return f"ZerodhaCredentials(configured={self.configured})"


def _store_values(settings: DownloadSettings, store: CredentialStore | None) -> dict[str, str]:
    from data.provider.credentials_store import default_store, provider_service

    if store is None:
        store = default_store(settings.data_dir)
    saved = store.load(provider_service(settings.provider))
    return {key: value.strip() for key, value in (saved or {}).items() if value.strip()}


def load_zerodha_credentials(
    settings: DownloadSettings, store: CredentialStore | None = None
) -> ZerodhaCredentials:
    """Layered credential resolution: in-app secure store first, then env.

    Preferred priority (per-field):

        1. secure in-app credentials (``vayren:<provider>`` in the store)
        2. ``VAYREN_ZERODHA_*`` environment variables (backward compatible)
        3. empty — the provider reports Not Configured
    """
    env = ZerodhaCredentials.from_env()
    stored = _store_values(settings, store)

    def pick(field: str) -> str:
        return stored.get(field, "") or getattr(env, field)

    return ZerodhaCredentials(
        api_key=pick("api_key"),
        api_secret=pick("api_secret"),
        user_id=pick("user_id"),
        password=pick("password"),
        totp_secret=pick("totp_secret"),
    )
