"""UBL credential boundary — production-safe, key-reference based (M8 §4).

Rules (fail-closed, auditable):

- Secret VALUES live only in a :class:`CredentialResolver` (an existing
  store: the data-layer OS/file store or the execution-layer env store)
  and are resolved in memory at use time, never stored on objects.
- :class:`CredentialRef` carries identity (scope, environment, key name),
  never secret values. ``repr`` is redacted by construction — there is
  no value field to leak into logs, journals, events, UI or selection
  persistence.
- :class:`BrokerSelection` persistence carries no secret fields (enforced
  by ``FileSelectionStore`` schema + tests).
- PAPER / SANDBOX / LIVE references are isolated by environment: a ref
  only resolves when the resolver's environment matches
  (``validate_refs`` reports mismatches by NAME, never values).
- This module imports stdlib + UBL vocabulary only — it never imports
  ``data.*`` / ``execution.*`` (no data↔execution cycle). Existing stores
  implement :class:`CredentialResolver` structurally (duck-typed) at the
  composition root.

No real credentials are requested, stored, or authenticated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from broker.vocab import Environment


class CredentialScope(StrEnum):
    """Which broker surface a credential reference unlocks."""

    HISTORICAL = "historical"
    TRADING = "trading"
    MARKET_DATA = "market_data"


@dataclass(frozen=True)
class CredentialRef:
    """A reference to one secret: broker + scope + environment + key name.

    Carries metadata only — there is deliberately no ``value`` field, so
    secret values cannot enter selection state, logs, events, journals or
    UI through this object. ``broker`` is ``None`` for venue-agnostic refs
    (e.g. shared historical credentials); broker-bound refs fail closed on
    broker mismatch.
    """

    key: str
    scope: CredentialScope
    environment: Environment
    required: bool = True
    broker: str | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValueError("credential ref requires a non-empty key name")
        if not isinstance(self.scope, CredentialScope):
            raise ValueError(f"credential scope must be a CredentialScope, got {self.scope!r}")
        if not isinstance(self.environment, Environment):
            raise ValueError(
                f"credential environment must be an Environment, got {self.environment!r}"
            )
        if self.broker is not None and (not self.broker or not self.broker.strip()):
            raise ValueError("credential broker must be a non-empty name or None")

    def __repr__(self) -> str:
        return (
            f"CredentialRef(key={self.key!r}, scope={self.scope.value!r}, "
            f"environment={self.environment.value!r}, secrets=<redacted>)"
        )


class CredentialResolver(Protocol):
    """Secret lookup by key name. Implementations never log values."""

    def resolve(self, ref: CredentialRef) -> str | None:
        """Return the secret value for ``ref``, or None when absent."""
        ...

    def resolver_environment(self) -> Environment | None:
        """Environment this resolver serves, or None when unscoped."""
        ...


def validate_refs(
    refs: tuple[CredentialRef, ...],
    resolver: CredentialResolver | None,
    *,
    expected_environment: Environment | None = None,
    expected_broker: str | None = None,
    expected_scope: CredentialScope | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Validate credential references WITHOUT exposing values.

    Returns (ok, reasons). Reasons name missing keys / broker mismatches /
    scope mismatches / environment mismatches — never secret values.
    Fail-closed: a missing resolver, a missing required key, or any
    broker/scope/environment mismatch denies.
    """
    reasons: list[str] = []
    scope = expected_environment
    if resolver is None:
        return False, ("no credential resolver configured",)
    served = resolver.resolver_environment()
    for ref in refs:
        if expected_broker is not None and ref.broker not in (None, expected_broker):
            reasons.append(
                f"credential {ref.key!r} is bound to broker {ref.broker!r}, "
                f"expected {expected_broker!r}"
            )
            continue
        if expected_scope is not None and ref.scope is not expected_scope:
            reasons.append(
                f"credential {ref.key!r} is scoped to {ref.scope.value!r}, "
                f"expected {expected_scope.value!r}"
            )
            continue
        if scope is not None and ref.environment is not scope:
            reasons.append(
                f"credential {ref.key!r} is scoped to {ref.environment.value!r}, "
                f"expected {scope.value!r}"
            )
            continue
        if served is not None and ref.environment is not served:
            reasons.append(
                f"credential {ref.key!r} requires {ref.environment.value!r}, "
                f"resolver serves {served.value!r}"
            )
            continue
        if ref.required and resolver.resolve(ref) is None:
            reasons.append(f"secret not resolvable: {ref.key}")
    return (not reasons, tuple(reasons))


@dataclass(frozen=True)
class CredentialMetadata:
    """Rotation/expiry metadata for one credential reference (FINAL §D).

    Carries lifecycle facts only — never secret values. ISO-8601 timestamps;
    empty strings mean unknown (fail-open never: unknown expiry is reported,
    rotation decisions stay with the operator/store).
    """

    key: str
    rotated_at: str = ""
    expires_at: str = ""
    broker: str | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValueError("credential metadata requires a non-empty key name")

    def _epoch(self, value: str) -> float | None:
        if not value:
            return None
        try:
            from datetime import datetime

            return datetime.fromisoformat(value).timestamp()
        except (TypeError, ValueError):
            return None

    def is_expired(self, now_epoch: float) -> bool:
        """True only when a valid expiry exists and has passed."""
        expiry = self._epoch(self.expires_at)
        return expiry is not None and now_epoch >= expiry

    def needs_rotation(self, now_epoch: float, max_age_seconds: float) -> bool:
        """True when rotation age is known and exceeded, or expiry is near.

        Unknown rotation timestamps never force rotation (reported, not
        acted on) — rotation stays an operator/store decision.
        """
        rotated = self._epoch(self.rotated_at)
        if rotated is not None and max_age_seconds >= 0 and now_epoch - rotated >= max_age_seconds:
            return True
        return self.is_expired(now_epoch)


def validate_metadata(
    entries: tuple[CredentialMetadata, ...], now_epoch: float
) -> tuple[bool, tuple[str, ...]]:
    """Reject expired credentials by NAME (fail-closed); report only names."""
    reasons = [
        f"credential expired: {entry.key}" for entry in entries if entry.is_expired(now_epoch)
    ]
    return (not reasons, tuple(reasons))


__all__ = [
    "CredentialMetadata",
    "CredentialRef",
    "CredentialResolver",
    "CredentialScope",
    "validate_metadata",
    "validate_refs",
]
