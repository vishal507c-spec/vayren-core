"""Canonical Instrument Registry — NSE Equity identity authority (Phase 2).

The SINGLE authoritative normalization/resolution path: every caller
(strategy universe store, and any future consumer) resolves through
:func:`resolve_instrument` here — never through a local copy of the
normalization rules.

Seed honesty: the registry boots from ``NSE_EQUITY_SEED``, the NSE-equity
vocabulary already verified inside this repository (the OBR C1C4
declaration plus the live-flow fixtures). That seed is deterministic and
broker-independent, but it is NOT a production instrument master — the
provider-master layer arrives in a later phase and must not be invented
here. Unknown symbols resolve NOT_FOUND; they are never auto-created.

Deliberately NOT here (future phases): provider/broker routing and
mappings, dynamic universe rules, universe versioning, failover.
"""

from __future__ import annotations

import threading
from typing import Any

from strategy.models.instrument import (
    EXCHANGE_NSE,
    INACTIVE,
    INSTRUMENT_TYPE_EQUITY,
    INVALID_FORMAT,
    NOT_FOUND,
    RESOLVED,
    SEGMENT_EQUITY,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    UNSUPPORTED_EXCHANGE,
    UNSUPPORTED_SEGMENT,
    CanonicalInstrument,
    ResolutionResult,
    canonical_id,
    parse_reference,
)

#: Deterministic NSE-equity seed: the repository's own verified vocabulary
#: (OBR_C1C4_SYMBOLS + live-flow fixtures). NOT a production master.
NSE_EQUITY_SEED: tuple[str, ...] = (
    "NSE:KAYNES",
    "NSE:DREDGECORP",
    "NSE:AVANTIFEED",
    "NSE:WAAREERTL",
    "NSE:ROUTE",
    "NSE:KEC",
    "NSE:RAILTEL",
    "NSE:SCI",
    "NSE:MOIL",
    "NSE:CRAMC",
    "NSE:LXCHEM",
    "NSE:NOCIL",
    "NSE:DAMCAPITAL",
    "NSE:JSWCEMENT",
    "NSE:TEXRAIL",
    "NSE:ZEEL",
    "NSE:VIKRAN",
    "NSE:IFCI",
    "NSE:DCW",
    "NSE:SAGILITY",
    "NSE:JINDWORLD",
    "NSE:NETWEB",
    "NSE:RELIANCE",
    "NSE:TCS",
    "NSE:INFY",
    "NSE:SBIN",
    "NSE:HDFCBANK",
)


class RegistryError(RuntimeError):
    """Refused registry mutation (duplicate identity, incoherent record)."""


class CanonicalInstrumentRegistry:
    """Source of truth for canonical instrument identity (NSE Equity)."""

    def __init__(self, seed: tuple[str, ...] = NSE_EQUITY_SEED) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, CanonicalInstrument] = {}
        for reference in seed:
            self.register_display(reference)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def register(self, instrument: CanonicalInstrument) -> CanonicalInstrument:
        """Store one record; duplicate identities are refused, never merged."""
        with self._lock:
            if instrument.exchange != EXCHANGE_NSE or instrument.segment != SEGMENT_EQUITY:
                raise RegistryError(
                    f"Phase 2 serves NSE EQUITY only, got {instrument.instrument_id!r}"
                )
            existing = self._records.get(instrument.instrument_id)
            if existing is not None and existing != instrument:
                raise RegistryError(f"duplicate canonical identity: {instrument.instrument_id!r}")
            self._records[instrument.instrument_id] = instrument
            return instrument

    def register_display(self, reference: str) -> CanonicalInstrument:
        """Register one ``NSE:SYMBOL`` display reference as ACTIVE."""
        parsed = parse_reference(reference)
        if parsed is None:
            raise RegistryError(f"cannot seed invalid reference: {reference!r}")
        exchange, segment, symbol = parsed
        return self.register(
            CanonicalInstrument(
                instrument_id=canonical_id(exchange, segment, symbol),
                exchange=exchange,
                segment=segment,
                symbol=symbol,
                instrument_type=INSTRUMENT_TYPE_EQUITY,
            )
        )

    def load_symbols(self, symbols: tuple[str, ...]) -> int:
        """Register verified NSE equity symbols as ACTIVE; returns records added.

        Existing identities are kept as they are, so this never demotes or
        duplicates a record. Symbols that fail the NSE display grammar are
        skipped, not coerced.
        """
        added = 0
        for symbol in symbols:
            reference = f"NSE:{str(symbol).strip().upper()}"
            if parse_reference(reference) is None:
                continue
            if self.get(canonical_id(EXCHANGE_NSE, SEGMENT_EQUITY, reference.split(":")[1])):
                continue
            self.register_display(reference)
            added += 1
        return added

    def get(self, instrument_id: str) -> CanonicalInstrument | None:
        """The record for a stable id, or None (never a guess)."""
        with self._lock:
            return self._records.get(instrument_id)

    def set_status(self, instrument_id: str, status: str) -> CanonicalInstrument:
        """Flip ACTIVE/INACTIVE; the identity itself is never deleted."""
        if status not in (STATUS_ACTIVE, STATUS_INACTIVE):
            raise RegistryError(f"status must be ACTIVE/INACTIVE, got {status!r}")
        with self._lock:
            current = self._records.get(instrument_id)
            if current is None:
                raise RegistryError(f"unknown instrument: {instrument_id!r}")
            updated = current.with_status(status)
            self._records[instrument_id] = updated
            return updated

    def resolve(self, raw: object) -> ResolutionResult:
        """Resolve one reference through the single canonical path."""
        parsed = parse_reference(raw)
        if parsed is None:
            return ResolutionResult(
                status=INVALID_FORMAT, reason=f"invalid instrument reference: {raw!r}"
            )
        exchange, segment, symbol = parsed
        if exchange != EXCHANGE_NSE:
            return ResolutionResult(
                status=UNSUPPORTED_EXCHANGE,
                reason=f"Phase 2 serves NSE only, got exchange {exchange!r}",
            )
        if segment != SEGMENT_EQUITY:
            return ResolutionResult(
                status=UNSUPPORTED_SEGMENT,
                reason=f"Phase 2 serves EQUITY only, got segment {segment!r}",
            )
        record = self.get(canonical_id(exchange, segment, symbol))
        if record is None:
            return ResolutionResult(
                status=NOT_FOUND,
                reason=f"instrument not found in NSE Equity registry: {exchange}:{symbol}",
            )
        if not record.is_active:
            return ResolutionResult(
                status=INACTIVE,
                instrument=record,
                reason=f"instrument is inactive: {record.display}",
            )
        return ResolutionResult(status=RESOLVED, instrument=record)

    def active_instruments(self) -> tuple[CanonicalInstrument, ...]:
        """All ACTIVE records, sorted by stable id (deterministic)."""
        with self._lock:
            return tuple(
                sorted(
                    (r for r in self._records.values() if r.is_active),
                    key=lambda r: r.instrument_id,
                )
            )

    def snapshot(self) -> dict[str, Any]:
        """Deterministic debug view (identity only, no provider data)."""
        with self._lock:
            return {
                instrument_id: {
                    "exchange": record.exchange,
                    "segment": record.segment,
                    "symbol": record.symbol,
                    "type": record.instrument_type,
                    "status": record.status,
                }
                for instrument_id, record in sorted(self._records.items())
            }


_REGISTRY: CanonicalInstrumentRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_instrument_registry() -> CanonicalInstrumentRegistry:
    """Process-wide canonical registry (seeded, broker-independent)."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = CanonicalInstrumentRegistry()
        return _REGISTRY


def reset_instrument_registry(
    seed: tuple[str, ...] = NSE_EQUITY_SEED,
) -> CanonicalInstrumentRegistry:
    """Rebuild the process registry (tests only — never production flow)."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = CanonicalInstrumentRegistry(seed=seed)
        return _REGISTRY


def resolve_instrument(raw: object) -> ResolutionResult:
    """Resolve one reference via the process registry (single path)."""
    return get_instrument_registry().resolve(raw)


__all__ = [
    "CanonicalInstrumentRegistry",
    "RegistryError",
    "NSE_EQUITY_SEED",
    "get_instrument_registry",
    "reset_instrument_registry",
    "resolve_instrument",
]
