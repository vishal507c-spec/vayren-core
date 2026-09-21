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
- The validation RULE and its reason wording are Rust-owned
  (``rust/vayren-core/src/live_readiness.rs``); this module keeps the types,
  the redaction and the one step a kernel cannot do — resolving a secret
  name through the store (constitution §1, migration §7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from execution.broker.native_policy import native_credential_reasons


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

    The store lookup is the only step performed here because it needs the
    secret values. The rule and its wording belong to the Rust kernel; the
    reasons it returns name missing fields, never values.
    """
    resolvable: tuple[bool, ...]
    if require_secrets and store is not None:
        resolvable = tuple(store.get(ref) is not None for ref in creds.key_refs)
    else:
        resolvable = (False,) * len(creds.key_refs)
    reasons = native_credential_reasons(
        account_id=creds.account_id,
        environment=creds.environment,
        expected_environment=expected_environment,
        key_refs=tuple(creds.key_refs),
        resolvable=resolvable,
        require_secrets=require_secrets,
        store_present=store is not None,
    )
    return (not reasons, reasons)


def default_account_id() -> str:
    """Conventional env-provided account identity (empty when unset)."""
    return os.environ.get("VAYREN_BROKER_ACCOUNT_ID", "")
