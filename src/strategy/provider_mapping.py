"""ProviderMappingRegistry — canonical ↔ provider identity layer (Phase 3).

Owns ``CanonicalInstrument (1) → (*) ProviderInstrument`` mappings for
named providers (two supported today, open names for future ones). The
canonical side stays the single source of truth: mappings resolve THROUGH
the canonical registry, provider data can never create/rename a canonical
instrument, and conflicts are refused explicitly — never overwritten,
never guessed.

Provider-name dispatch lives in the adapter module (the only place
provider identity is interpreted); this registry only handles names as
opaque validated strings. Method names follow the spec API
(``register_provider_instrument``, ``resolve_provider_instrument``,
``map_provider_instrument``, ``get_mapping``, ``list_mappings``,
``mapping_status``) and deliberately avoid the bare ``register`` shape so
governance keeps seeing one canonical seeding act per registry file.

Method names follow the spec API (``register_provider_instrument``,
``resolve_provider_instrument``, ``map_provider_instrument``,
``get_mapping``, ``list_mappings``, ``mapping_status``) and deliberately
avoid the bare ``register`` shape so governance keeps seeing one
canonical seeding act per registry file.

Persistence mirrors the universe store (atomic tempfile+replace+fsync,
tolerant load). Mappings survive restart; import metadata (provider,
timestamp, source, outcome counts) is stored for diagnostics — not a
full audit engine (later phase).

Deliberately NOT here (future phases): market-data/broker routing,
failover, dynamic rules, universe versioning, explainability, any
execution/risk/strategy redesign.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from strategy.instrument_registry import get_instrument_registry
from strategy.models.instrument import RESOLVED
from strategy.models.provider_instrument import (
    AMBIGUOUS,
    CONFLICT,
    DISABLED,
    FOUND,
    MAPPED,
    NOT_FOUND,
    RECORD_ACTIVE,
    RECORD_DISABLED,
    UNMAPPED,
    ProviderInstrument,
)
from strategy.provider_adapters import (
    SUPPORTED_PROVIDER_NAMES,
    ProviderParseError,
    parse_master_row,
)

_STORE_KIND = "vayren.provider_mappings"
_STORE_VERSION = 1

_PROVIDER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class MappingError(RuntimeError):
    """Explicit mapping refusal (conflict, unknown provider, bad identity)."""


@dataclass(frozen=True)
class ProviderResolution:
    """One provider→canonical verdict (status + records, never a guess)."""

    status: str
    provider_instrument: ProviderInstrument | None = None
    canonical_instrument_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class MappingLookup:
    """One canonical→provider verdict (FOUND/NOT_FOUND/DISABLED/CONFLICT)."""

    status: str
    provider_instrument: ProviderInstrument | None = None
    reason: str = ""


@dataclass(frozen=True)
class ImportReport:
    """Deterministic master-ingest outcome (diagnostics, not an audit log)."""

    provider: str
    source: str
    imported_at: str
    total: int
    mapped: int
    unmapped: int
    ambiguous: int
    conflicting: int
    details: tuple[tuple[str, str, str], ...] = ()


def _normalize_provider(provider: object) -> str:
    name = str(provider or "").strip().upper()
    if not _PROVIDER_RE.match(name):
        raise MappingError(f"invalid provider name: {provider!r}")
    return name


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class ProviderMappingRegistry:
    """Canonical ↔ provider mappings with conflict-proof integrity."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path = Path(path) if path is not None else None
        self._records: dict[tuple[str, str], ProviderInstrument] = {}
        self._conflicts: dict[tuple[str, str], tuple[str, ...]] = {}
        self._imports: dict[str, dict[str, Any]] = {}
        if self._path is not None:
            self._load()

    # ── explicit single mapping (spec §13) ──────────────────────────

    def register_provider_instrument(
        self,
        provider: str,
        provider_instrument_id: str,
        canonical_instrument_id: str,
        source: str = "",
    ) -> ProviderInstrument:
        """Store one exact mapping; conflicts are refused, never merged."""
        provider_name = _normalize_provider(provider)
        pid = str(provider_instrument_id or "").strip()
        if not pid:
            raise MappingError("provider_instrument_id must be non-empty")
        canonical = get_instrument_registry().get(canonical_instrument_id)
        if canonical is None:
            raise MappingError(f"unknown canonical instrument: {canonical_instrument_id!r}")
        return self._store(
            ProviderInstrument(
                provider=provider_name,
                provider_instrument_id=pid,
                canonical_instrument_id=canonical.instrument_id,
                exchange=canonical.exchange,
                segment=canonical.segment,
                symbol=canonical.symbol,
                source=str(source or ""),
                updated_at=_utcnow_iso(),
            )
        )

    def map_provider_instrument(
        self,
        provider: str,
        provider_instrument_id: str,
        canonical_reference: str,
        source: str = "",
    ) -> ProviderInstrument:
        """Map via a human reference (resolved first — never guessed)."""
        result = get_instrument_registry().resolve(canonical_reference)
        if result.status != RESOLVED or result.instrument is None:
            raise MappingError(
                f"cannot map {provider_instrument_id!r}: canonical resolution "
                f"{result.status} ({result.reason})"
            )
        return self.register_provider_instrument(
            provider, provider_instrument_id, result.instrument.instrument_id, source
        )

    def _store(self, record: ProviderInstrument) -> ProviderInstrument:
        """Insert with both uniqueness invariants enforced (no overwrite)."""
        key = (record.provider, record.provider_instrument_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if (
                    existing.canonical_instrument_id == record.canonical_instrument_id
                    and existing.status == record.status
                ):
                    return existing  # idempotent re-registration
                self._conflicts[key] = tuple(
                    dict.fromkeys(
                        (existing.canonical_instrument_id, record.canonical_instrument_id)
                    )
                )
                raise MappingError(
                    f"conflict: {record.provider} {record.provider_instrument_id!r} "
                    f"already maps to {existing.canonical_instrument_id!r}; "
                    f"refusing {record.canonical_instrument_id!r}"
                )
            for (other_provider, _), other in self._records.items():
                if (
                    other_provider == record.provider
                    and other.canonical_instrument_id == record.canonical_instrument_id
                    and other.status == RECORD_ACTIVE
                    and record.status == RECORD_ACTIVE
                ):
                    raise MappingError(
                        f"conflict: {record.canonical_instrument_id!r} already has an "
                        f"active {record.provider} mapping "
                        f"({other.provider_instrument_id!r}); refusing "
                        f"{record.provider_instrument_id!r}"
                    )
            self._records[key] = record
            self._persist()
            return record

    # ── queries (spec §13–§15) ───────────────────────────────────────

    def resolve_provider_instrument(
        self, provider: str, provider_instrument_id: str
    ) -> ProviderResolution:
        """Provider id → canonical (MAPPED/UNMAPPED/DISABLED/CONFLICT)."""
        try:
            provider_name = _normalize_provider(provider)
        except MappingError as exc:
            return ProviderResolution(status=UNMAPPED, reason=str(exc))
        pid = str(provider_instrument_id or "").strip()
        key = (provider_name, pid)
        with self._lock:
            if key in self._conflicts:
                established = self._records.get(key)
                return ProviderResolution(
                    status=CONFLICT,
                    provider_instrument=established,
                    canonical_instrument_id=(
                        established.canonical_instrument_id if established is not None else ""
                    ),
                    reason=f"conflicting canonical claims for {provider_name} {pid!r}",
                )
            record = self._records.get(key)
        if record is None:
            return ProviderResolution(
                status=UNMAPPED,
                reason=f"no {provider_name} mapping for {pid!r}",
            )
        if not record.is_active:
            return ProviderResolution(
                status=DISABLED, provider_instrument=record, reason="mapping disabled"
            )
        return ProviderResolution(
            status=MAPPED,
            provider_instrument=record,
            canonical_instrument_id=record.canonical_instrument_id,
        )

    def resolve_provider_to_canonical(
        self, provider: str, provider_instrument_id: str
    ) -> tuple[str, Any]:
        """Reverse lookup → (status, CanonicalInstrument | None)."""
        outcome = self.resolve_provider_instrument(provider, provider_instrument_id)
        if outcome.status != MAPPED:
            return (outcome.status, None)
        return (MAPPED, get_instrument_registry().get(outcome.canonical_instrument_id))

    def get_mapping(self, canonical_instrument_id: str, provider: str) -> MappingLookup:
        """Canonical + provider → FOUND/NOT_FOUND/DISABLED/CONFLICT."""
        try:
            provider_name = _normalize_provider(provider)
        except MappingError as exc:
            return MappingLookup(status=NOT_FOUND, reason=str(exc))
        with self._lock:
            hits = [
                record
                for (prov, _), record in self._records.items()
                if prov == provider_name
                and record.canonical_instrument_id == canonical_instrument_id
            ]
            conflicted = any(
                canonical_instrument_id in claimed
                for (prov, _), claimed in self._conflicts.items()
                if prov == provider_name
            )
        if conflicted:
            return MappingLookup(
                status=CONFLICT,
                reason=f"conflicting {provider_name} claims on {canonical_instrument_id!r}",
            )
        if not hits:
            return MappingLookup(
                status=NOT_FOUND,
                reason=f"no {provider_name} mapping for {canonical_instrument_id!r}",
            )
        record = sorted(hits, key=lambda r: r.provider_instrument_id)[0]
        if not record.is_active:
            return MappingLookup(
                status=DISABLED, provider_instrument=record, reason="mapping disabled"
            )
        return MappingLookup(status=FOUND, provider_instrument=record)

    def get_provider_mapping(self, canonical_id: str, provider: str) -> MappingLookup:
        """Spec §14 alias for :meth:`get_mapping`."""
        return self.get_mapping(canonical_id, provider)

    def list_mappings(
        self,
        provider: str | None = None,
        canonical_instrument_id: str | None = None,
        active_only: bool = True,
    ) -> tuple[ProviderInstrument, ...]:
        """Stored mappings, optionally filtered (deterministic order)."""
        provider_name = _normalize_provider(provider) if provider is not None else None
        with self._lock:
            rows = [
                record
                for (prov, _), record in self._records.items()
                if (provider_name is None or prov == provider_name)
                and (
                    canonical_instrument_id is None
                    or record.canonical_instrument_id == canonical_instrument_id
                )
                and (not active_only or record.is_active)
            ]
        return tuple(sorted(rows, key=lambda r: (r.provider, r.provider_instrument_id)))

    def mapping_status(self, provider: str | None = None) -> dict[str, Any]:
        """Counts + import metadata for diagnostics (not an audit engine)."""
        provider_name = _normalize_provider(provider) if provider is not None else None
        with self._lock:
            rows = [
                record
                for (prov, _), record in self._records.items()
                if provider_name is None or prov == provider_name
            ]
            conflict_count = sum(
                1 for (prov, _) in self._conflicts if provider_name is None or prov == provider_name
            )
            imports = (
                dict(self._imports)
                if provider_name is None
                else {provider_name: self._imports.get(provider_name, {})}
            )
        return {
            "mapped": sum(1 for r in rows if r.is_active),
            "disabled": sum(1 for r in rows if not r.is_active),
            "conflicted": conflict_count,
            "imports": imports,
        }

    def disable_mapping(self, provider: str, provider_instrument_id: str) -> ProviderInstrument:
        """Disable a mapping (record kept for history — never deleted)."""
        return self._flip(provider, provider_instrument_id, RECORD_DISABLED)

    def enable_mapping(self, provider: str, provider_instrument_id: str) -> ProviderInstrument:
        """Re-enable a disabled mapping (conflict rules still apply)."""
        return self._flip(provider, provider_instrument_id, RECORD_ACTIVE)

    def _flip(self, provider: str, provider_instrument_id: str, status: str) -> ProviderInstrument:
        provider_name = _normalize_provider(provider)
        pid = str(provider_instrument_id or "").strip()
        with self._lock:
            record = self._records.get((provider_name, pid))
            if record is None:
                raise MappingError(f"no {provider_name} mapping for {pid!r}")
            if status == RECORD_ACTIVE:
                for (other_provider, _), other in self._records.items():
                    if (
                        other_provider == provider_name
                        and other.canonical_instrument_id == record.canonical_instrument_id
                        and other.is_active
                        and (other_provider, other.provider_instrument_id) != (provider_name, pid)
                    ):
                        raise MappingError(
                            f"conflict: {record.canonical_instrument_id!r} already has "
                            f"an active {provider_name} mapping"
                        )
            updated = record.with_status(status, _utcnow_iso())
            self._records[(provider_name, pid)] = updated
            self._persist()
            return updated

    # ── master ingest (spec §6–§8, §11) ─────────────────────────────

    def ingest_master(self, provider: str, records: list[Any], source: str = "") -> ImportReport:
        """Ingest one provider master (plain rows, never SDK objects).

        Each row is parsed by the provider adapter, matched EXACTLY against
        the canonical registry, and mapped under the conflict rules. Unknown
        rows stay UNMAPPED (no canonical auto-creation, ever); ambiguous
        rows stay AMBIGUOUS; conflicts are recorded, never overwritten.
        """
        provider_name = _normalize_provider(provider)
        if provider_name not in SUPPORTED_PROVIDER_NAMES:
            raise MappingError(
                f"no master adapter for provider {provider_name!r} "
                f"(supported: {', '.join(SUPPORTED_PROVIDER_NAMES)})"
            )
        mapped = unmapped = ambiguous = conflicting = 0
        details: list[tuple[str, str, str]] = []
        for row in records:
            try:
                pid, _exchange, _segment, symbol = parse_master_row(provider_name, row)
            except ProviderParseError as exc:
                message = str(exc)
                if "ambiguous" in message.lower():
                    ambiguous += 1
                    details.append((str(row), AMBIGUOUS, message))
                else:
                    unmapped += 1
                    details.append((str(row), UNMAPPED, message))
                continue
            outcome = get_instrument_registry().resolve(f"NSE:{symbol}")
            if outcome.status != RESOLVED or outcome.instrument is None:
                unmapped += 1
                details.append((pid, UNMAPPED, f"NO_CANONICAL_MATCH for NSE:{symbol}"))
                continue
            try:
                self._store(
                    ProviderInstrument(
                        provider=provider_name,
                        provider_instrument_id=pid,
                        canonical_instrument_id=outcome.instrument.instrument_id,
                        exchange=outcome.instrument.exchange,
                        segment=outcome.instrument.segment,
                        symbol=outcome.instrument.symbol,
                        source=str(source or ""),
                        updated_at=_utcnow_iso(),
                    )
                )
            except MappingError as exc:
                conflicting += 1
                details.append((pid, CONFLICT, str(exc)))
                continue
            mapped += 1
            details.append((pid, MAPPED, outcome.instrument.instrument_id))
        report = ImportReport(
            provider=provider_name,
            source=str(source or ""),
            imported_at=_utcnow_iso(),
            total=len(records),
            mapped=mapped,
            unmapped=unmapped,
            ambiguous=ambiguous,
            conflicting=conflicting,
            details=tuple(details),
        )
        with self._lock:
            self._imports[provider_name] = {
                "source": report.source,
                "imported_at": report.imported_at,
                "total": report.total,
                "mapped": report.mapped,
                "unmapped": report.unmapped,
                "ambiguous": report.ambiguous,
                "conflicting": report.conflicting,
            }
            self._persist()
        return report

    # ── persistence (spec §12) ──────────────────────────────────────

    def _persist(self) -> None:
        if self._path is None:
            return
        payload: dict[str, Any] = {
            "kind": _STORE_KIND,
            "version": _STORE_VERSION,
            "mappings": {},
            "conflicts": {},
            "imports": self._imports,
        }
        for (provider, pid), record in sorted(self._records.items()):
            payload["mappings"].setdefault(provider, {})[pid] = {
                "canonical_id": record.canonical_instrument_id,
                "exchange": record.exchange,
                "segment": record.segment,
                "symbol": record.symbol,
                "status": record.status,
                "source": record.source,
                "updated_at": record.updated_at,
            }
        for (provider, pid), claimed in sorted(self._conflicts.items()):
            payload["conflicts"].setdefault(provider, {})[pid] = list(claimed)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=1))
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        tmp.replace(self._path)
        with contextlib.suppress(OSError):
            dir_fd = os.open(str(self._path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            logging.getLogger(__name__).warning(
                "provider mapping file %s is corrupt; starting empty", self._path
            )
            return
        if not isinstance(data, dict) or data.get("kind", _STORE_KIND) != _STORE_KIND:
            logging.getLogger(__name__).warning(
                "provider mapping file %s has unknown kind; starting empty", self._path
            )
            return
        stored = data.get("mappings", {})
        if not isinstance(stored, dict):
            return
        for provider, section in stored.items():
            if not isinstance(section, dict):
                continue
            for pid, entry in section.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    record = ProviderInstrument(
                        provider=str(provider),
                        provider_instrument_id=str(pid),
                        canonical_instrument_id=str(entry.get("canonical_id", "")),
                        exchange=str(entry.get("exchange", "")),
                        segment=str(entry.get("segment", "")),
                        symbol=str(entry.get("symbol", "")),
                        status=str(entry.get("status", RECORD_ACTIVE)),
                        source=str(entry.get("source", "")),
                        updated_at=str(entry.get("updated_at", "")),
                    )
                except ValueError:
                    logging.getLogger(__name__).warning(
                        "provider mapping file %s holds an invalid record; skipping",
                        self._path,
                    )
                    continue
                self._records[(record.provider, record.provider_instrument_id)] = record
        stored_conflicts = data.get("conflicts", {})
        if isinstance(stored_conflicts, dict):
            for provider, section in stored_conflicts.items():
                if not isinstance(section, dict):
                    continue
                for pid, claimed in section.items():
                    if isinstance(claimed, list) and claimed:
                        self._conflicts[(str(provider), str(pid))] = tuple(str(c) for c in claimed)
        imports = data.get("imports", {})
        if isinstance(imports, dict):
            self._imports = {
                str(provider): dict(info)
                for provider, info in imports.items()
                if isinstance(info, dict)
            }


__all__ = [
    "ProviderMappingRegistry",
    "ProviderResolution",
    "MappingLookup",
    "ImportReport",
    "MappingError",
]
