"""Universal broker authentication contract — the broker-agnostic auth vocabulary.

VAYREN core thinks ``"connect to the selected broker"``; each venue's
adapter thinks ``"how does this particular broker authenticate?"``. This
module is the stable meeting point between the two:

- :class:`AuthCapability` — what a broker may need (declared, never assumed).
- :class:`AuthField` — one configurable credential (UBL-owned shape; mirrors
  the provider layer's ``CredentialField`` without importing it — this
  package imports nothing but stdlib).
- :class:`ConnectionState` — the universal session lifecycle.
- :class:`AuthErrorCode` — canonical upper-layer failure reasons.
- :class:`AccountIdentity` / :class:`AuthResult` — secret-free facts.
- :class:`BrokerAuthContract` — the protocol every venue adapter implements.

The contract never assumes ``API_KEY + API_SECRET``: the adapter's
:func:`authentication_schema` decides what is required and the UI renders
exactly that. Secrets never appear here — results carry labels and masked
values only (see :func:`mask_secret`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class AuthCapability(StrEnum):
    """Authentication building blocks a broker may declare.

    A venue advertises the subset it needs; VAYREN never demands more.
    """

    API_KEY = "API_KEY"
    API_SECRET = "API_SECRET"
    CLIENT_ID = "CLIENT_ID"
    USERNAME = "USERNAME"
    PASSWORD = "PASSWORD"
    TOTP = "TOTP"
    ACCESS_TOKEN = "ACCESS_TOKEN"
    OAUTH = "OAUTH"
    AUTH_CODE = "AUTH_CODE"
    REDIRECT_URI = "REDIRECT_URI"
    BROWSER_LOGIN = "BROWSER_LOGIN"
    SESSION_REFRESH = "SESSION_REFRESH"
    CUSTOM = "CUSTOM"


class ConnectionState(StrEnum):
    """Universal session lifecycle (UI renders this, never tokens)."""

    DISCONNECTED = "DISCONNECTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    CONNECTED = "CONNECTED"
    EXPIRED = "EXPIRED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    ERROR = "ERROR"


class AuthErrorCode(StrEnum):
    """Canonical upper-layer authentication failure reasons.

    Adapters map venue-specific diagnostics onto these; the original
    message stays inside the adapter (secret-free) for debugging.
    """

    AUTH_REQUIRED = "AUTH_REQUIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    AUTH_TIMEOUT = "AUTH_TIMEOUT"
    REDIRECT_FAILED = "REDIRECT_FAILED"
    TOKEN_EXCHANGE_FAILED = "TOKEN_EXCHANGE_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"


@dataclass(frozen=True)
class AuthField:
    """One configurable credential of a broker adapter.

    ``key`` is the storage key; ``label`` the UI caption; ``secret`` marks
    values that must always be masked and never logged; ``required``
    enables empty-value validation; ``default`` pre-fills honest broker
    defaults (e.g. a localhost redirect URI); ``capabilities`` names the
    :class:`AuthCapability` values this field satisfies.
    """

    key: str
    label: str
    secret: bool = False
    required: bool = False
    help: str = ""
    default: str = ""
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountIdentity:
    """Secret-free proof of who the session belongs to."""

    broker_id: str
    account_id: str
    display_name: str = ""
    environment: str = "paper"


@dataclass(frozen=True)
class AuthResult:
    """Outcome of one authentication attempt. Never carries secrets."""

    ok: bool
    broker_id: str
    message: str
    error_code: str = ""
    account_id: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


def mask_secret(value: str) -> str:
    """Mask a secret for display (first 4 + last 2, else bullets)."""
    if not value:
        return ""
    return f"{value[:4]}…{value[-2:]}" if len(value) > 8 else "••••"


@runtime_checkable
class BrokerAuthContract(Protocol):
    """Stable universal broker authentication contract (one venue = one impl).

    Implementations live inside the venue's adapter package; VAYREN core
    programs against this protocol only and never touches venue SDKs,
    login URLs, credential names or redirect internals.
    """

    def broker_id(self) -> str:
        """Stable registry id (e.g. ``"zerodha"``, ``"fyers"``)."""
        ...

    def display_name(self) -> str:
        """UI label only, never a lookup key."""
        ...

    def authentication_schema(self) -> tuple[AuthField, ...]:
        """Credential fields the UI must render (the adapter decides)."""
        ...

    def auth_capabilities(self) -> tuple[AuthCapability, ...]:
        """Declared authentication building blocks."""
        ...

    def connect(self) -> AuthResult:
        """Establish a session from securely stored credentials/session."""
        ...

    def disconnect(self) -> AuthResult:
        """Clear the session, keep the configuration."""
        ...

    def is_connected(self) -> bool:
        """True only when a verified session is live."""
        ...

    def connection_status(self) -> ConnectionState:
        """Current lifecycle state (UI renders this)."""
        ...

    def authenticate(self) -> AuthResult:
        """Run the venue's full login flow (browser/redirect automated
        where the venue permits; the user always performs the venue's own
        login/2FA step)."""
        ...

    def refresh_session_if_supported(self) -> AuthResult:
        """Renew the session when the venue supports it, else a
        fail-closed result (never a silent re-login)."""
        ...

    def validate_credentials(self, values: dict[str, str]) -> str | None:
        """First missing/invalid field as a label-only message, else None."""
        ...

    def get_account_identity(self) -> AccountIdentity | None:
        """Verified identity, or None when no live session exists."""
        ...

    def get_connection_metadata(self) -> dict[str, str]:
        """Secret-free connection facts (venue label, callback URL, ...)."""
        ...

    def secure_store_credentials(self, values: dict[str, str]) -> AuthResult:
        """Validate + persist credentials via the platform store."""
        ...

    def secure_store_session(self, session: dict[str, str]) -> AuthResult:
        """Persist a fresh session (tokens) via the platform store."""
        ...

    def load_session(self) -> dict[str, str] | None:
        """Stored session payload, or None when no session exists.

        The payload may contain token values for the immediate caller
        only — callers must never log, persist elsewhere, or expose it.
        """
        ...

    def clear_session(self) -> AuthResult:
        """Erase the session (configuration untouched)."""
        ...


def schema_keys(schema: tuple[AuthField, ...]) -> tuple[str, ...]:
    """Storage keys of a schema, in order (for store payloads)."""
    return tuple(field.key for field in schema)


def describe_schema(schema: tuple[AuthField, ...]) -> list[dict[str, Any]]:
    """JSON-safe projection of a schema for UI snapshots (no values)."""
    return [
        {
            "key": field.key,
            "label": field.label,
            "secret": bool(field.secret),
            "required": bool(field.required),
            "help": field.help,
            "default": field.default,
            "capabilities": list(field.capabilities),
        }
        for field in schema
    ]


__all__ = [
    "AccountIdentity",
    "AuthCapability",
    "AuthErrorCode",
    "AuthField",
    "AuthResult",
    "BrokerAuthContract",
    "ConnectionState",
    "describe_schema",
    "mask_secret",
    "schema_keys",
]
