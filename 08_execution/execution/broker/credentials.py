"""Broker credentials — provider-neutral identity without secret leakage.

Design rules (fail-closed, auditable):
- Secret VALUES live only in a :class:`CredentialStore` (env by default)
  and are resolved in memory at use time, never stored on objects.
- :class:`BrokerCredentials` carries identity (account, environment) plus
  secret NAMES (key refs), never secret values.
- ``repr`` is redacted; logs/journal must only ever see account ids,
  environment labels and key-ref names.
- Missing or unresolvable credentials fail validation BEFORE any order
  path is constructed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BrokerCredentials:
    """Who is trading where — identity plus secret references, no values."""

    account_id: str = ""
    environment: str = ""  # "sandbox" | "live" | ""
    key_refs: tuple[str, ...] = ()

    def __repr__(self) -> str:
        keys = ",".join(self.key_refs)
        return (
            f"BrokerCredentials(account_id={self.account_id!r}, "
            f"environment={self.environment!r}, key_refs=({keys}), secrets=<redacted>)"
        )


class CredentialStore(Protocol):
    """Secret lookup by name. Implementations never log values."""

    def get(self, name: str) -> str | None:
        """Return the secret value, or None when absent."""
        ...


class EnvCredentialStore:
    """Read secrets from explicit environment variables (optionally prefixed)."""

    def __init__(self, prefix: str = "VAYREN_BROKER_", env: dict[str, str] | None = None) -> None:
        self._prefix = prefix
        self._env = env if env is not None else os.environ

    def get(self, name: str) -> str | None:
        value = self._env.get(self._prefix + name)
        if value is None or value == "":
            return None
        return value


def validate_credentials(
    creds: BrokerCredentials,
    store: CredentialStore | None,
    *,
    require_secrets: bool,
    expected_environment: str = "",
) -> tuple[bool, tuple[str, ...]]:
    """Validate identity (+ secrets when required). Never touches orders.

    Returns (ok, reasons). Reasons name missing fields, never values.
    """
    reasons: list[str] = []
    if not creds.account_id:
        reasons.append("missing account_id")
    if not creds.environment:
        reasons.append("missing environment")
    elif expected_environment and creds.environment != expected_environment:
        reasons.append(
            f"environment mismatch: credentials say {creds.environment!r}, "
            f"expected {expected_environment!r}"
        )
    if require_secrets:
        if store is None:
            reasons.append("no credential store configured")
        else:
            for ref in creds.key_refs:
                if store.get(ref) is None:
                    reasons.append(f"secret not resolvable: {ref}")
        if not creds.key_refs:
            reasons.append("no secret key_refs declared")
    return (not reasons, tuple(reasons))


def default_account_id() -> str:
    """Conventional env-provided account identity (empty when unset)."""
    return os.environ.get("VAYREN_BROKER_ACCOUNT_ID", "")
