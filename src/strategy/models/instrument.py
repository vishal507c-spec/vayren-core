"""CanonicalInstrument — broker-independent instrument identity (Phase 2).

Identity is ``exchange + segment + symbol + instrument_type`` and nothing
else. There are deliberately NO vendor/broker/provider fields here:
provider mappings belong to a future layer and must never leak into
identity.

Phase 2 scope is strictly NSE EQUITY. The model carries exchange/segment
fields so later phases can extend, but resolution only serves NSE EQUITY.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Phase 2 scope — the only exchange/segment/instrument-type served.
EXCHANGE_NSE = "NSE"
SEGMENT_EQUITY = "EQUITY"
INSTRUMENT_TYPE_EQUITY = "EQUITY"

#: Instrument lifecycle states.
STATUS_ACTIVE = "ACTIVE"
STATUS_INACTIVE = "INACTIVE"

#: Resolver outcomes (single authoritative vocabulary).
RESOLVED = "RESOLVED"
NOT_FOUND = "NOT_FOUND"
INVALID_FORMAT = "INVALID_FORMAT"
UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"
UNSUPPORTED_SEGMENT = "UNSUPPORTED_SEGMENT"
INACTIVE = "INACTIVE"


def canonical_id(exchange: str, segment: str, symbol: str) -> str:
    """Stable VAYREN-owned identifier for a canonical tuple.

    Deterministic and broker-independent (``NSE:EQUITY:RELIANCE``) — never
    a vendor, broker, or provider id.
    """
    return f"{exchange}:{segment}:{symbol}"


def parse_reference(raw: object) -> tuple[str, str, str] | None:
    """Parse one user-facing reference into (exchange, segment, symbol).

    Accepts ``NSE:RELIANCE`` (segment defaults to EQUITY) and the explicit
    ``NSE:EQUITY:RELIANCE`` form. Case/whitespace are normalized; anything
    else (bare names, empty parts, extra parts) returns None so the caller
    reports INVALID_FORMAT instead of guessing.
    """
    if not isinstance(raw, str):
        return None
    parts = [piece.strip().upper() for piece in raw.strip().split(":")]
    if len(parts) == 2 and all(parts):
        exchange, symbol = parts
        return (exchange, SEGMENT_EQUITY, symbol)
    if len(parts) == 3 and all(parts):
        exchange, segment, symbol = parts
        return (exchange, segment, symbol)
    return None


@dataclass(frozen=True)
class CanonicalInstrument:
    """One broker-independent NSE Equity instrument.

    Attributes:
        instrument_id: Stable VAYREN-owned id (``NSE:EQUITY:RELIANCE``).
        exchange: ``NSE`` (Phase 2 scope).
        segment: ``EQUITY`` (Phase 2 scope).
        symbol: Bare symbol (``RELIANCE``).
        instrument_type: ``EQUITY`` (Phase 2 scope).
        status: ``ACTIVE`` or ``INACTIVE``. Inactive instruments stay in
            the registry — the identity is never deleted.
    """

    instrument_id: str
    exchange: str
    segment: str
    symbol: str
    instrument_type: str
    status: str = STATUS_ACTIVE

    def __post_init__(self) -> None:
        """Reject incoherent identity (id must match the tuple)."""
        for field_name in ("instrument_id", "exchange", "segment", "symbol", "instrument_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"CanonicalInstrument {field_name} must be non-empty")
        if self.status not in (STATUS_ACTIVE, STATUS_INACTIVE):
            raise ValueError(
                f"CanonicalInstrument status must be ACTIVE/INACTIVE, got {self.status!r}"
            )
        expected = canonical_id(self.exchange, self.segment, self.symbol)
        if self.instrument_id != expected:
            raise ValueError(
                f"instrument_id {self.instrument_id!r} does not match identity {expected!r}"
            )

    @property
    def is_active(self) -> bool:
        """True while the instrument is eligible for configuration."""
        return self.status == STATUS_ACTIVE

    @property
    def display(self) -> str:
        """User-facing identity (``NSE:RELIANCE``) — never the internal id."""
        return f"{self.exchange}:{self.symbol}"

    def with_status(self, status: str) -> CanonicalInstrument:
        """Return a copy carrying a new lifecycle status."""
        return CanonicalInstrument(
            instrument_id=self.instrument_id,
            exchange=self.exchange,
            segment=self.segment,
            symbol=self.symbol,
            instrument_type=self.instrument_type,
            status=status,
        )


@dataclass(frozen=True)
class ResolutionResult:
    """One resolver verdict: status plus the instrument when resolved."""

    status: str
    instrument: CanonicalInstrument | None = None
    reason: str = ""

    @property
    def resolved(self) -> CanonicalInstrument | None:
        """The instrument on RESOLVED, else None (never a guess)."""
        return self.instrument if self.status == RESOLVED else None


__all__ = [
    "CanonicalInstrument",
    "ResolutionResult",
    "canonical_id",
    "parse_reference",
    "EXCHANGE_NSE",
    "SEGMENT_EQUITY",
    "INSTRUMENT_TYPE_EQUITY",
    "STATUS_ACTIVE",
    "STATUS_INACTIVE",
    "RESOLVED",
    "NOT_FOUND",
    "INVALID_FORMAT",
    "UNSUPPORTED_EXCHANGE",
    "UNSUPPORTED_SEGMENT",
    "INACTIVE",
]
