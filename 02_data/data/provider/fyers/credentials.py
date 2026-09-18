"""FyersCredentials — FYERS API v3 credentials, never stored in settings.

Credential loading is layered (in-app secure store first, then the
``VAYREN_FYERS_*`` environment variables as a fallback). This is the ONLY
place the FYERS world knows about the environment variables.

The venue never sees Zerodha state and Zerodha never sees FYERS state:
config lives under service ``vayren:fyers``, the session under
``vayren:fyers:session``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from data.provider.fyers.live_auth import DEFAULT_REDIRECT_URL

if TYPE_CHECKING:
    from data.provider.credentials_store import CredentialStore
    from data.settings import DownloadSettings


class FyersCredentials:
    """FYERS API v3 credentials — never stored in settings, never logged,
    never exposed through events or the system model.

    Auto-login (Zerodha-experience parity) additionally needs the user's
    FYERS login ID (``client_id``), TOTP secret (enable TOTP 2FA in the
    FYERS portal) and trading ``pin`` — all optional, all layered like
    the rest. Without them the venue falls back to the interactive
    browser flow (user logs in on FYERS' site; capture stays automatic).
    """

    def __init__(
        self,
        app_id: str,
        secret: str,
        redirect_uri: str = "",
        pin: str = "",
        client_id: str = "",
        totp_secret: str = "",
    ) -> None:
        self._app_id = app_id
        self._secret = secret
        self._redirect_uri = redirect_uri or DEFAULT_REDIRECT_URL
        self._pin = pin
        self._client_id = client_id
        self._totp_secret = totp_secret

    @classmethod
    def from_env(cls, env: os._Environ | dict[str, str] | None = None) -> FyersCredentials:
        """Build credentials from VAYREN_FYERS_* environment variables."""
        source = os.environ if env is None else env
        return cls(
            app_id=source.get("VAYREN_FYERS_APP_ID", "").strip(),
            secret=source.get("VAYREN_FYERS_SECRET", "").strip(),
            redirect_uri=source.get("VAYREN_FYERS_REDIRECT_URI", "").strip(),
            pin=source.get("VAYREN_FYERS_PIN", "").strip(),
            client_id=source.get("VAYREN_FYERS_CLIENT_ID", "").strip(),
            totp_secret=source.get("VAYREN_FYERS_TOTP_SECRET", "").strip(),
        )

    @property
    def configured(self) -> bool:
        """True when the App ID + Secret pair is present."""
        return bool(self._app_id and self._secret)

    @property
    def auto_login_configured(self) -> bool:
        """True when the full auto-login triple is present (login ID +
        TOTP secret + trading PIN, on top of the configured pair)."""
        return bool(self.configured and self._client_id and self._totp_secret and self._pin)

    @property
    def app_id(self) -> str:
        return self._app_id

    @property
    def secret(self) -> str:
        return self._secret

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    @property
    def pin(self) -> str:
        return self._pin

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def totp_secret(self) -> str:
        return self._totp_secret

    def __repr__(self) -> str:
        # Never leak values; repr is used in error messages and logs.
        return (
            f"FyersCredentials(configured={self.configured}, "
            f"auto_login={self.auto_login_configured})"
        )


def _store_values(settings: DownloadSettings, store: CredentialStore | None) -> dict[str, str]:
    from data.provider.credentials_store import default_store, provider_service

    if store is None:
        store = default_store(settings.data_dir)
    saved = store.load(provider_service("fyers"))
    return {key: value.strip() for key, value in (saved or {}).items() if value.strip()}


def load_fyers_credentials(
    settings: DownloadSettings, store: CredentialStore | None = None
) -> FyersCredentials:
    """Layered credential resolution: in-app secure store first, then env.

    Preferred priority (per-field):

        1. secure in-app credentials (``vayren:fyers`` in the store)
        2. ``VAYREN_FYERS_*`` environment variables
        3. empty — the provider reports Not Configured
    """
    env = FyersCredentials.from_env()
    stored = _store_values(settings, store)

    def pick(field: str) -> str:
        return stored.get(field, "") or getattr(env, field)

    return FyersCredentials(
        app_id=pick("app_id"),
        secret=pick("secret"),
        redirect_uri=pick("redirect_uri"),
        pin=pick("pin"),
        client_id=pick("client_id"),
        totp_secret=pick("totp_secret"),
    )
