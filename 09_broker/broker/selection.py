"""BrokerSelection — the single source of truth for the chosen broker
(design §4.4/§8, M3 contract only; NO UX wiring — that is M4).

For a session, exactly one selection exists. The pure
:func:`surface_resolution` helper encodes the authoritative-broker rule
(design §4.4 table) so M4 wiring becomes mechanical:

- historical download → historical_data face required, else fail-closed
  (an explicit alternate data broker is a recorded override, never silent);
- trading → trading face required, else the existing PAPER downgrade;
- market data → market_data face required, else the offline/replay path
  with a recorded reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from broker.capabilities import CapabilitySet, CapabilityStatus, Domain
from broker.vocab import Environment


class SelectionError(ValueError):
    """Invalid broker selection (fail-closed before any surface use)."""


class BrokerSelection:
    """The recorded fact: which broker is authoritative, for what."""

    __slots__ = ("name", "environment", "selected_at", "reason")

    def __init__(self, name: str, environment: Environment, selected_at: str, reason: str) -> None:
        if not name or not name.strip():
            raise SelectionError("broker selection requires a non-empty name")
        if not isinstance(environment, Environment):
            raise SelectionError(f"environment must be an Environment, got {environment!r}")
        try:
            datetime.fromisoformat(selected_at)
        except (TypeError, ValueError) as exc:
            raise SelectionError(f"selected_at must be ISO-8601: {selected_at!r}") from exc
        if not reason or not reason.strip():
            raise SelectionError("broker selection requires a recorded reason")
        self.name = name
        self.environment = environment
        self.selected_at = selected_at
        self.reason = reason

    def __repr__(self) -> str:
        return (
            f"BrokerSelection(name={self.name!r}, environment={self.environment.value!r}, "
            f"selected_at={self.selected_at!r}, reason={self.reason!r})"
        )


def surface_resolution(
    selection: BrokerSelection | None,
    record_capabilities: CapabilitySet | None,
    domain: Domain,
) -> tuple[bool, str]:
    """Pure authoritative-broker rule (design §4.4). Never raises.

    Returns (allowed, reason). No selection → not allowed with a recorded
    reason (fail-closed); a selection whose broker lacks the domain is not
    allowed for that surface — for trading the caller applies the existing
    PAPER downgrade using the returned reason.
    """
    domain_value = domain.value if isinstance(domain, Domain) else str(domain)
    if selection is None:
        return False, "no broker selected"
    if record_capabilities is None:
        return False, f"broker {selection.name!r} capabilities are undeclared"
    if not record_capabilities.supports_domain(domain):
        return (
            False,
            f"broker {selection.name!r} does not provide {domain_value} capability "
            f"(explicit override required for this surface)",
        )
    return True, f"broker {selection.name!r} serves {domain_value}"


def surface_status(
    selection: BrokerSelection | None,
    record_capabilities: CapabilitySet | None,
    domain: Domain,
) -> tuple[CapabilityStatus, str]:
    """Tri-state authoritative-broker rule (M8 §3).

    Same fail-closed matrix as :func:`surface_resolution`, but the verdict
    is a :class:`CapabilityStatus`: no selection or undeclared capabilities
    → ``NOT_CONFIGURED``; broker lacking the domain → ``NOT_SUPPORTED``;
    broker serving the domain → ``SUPPORTED``. Never raises.
    """
    domain_value = domain.value if isinstance(domain, Domain) else str(domain)
    if selection is None:
        return CapabilityStatus.NOT_CONFIGURED, "no broker selected"
    if record_capabilities is None or not record_capabilities.items:
        return (
            CapabilityStatus.NOT_CONFIGURED,
            f"broker {selection.name!r} capabilities are undeclared",
        )
    if not record_capabilities.supports_domain(domain):
        return (
            CapabilityStatus.NOT_SUPPORTED,
            f"broker {selection.name!r} does not provide {domain_value} capability "
            f"(explicit override required for this surface)",
        )
    return CapabilityStatus.SUPPORTED, f"broker {selection.name!r} serves {domain_value}"


class SelectionStore(Protocol):
    """Persistence for the session selection (design §4.4)."""

    def load(self) -> BrokerSelection | None: ...

    def save(self, selection: BrokerSelection) -> None: ...

    def clear(self) -> None: ...


class MemorySelectionStore:
    """Session-scoped in-memory store (M3 contract; file store is M4)."""

    def __init__(self) -> None:
        self._selection: BrokerSelection | None = None

    def load(self) -> BrokerSelection | None:
        return self._selection

    def save(self, selection: BrokerSelection) -> None:
        self._selection = selection

    def clear(self) -> None:
        self._selection = None
