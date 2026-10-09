"""ProviderInstrument — one provider-side instrument record (Phase 3).

A provider record is the provider's OWN view of an instrument (its id, its
symbol spelling, its lifecycle) mapped onto exactly one canonical
instrument. Provider knowledge stops here: no provider id, token, or symbol
may leak into CanonicalInstrument, strategy universes, risk, or execution.

Status vocabulary (stored record state):
- ACTIVE: eligible for mapping queries.
- DISABLED: kept for history/diagnostics, never returned as a live mapping.
  A provider-side inactive record ingests as DISABLED.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Supported providers (names only — never SDK objects). New providers join
#: by name without touching CanonicalInstrument.
PROVIDER_FYERS = "FYERS"
PROVIDER_ZERODHA = "ZERODHA"

#: Stored record lifecycle.
RECORD_ACTIVE = "ACTIVE"
RECORD_DISABLED = "DISABLED"

#: Mapping query/resolution states.
MAPPED = "MAPPED"
UNMAPPED = "UNMAPPED"
AMBIGUOUS = "AMBIGUOUS"
CONFLICT = "CONFLICT"
DISABLED = "DISABLED"

#: Canonical-to-provider lookup states.
FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class ProviderInstrument:
    """One provider-side instrument mapped to a canonical instrument.

    Attributes:
        provider: Provider name (``FYERS``, ``ZERODHA``, ...).
        provider_instrument_id: The provider's own identifier (FYERS
            symbol ``NSE:SBIN-EQ``, Zerodha token ``"779521"`` as text).
        canonical_instrument_id: Stable ``NSE:EQUITY:*`` id (authority).
        exchange: Normalized exchange (``NSE`` in Phase 3 scope).
        segment: Normalized segment (``EQUITY`` in Phase 3 scope).
        symbol: Normalized bare symbol (``SBIN``).
        status: ``ACTIVE`` or ``DISABLED``.
        source: Import source identifier (master file/feed label).
        updated_at: ISO-8601 UTC timestamp of the last write.
    """

    provider: str
    provider_instrument_id: str
    canonical_instrument_id: str
    exchange: str
    segment: str
    symbol: str
    status: str = RECORD_ACTIVE
    source: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        """Reject empty identity fields and unknown record states."""
        for field_name in (
            "provider",
            "provider_instrument_id",
            "canonical_instrument_id",
            "exchange",
            "segment",
            "symbol",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ProviderInstrument {field_name} must be non-empty")
        if self.status not in (RECORD_ACTIVE, RECORD_DISABLED):
            raise ValueError(
                f"ProviderInstrument status must be ACTIVE/DISABLED, got {self.status!r}"
            )

    @property
    def is_active(self) -> bool:
        """True while the record participates in live mapping queries."""
        return self.status == RECORD_ACTIVE

    def with_status(self, status: str, updated_at: str = "") -> ProviderInstrument:
        """Return a copy carrying a new lifecycle status."""
        return ProviderInstrument(
            provider=self.provider,
            provider_instrument_id=self.provider_instrument_id,
            canonical_instrument_id=self.canonical_instrument_id,
            exchange=self.exchange,
            segment=self.segment,
            symbol=self.symbol,
            status=status,
            source=self.source,
            updated_at=updated_at or self.updated_at,
        )


__all__ = [
    "ProviderInstrument",
    "PROVIDER_FYERS",
    "PROVIDER_ZERODHA",
    "RECORD_ACTIVE",
    "RECORD_DISABLED",
    "MAPPED",
    "UNMAPPED",
    "AMBIGUOUS",
    "CONFLICT",
    "DISABLED",
    "FOUND",
    "NOT_FOUND",
]
