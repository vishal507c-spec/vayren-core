"""Capability model — queryable truth about what a broker can do.

Capability ids are ``"<domain>.<sub-capability>"`` strings, centrally named
in :class:`Caps`. A :class:`CapabilitySet` is an immutable declaration;
discovery is explicit (never probed at call time) and unsupported requests
fail closed upstream.

Tri-state declaration (M8): :class:`CapabilityStatus` distinguishes
``SUPPORTED`` (advertised), ``NOT_SUPPORTED`` (known capability id, absent
from this broker) and ``NOT_CONFIGURED`` (unknown id, or an undeclared /
unconfigured broker context). Bool helpers (``supports`` /
``supports_domain``) are preserved for backward compatibility.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from broker.vocab import Domain


class CapabilityStatus(StrEnum):
    """Tri-state capability declaration (M8 §3).

    - ``SUPPORTED`` — the broker advertises the exact capability id.
    - ``NOT_SUPPORTED`` — the id is a known capability but this broker
      does not advertise it (fail-closed, never silently fall back).
    - ``NOT_CONFIGURED`` — the id is unknown, or the broker context is
      undeclared (no capabilities published at all).
    """

    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class Caps:
    """Well-known capability ids (design §4.2)."""

    # historical_data domain
    HIST_CANDLES = "historical_data.candles"
    HIST_SYMBOLS = "historical_data.symbols"

    # market_data domain
    MD_CANDLE_STREAM = "market_data.candle_stream"
    MD_QUOTES = "market_data.quotes"
    MD_DEPTH = "market_data.depth"
    MD_HEARTBEAT = "market_data.heartbeat"

    # trading domain (superset of the execution layer's BrokerCapabilities)
    ORDERS_MARKET = "orders.market"
    ORDERS_LIMIT = "orders.limit"
    ORDERS_CANCEL = "orders.cancel"
    ORDERS_MODIFY = "orders.modify"
    ACCOUNT_POSITIONS = "account.positions"
    ACCOUNT_OPEN_ORDERS = "account.open_orders"
    ACCOUNT_FUNDS = "account.funds"
    STREAM_FILLS = "stream.fills"


DOMAIN_ITEMS: dict[Domain, tuple[str, ...]] = {
    Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS),
    Domain.MARKET_DATA: (Caps.MD_CANDLE_STREAM, Caps.MD_QUOTES, Caps.MD_DEPTH, Caps.MD_HEARTBEAT),
    Domain.TRADING: (
        Caps.ORDERS_MARKET,
        Caps.ORDERS_LIMIT,
        Caps.ORDERS_CANCEL,
        Caps.ORDERS_MODIFY,
        Caps.ACCOUNT_POSITIONS,
        Caps.ACCOUNT_OPEN_ORDERS,
        Caps.ACCOUNT_FUNDS,
        Caps.STREAM_FILLS,
    ),
}


@dataclass(frozen=True)
class CapabilitySet:
    """Immutable capability declaration across the three domains."""

    domains: tuple[Domain, ...] = ()
    items: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        outside = self.items - {cap for caps in DOMAIN_ITEMS.values() for cap in caps}
        if outside:
            raise ValueError(f"unknown capability ids: {sorted(outside)}")
        implied = {
            domain for domain, caps in DOMAIN_ITEMS.items() if caps and self.items & set(caps)
        }
        missing_domains = implied - set(self.domains)
        if missing_domains:
            raise ValueError(
                f"domains must list every domain with items; missing: "
                f"{sorted(d.value for d in missing_domains)}"
            )
        for domain in self.domains:
            domain_items = DOMAIN_ITEMS.get(domain)
            if domain_items is None:
                raise ValueError(f"unknown domain entry: {domain!r}")
            if not self.items & set(domain_items):
                raise ValueError(
                    f"domain {domain.value!r} listed without any of its items (no phantom domains)"
                )

    def supports(self, cap: str) -> bool:
        """True when the exact capability id is advertised."""
        return cap in self.items

    def status(self, cap: str) -> CapabilityStatus:
        """Tri-state declaration for one capability id (M8 §3).

        ``SUPPORTED`` when advertised; ``NOT_SUPPORTED`` when the id is a
        known capability but absent; ``NOT_CONFIGURED`` when the id is
        unknown or this set declares nothing at all (undeclared broker).
        """
        if cap in self.items:
            return CapabilityStatus.SUPPORTED
        if not self.items:
            return CapabilityStatus.NOT_CONFIGURED
        known = {known_cap for caps in DOMAIN_ITEMS.values() for known_cap in caps}
        if cap not in known:
            return CapabilityStatus.NOT_CONFIGURED
        return CapabilityStatus.NOT_SUPPORTED

    def supports_domain(self, domain: Domain) -> bool:
        """True when any capability of the domain is advertised."""
        return bool(self.items & set(DOMAIN_ITEMS.get(domain, ())))

    def missing(self, caps: Iterable[str]) -> tuple[str, ...]:
        """Requested capabilities NOT advertised (empty tuple = all present)."""
        wanted = tuple(caps)
        return tuple(cap for cap in wanted if cap not in self.items)

    def union(self, other: CapabilitySet) -> CapabilitySet:
        """Merge two sets (consistency rule 3: plugin set = union of faces)."""
        return CapabilitySet(
            domains=tuple(dict.fromkeys((*self.domains, *other.domains))),
            items=self.items | other.items,
        )


def capability_set(domains_and_items: dict[Domain, Iterable[str]]) -> CapabilitySet:
    """Build a validated :class:`CapabilitySet` from a domain→items map."""
    domains: list[Domain] = []
    items: set[str] = set()
    for domain, caps in domains_and_items.items():
        domains.append(domain)
        items.update(caps)
    return CapabilitySet(domains=tuple(domains), items=frozenset(items))


def capability_status(caps: CapabilitySet | None, cap: str) -> CapabilityStatus:
    """Tri-state declaration with broker context (M8 §3).

    ``None`` (undeclared capabilities) → ``NOT_CONFIGURED``; otherwise
    delegates to :meth:`CapabilitySet.status`.
    """
    if caps is None:
        return CapabilityStatus.NOT_CONFIGURED
    return caps.status(cap)


# Execution-layer BrokerCapabilities strings are byte-identical to UBL
# trading-domain ids, so trading sets translate 1:1. Market-data legacy
# strings map onto the four MD ids (transport liveness groups heartbeat +
# reconnect; quote-level data groups tick/quote/trade).
_TRADING_LEGACY_MAP: dict[str, str] = {
    "orders.market": Caps.ORDERS_MARKET,
    "orders.limit": Caps.ORDERS_LIMIT,
    "orders.cancel": Caps.ORDERS_CANCEL,
    "orders.modify": Caps.ORDERS_MODIFY,
    "account.positions": Caps.ACCOUNT_POSITIONS,
    "account.open_orders": Caps.ACCOUNT_OPEN_ORDERS,
    "stream.events": Caps.STREAM_FILLS,
}

_MARKET_DATA_LEGACY_MAP: dict[str, str] = {
    "tick": Caps.MD_QUOTES,
    "quote": Caps.MD_QUOTES,
    "trade": Caps.MD_QUOTES,
    "candle-close": Caps.MD_CANDLE_STREAM,
    "order-book": Caps.MD_DEPTH,
    "heartbeat": Caps.MD_HEARTBEAT,
    "reconnect": Caps.MD_HEARTBEAT,
}


def trading_set_from_legacy(legacy: Iterable[str]) -> CapabilitySet:
    """Translate execution-layer legacy trading caps into a CapabilitySet."""
    items = tuple(_TRADING_LEGACY_MAP[cap] for cap in legacy)
    return CapabilitySet(domains=(Domain.TRADING,), items=frozenset(items))


def market_data_set_from_legacy(legacy: Iterable[str]) -> CapabilitySet:
    """Translate execution-layer legacy market-data caps into a CapabilitySet."""
    items = tuple(_MARKET_DATA_LEGACY_MAP[cap] for cap in legacy if cap in _MARKET_DATA_LEGACY_MAP)
    return CapabilitySet(domains=(Domain.MARKET_DATA,), items=frozenset(items))
