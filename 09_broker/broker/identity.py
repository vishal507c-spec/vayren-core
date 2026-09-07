"""UBL broker identity — one typed identity for every venue (FINAL §C).

Identity distinguishes ``broker_id`` / ``display_name`` / ``environment``
/ ``account_id`` without scattering broker-specific dictionaries through
execution and without any ``if broker == ...`` branching. Capability
checks stay name-blind; identity is for display, audit and journal only.
"""

from __future__ import annotations

from dataclasses import dataclass

from broker.vocab import Environment


@dataclass(frozen=True)
class BrokerIdentity:
    """Typed venue identity. ``account_id`` is empty until an account is
    confirmed; it never carries secret values (ids only)."""

    broker_id: str
    display_name: str
    environment: Environment
    account_id: str = ""

    def __post_init__(self) -> None:
        if not self.broker_id or not self.broker_id.strip():
            raise ValueError("broker identity requires a non-empty broker_id")
        if not self.display_name or not self.display_name.strip():
            raise ValueError("broker identity requires a non-empty display_name")
        if not isinstance(self.environment, Environment):
            raise ValueError(
                f"broker identity environment must be an Environment, got {self.environment!r}"
            )
        if not isinstance(self.account_id, str):
            raise ValueError(
                f"broker identity account_id must be a string, got {self.account_id!r}"
            )

    def describe(self) -> str:
        """Stable human rendering for UI/journal (never a lookup key)."""
        base = f"{self.display_name} ({self.environment.value})"
        return f"{base} [{self.account_id}]" if self.account_id else base


def identity_of(
    broker_id: str,
    display_name: str,
    environment: Environment,
    account_id: str = "",
) -> BrokerIdentity:
    """Build the canonical identity for a registry record + selection."""
    return BrokerIdentity(
        broker_id=broker_id,
        display_name=display_name,
        environment=environment,
        account_id=account_id,
    )


__all__ = [
    "BrokerIdentity",
    "identity_of",
]
