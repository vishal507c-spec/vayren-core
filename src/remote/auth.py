"""Client authentication for the remote transport (no broker secrets).

Remote credentials are entirely separate from broker credentials: a bearer
token the operator provisions out-of-band (environment or a file outside
the repo). Two scopes exist so a read-only display token can never change
system state:

- ``trade`` — snapshot, events, subscriptions, and control commands.
- ``read`` — snapshot, events, subscriptions, and heartbeat only.

Broker secrets (FYERS API secret, access token, TOTP) are never accepted
here and never travel to clients. Tokens are compared in constant time and
never logged.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

#: Minimum accepted token length (short tokens fail closed at load time).
MIN_TOKEN_LENGTH = 16

#: Maximum failed hello attempts per connection before the server hangs up.
MAX_AUTH_ATTEMPTS = 3


class RemoteRole:
    """Client scopes (plain strings; no second permission system)."""

    READ = "read"
    TRADE = "trade"

    @staticmethod
    def allows_command(role: str) -> bool:
        """Only ``trade`` clients may send state-changing commands."""
        return role == RemoteRole.TRADE


class AuthConfigError(Exception):
    """Remote auth is misconfigured — the server must refuse to start."""


@dataclass(frozen=True, slots=True)
class TokenEntry:
    """One provisioned credential (secret never leaves this object)."""

    name: str
    role: str
    secret: str


def _checked_token(name: str, role: str, secret: str) -> TokenEntry | None:
    """Validate one candidate credential; blank/short values are dropped."""
    text = (secret or "").strip()
    if not text or len(text) < MIN_TOKEN_LENGTH:
        return None
    if role not in (RemoteRole.READ, RemoteRole.TRADE):
        return None
    return TokenEntry(name=name, role=role, secret=text)


def load_token_map(env: dict[str, str] | None = None) -> dict[str, TokenEntry]:
    """Load provisioned remote credentials keyed by secret.

    Sources (no hardcoding, nothing broker-related):

    - ``VAYREN_REMOTE_TOKEN`` — full ``trade`` token.
    - ``VAYREN_REMOTE_READ_TOKEN`` — optional ``read``-only token.
    - ``VAYREN_REMOTE_TOKEN_FILE`` — optional file with ``role:token``
      lines (``#`` comments allowed, mode 0600 recommended) for operators
      who provision several display clients.

    Raises :class:`AuthConfigError` when nothing usable is configured —
    the server must not listen without authentication.
    """
    source = env if env is not None else os.environ
    entries: dict[str, TokenEntry] = {}

    def _add(entry: TokenEntry | None) -> None:
        if entry is not None and entry.secret not in entries:
            entries[entry.secret] = entry

    _add(_checked_token("env-trade", RemoteRole.TRADE, source.get("VAYREN_REMOTE_TOKEN", "")))
    _add(_checked_token("env-read", RemoteRole.READ, source.get("VAYREN_REMOTE_READ_TOKEN", "")))
    token_file = (source.get("VAYREN_REMOTE_TOKEN_FILE", "") or "").strip()
    if token_file:
        try:
            with open(token_file, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            raise AuthConfigError(f"cannot read VAYREN_REMOTE_TOKEN_FILE: {exc}") from exc
        for lineno, line in enumerate(lines, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            role, _, secret = text.partition(":")
            entry = _checked_token(f"file:{lineno}", role.strip().lower(), secret.strip())
            if entry is None:
                raise AuthConfigError(
                    f"invalid credential on line {lineno} of VAYREN_REMOTE_TOKEN_FILE"
                )
            _add(entry)
    if not entries:
        raise AuthConfigError(
            "no remote credentials configured (set VAYREN_REMOTE_TOKEN or VAYREN_REMOTE_TOKEN_FILE)"
        )
    return entries


def verify_token(provided: str, token_map: dict[str, TokenEntry]) -> TokenEntry | None:
    """Constant-time bearer-token check; returns the entry or ``None``."""
    if not isinstance(provided, str) or not provided:
        return None
    candidate = provided.strip().encode("utf-8")
    for secret, entry in token_map.items():
        if hmac.compare_digest(candidate, secret.encode("utf-8")):
            return entry
    return None


class AuthTracker:
    """Per-connection hello accounting (attempt cap, no token storage)."""

    def __init__(self, max_attempts: int = MAX_AUTH_ATTEMPTS) -> None:
        self._max_attempts = max(1, int(max_attempts))
        self._attempts = 0
        self.authenticated_role: str | None = None

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def authenticated(self) -> bool:
        return self.authenticated_role is not None

    def note_attempt(self, entry: TokenEntry | None) -> bool:
        """Record one hello; True when the connection may keep trying."""
        if entry is not None:
            self.authenticated_role = entry.role
            return True
        self._attempts += 1
        return self._attempts < self._max_attempts
