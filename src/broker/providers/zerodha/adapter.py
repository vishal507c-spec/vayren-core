"""ZerodhaProvider — the Kite Connect adapter behind the provider contract.

This is the ONLY place the core system holds broker knowledge: the
KiteConnect SDK, Zerodha credentials, access tokens, Kite interval ids,
instrument tokens, Kite errors and rate limits. Everything the engine sees is
canonical (symbols, ``CANONICAL_INTERVALS``, normalized error codes, candle
dicts) or a contract sentinel.

Behavior preserved from the previous integration:
- ``fetch_candles`` resolves symbol → Kite instrument token internally and
  fetches through the preserved ``FetchEngine`` (retries, 429 stop, sentinels).
- the fetch session is lazily created per ``new_session()``/``renew()`` so the
  engine's per-job/per-sweep session boundaries (and their rate-limit
  bookkeeping) are exactly as before.
- ``AuthError`` failures surface as ``ProviderError(AUTHENTICATION_FAILED)``
  with the same message text; resolver/fetch network failures surface as
  ``ProviderError(NETWORK_ERROR)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.common.credentials import CredentialField
from broker.interfaces import (
    ERR_AUTHENTICATION_FAILED,
    ERR_INVALID_REQUEST,
    ERR_INVALID_SYMBOL,
    ERR_NETWORK_ERROR,
    ProviderError,
)
from broker.providers.zerodha.auth import AuthEngine, AuthError
from broker.providers.zerodha.credentials import (
    ZerodhaCredentials,
    load_zerodha_credentials,
)
from broker.providers.zerodha.fetch import FetchEngine
from broker.providers.zerodha.instruments import InstrumentResolver

BROKER_ID = "zerodha"
DISPLAY_NAME = "Zerodha"
VENUE_SUBTITLE = "Kite Connect"

HISTORICAL_CAPABILITIES: CapabilitySet = capability_set(
    {Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS)}
)

# Canonical interval → Kite Connect interval id.
KITE_INTERVAL_IDS: dict[str, str] = {
    "1m": "minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "60minute",
}


class ZerodhaProvider:
    """Engine-facing adapter over the existing Zerodha implementation."""

    name = BROKER_ID
    display_name = DISPLAY_NAME
    credential_fields: tuple[CredentialField, ...] = (
        CredentialField("api_key", "API Key", secret=False, required=True),
        CredentialField("api_secret", "API Secret", secret=True, required=True),
        CredentialField(
            "user_id", "User ID", secret=True, required=False, help="Auto-login (optional)"
        ),
        CredentialField("password", "Password", secret=True, required=False),
        CredentialField("totp_secret", "TOTP Secret", secret=True, required=False),
    )

    def __init__(self, settings: Any, auth: AuthEngine | None = None) -> None:
        self._settings = settings
        self._auth = (
            auth if auth is not None else AuthEngine(settings, load_zerodha_credentials(settings))
        )
        self._resolver: InstrumentResolver | None = None
        self._fetcher: FetchEngine | None = None

    # ── credential configuration (credentials manager surface) ──────────────

    @classmethod
    def build_credentials(cls, values: dict[str, str]) -> ZerodhaCredentials:
        """Map provider field values to a credentials object (trimmed)."""
        return ZerodhaCredentials(
            api_key=values.get("api_key", "").strip(),
            api_secret=values.get("api_secret", "").strip(),
            user_id=values.get("user_id", "").strip(),
            password=values.get("password", "").strip(),
            totp_secret=values.get("totp_secret", "").strip(),
        )

    def reload_credentials(self, credentials: ZerodhaCredentials | None = None) -> None:
        """Rebuild the auth session from given credentials, or fall back to
        the layered loader (secure store → environment variables)."""
        if credentials is None:
            credentials = load_zerodha_credentials(self._settings)
        self._auth = AuthEngine(self._settings, credentials)
        self._fetcher = None

    # ── Provider contract ────────────────────────────────────────────────────

    def capabilities(self) -> CapabilitySet:
        """Advertised UBL matrix: historical-data only."""
        return HISTORICAL_CAPABILITIES

    def available(self) -> tuple[bool, str]:
        return self._auth.available()

    def symbols(self) -> set[str]:
        try:
            return set(self._instrument_map())
        except AuthError as exc:
            raise ProviderError(str(exc), ERR_AUTHENTICATION_FAILED) from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), ERR_NETWORK_ERROR) from exc

    def fetch_candles(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict] | object:
        if interval not in KITE_INTERVAL_IDS:
            raise ProviderError(f"unsupported interval: {interval!r}", ERR_INVALID_REQUEST)
        try:
            token = self._instrument_map().get(symbol)
            if token is None:
                raise ProviderError(f"symbol not found: {symbol}", ERR_INVALID_SYMBOL)
            if self._fetcher is None:
                self._fetcher = FetchEngine(self._settings, self._auth.get_kite())
            return self._fetcher.fetch(token, KITE_INTERVAL_IDS[interval], start, end)
        except ProviderError:
            raise
        except AuthError as exc:
            raise ProviderError(str(exc), ERR_AUTHENTICATION_FAILED) from exc
        except Exception as exc:
            raise ProviderError(str(exc), ERR_NETWORK_ERROR) from exc

    def new_session(self) -> None:
        """Fresh fetch session — rate-limit bookkeeping reset, no re-auth."""
        self._fetcher = None

    def renew(self) -> None:
        try:
            self._auth.renew()
        except AuthError as exc:
            raise ProviderError(str(exc), ERR_AUTHENTICATION_FAILED) from exc
        self._fetcher = None

    # ── internals ────────────────────────────────────────────────────────────

    def _instrument_map(self) -> dict[str, int]:
        """Symbol → Kite instrument token, lazily fetched and cached."""
        if self._resolver is None:
            self._resolver = InstrumentResolver(self._settings, self._auth.get_kite())
            self._resolver.fetch()
        return self._resolver.map()


__all__ = [
    "BROKER_ID",
    "DISPLAY_NAME",
    "HISTORICAL_CAPABILITIES",
    "KITE_INTERVAL_IDS",
    "VENUE_SUBTITLE",
    "ZerodhaProvider",
]
