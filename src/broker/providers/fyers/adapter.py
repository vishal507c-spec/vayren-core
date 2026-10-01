"""FyersProvider — the FYERS Connect adapter behind the provider contract.

This is the FYERS venue's broker knowledge: FYERS API v3 credentials,
sessions, and (future) symbol/interval mapping. Everything the engine sees
is canonical or a normalized error — same boundary discipline as the
Zerodha provider.

Authentication phase scope: this provider advertises history shape, and
operational methods fail closed with an explicit reason instead of silently
serving another broker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.common.credentials import CredentialField
from broker.interfaces import (
    ERR_PROVIDER_UNAVAILABLE,
    ProviderError,
)
from broker.providers.fyers.credentials import (
    FyersCredentials,
    load_fyers_credentials,
)
from broker.providers.fyers.live_auth import DEFAULT_REDIRECT_URL

BROKER_ID = "fyers"
DISPLAY_NAME = "Fyers"
VENUE_SUBTITLE = "FYERS API v3"

HISTORICAL_CAPABILITIES: CapabilitySet = capability_set(
    {Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS)}
)

_HISTORY_UNAVAILABLE = (
    "FYERS historical download is not implemented in this phase "
    "(authentication only) — select the Zerodha history provider for downloads"
)


class FyersProvider:
    """Engine-facing FYERS adapter (authentication phase: fail-closed history)."""

    name = BROKER_ID
    display_name = DISPLAY_NAME
    credential_fields: tuple[CredentialField, ...] = (
        CredentialField("app_id", "App ID", secret=False, required=True),
        CredentialField("secret", "Secret", secret=True, required=True),
        CredentialField(
            "client_id",
            "Client ID",
            secret=False,
            required=False,
            help="FYERS login ID — enables fully automatic login",
        ),
        CredentialField(
            "totp_secret",
            "TOTP Secret",
            secret=True,
            required=False,
            help="Enable TOTP 2FA in the FYERS portal — enables automatic login",
        ),
        CredentialField(
            "pin",
            "PIN",
            secret=True,
            required=False,
            help="Trading PIN — required for automatic login",
        ),
        CredentialField(
            "redirect_uri",
            "Redirect URI",
            secret=False,
            required=False,
            help=f"Must match the FYERS app dashboard (default: {DEFAULT_REDIRECT_URL})",
        ),
    )

    def __init__(self, settings: Any, credentials: FyersCredentials | None = None) -> None:
        self._settings = settings
        self._credentials = (
            credentials if credentials is not None else load_fyers_credentials(settings)
        )

    # ── credential configuration (credentials manager surface) ──────────────

    @classmethod
    def build_credentials(cls, values: dict[str, str]) -> FyersCredentials:
        """Map provider field values to a credentials object (trimmed)."""
        return FyersCredentials(
            app_id=values.get("app_id", "").strip(),
            secret=values.get("secret", "").strip(),
            redirect_uri=values.get("redirect_uri", "").strip(),
            pin=values.get("pin", "").strip(),
            client_id=values.get("client_id", "").strip(),
            totp_secret=values.get("totp_secret", "").strip(),
        )

    def reload_credentials(self, credentials: FyersCredentials | None = None) -> None:
        """Rebuild the session from given credentials, or fall back to the
        layered loader (secure store → environment variables)."""
        if credentials is None:
            credentials = load_fyers_credentials(self._settings)
        self._credentials = credentials

    # ── Provider contract (fail-closed history) ─────────────────────────────

    def capabilities(self) -> CapabilitySet:
        """Advertised UBL matrix."""
        return HISTORICAL_CAPABILITIES

    def available(self) -> tuple[bool, str]:
        if not self._credentials.configured:
            return (
                False,
                "FYERS App ID / Secret not configured — configure them in "
                "SYSTEM → BROKERS (or use the VAYREN_FYERS_* "
                "environment variable fallback)",
            )
        return False, _HISTORY_UNAVAILABLE

    def symbols(self) -> set[str]:
        raise ProviderError(_HISTORY_UNAVAILABLE, ERR_PROVIDER_UNAVAILABLE)

    def fetch_candles(
        self,
        symbol: str,  # noqa: ARG002
        interval: str,  # noqa: ARG002
        start: datetime,  # noqa: ARG002
        end: datetime,  # noqa: ARG002
    ) -> list[dict] | object:
        raise ProviderError(_HISTORY_UNAVAILABLE, ERR_PROVIDER_UNAVAILABLE)

    def new_session(self) -> None:
        raise ProviderError(_HISTORY_UNAVAILABLE, ERR_PROVIDER_UNAVAILABLE)

    def renew(self) -> None:
        raise ProviderError(_HISTORY_UNAVAILABLE, ERR_PROVIDER_UNAVAILABLE)


__all__ = [
    "BROKER_ID",
    "DISPLAY_NAME",
    "HISTORICAL_CAPABILITIES",
    "VENUE_SUBTITLE",
    "FyersProvider",
]
