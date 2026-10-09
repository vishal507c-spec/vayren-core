"""StrategyUniverseStore — per-strategy universe persistence (Phase 1 + 2).

One JSON file maps each strategy to its own saved symbol set (schema v2)::

    {"kind": "vayren.strategy_universes", "version": 2,
     "universes": {"obr-c1c4": {
        "instruments": ["NSE:EQUITY:RELIANCE"],
        "symbols": ["NSE:RELIANCE"],
        "unresolved": [],
        "updated_at": "..."}}}

``instruments`` (stable canonical ids) is the authority; ``symbols`` is the
derived user-facing projection. Phase-1 v1 files (``{"symbols": [...]}``)
migrate transparently on load: each entry is normalized, resolved through
the single canonical path, and unresolvable entries are RECORDED in
``unresolved`` — never silently deleted, never replaced with guesses.

Atomic write discipline (temp file + fsync + os.replace) mirrors the broker
selection store; a missing or corrupt file loads as empty (the UI then shows
the empty state, never a global fallback).

Deliberately NOT here (future phases): provider/broker routing and
mappings, dynamic universe rules, universe versioning, data failover.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any

from strategy.instrument_registry import get_instrument_registry
from strategy.models.instrument import INACTIVE, NOT_FOUND
from strategy.models.universe import StrategyUniverse, validate_symbols

_STORE_KIND = "vayren.strategy_universes"
_STORE_VERSION = 2

#: Refusal vocabulary (spec §14 — these exact strings reach the UI).
NOT_FOUND_MESSAGE = "Instrument not found in NSE Equity registry."
INACTIVE_MESSAGE = "Instrument is inactive."


def universe_strategy_id(strategy_name: object) -> str:
    """Stable universe owner key for a configured strategy name.

    Registered strategies resolve to their definition id (stable across
    display-name edits); anything else keeps its own stripped name. Empty
    names resolve to "" so callers can gate "no strategy selected" first.
    """
    name = str(strategy_name or "").strip()
    if not name:
        return ""
    try:
        from strategy.registry import get_strategy_registry

        reg = get_strategy_registry()
        if reg.contains(name):
            resolved = str(reg.get(name).id or name).strip()
            return resolved or name
    except Exception:
        pass
    return name


def _resolve_displays(
    symbols: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Resolve display symbols through the single canonical path.

    Returns (instrument_ids, displays, unresolved) where unresolved holds
    (symbol, status) pairs for entries that must be reported, never
    guessed or silently dropped.
    """
    registry = get_instrument_registry()
    ids: list[str] = []
    displays: list[str] = []
    unresolved: list[tuple[str, str]] = []
    for symbol in symbols:
        result = registry.resolve(symbol)
        if result.status == "RESOLVED" and result.instrument is not None:
            ids.append(result.instrument.instrument_id)
            displays.append(result.instrument.display)
        else:
            unresolved.append((symbol, result.status))
    return tuple(ids), tuple(displays), tuple(unresolved)


def _resolve_ids(
    instrument_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Resolve stored canonical ids back to displays (v2 load path).

    Saved configuration is preserved as-is: records that no longer resolve
    are reported in ``unresolved`` (never silently deleted); inactive
    records stay configured (deactivation gates NEW configuration, and
    execution behavior is out of scope).
    """
    registry = get_instrument_registry()
    displays: list[str] = []
    unresolved: list[tuple[str, str]] = []
    for instrument_id in instrument_ids:
        record = registry.get(instrument_id)
        if record is None:
            unresolved.append((instrument_id, NOT_FOUND))
        else:
            displays.append(record.display)
    return tuple(displays), tuple(unresolved)


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class UniverseStoreError(RuntimeError):
    """Refused universe write (no owner, or non-NSE symbols)."""


class StrategyUniverseStore:
    """Owns the strategy_id -> StrategyUniverse map on disk."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> dict[str, StrategyUniverse]:
        """All saved universes; corrupt/missing files read as empty."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            logging.getLogger(__name__).warning(
                "strategy universe file %s is corrupt; starting empty", self._path
            )
            return {}
        if not isinstance(data, dict):
            return {}
        if data.get("kind", _STORE_KIND) != _STORE_KIND:
            logging.getLogger(__name__).warning(
                "strategy universe file %s has unknown kind; starting empty", self._path
            )
            return {}
        stored = data.get("universes", {})
        if not isinstance(stored, dict):
            return {}
        universes: dict[str, StrategyUniverse] = {}
        for strategy_id, entry in stored.items():
            if not isinstance(strategy_id, str) or not strategy_id.strip():
                continue
            if not isinstance(entry, dict):
                continue
            updated_at = str(entry.get("updated_at", ""))
            raw_instruments = entry.get("instruments", None)
            if isinstance(raw_instruments, list):
                # Schema v2: canonical ids are the authority.
                ids = tuple(str(i) for i in raw_instruments)
                displays, unresolved = _resolve_ids(ids)
            else:
                # Schema v1 (Phase 1): migrate display symbols transparently.
                raw_symbols = entry.get("symbols", ())
                if not isinstance(raw_symbols, list):
                    continue
                accepted, _rejected = validate_symbols(tuple(str(s) for s in raw_symbols))
                ids, displays, unresolved = _resolve_displays(accepted)
            try:
                universes[strategy_id] = StrategyUniverse(
                    strategy_id=strategy_id,
                    symbols=displays,
                    instrument_ids=ids,
                    unresolved=unresolved,
                    updated_at=updated_at,
                )
            except ValueError:
                continue
        return universes

    def get(self, strategy_id: str) -> StrategyUniverse | None:
        """The saved universe for one strategy, or None when never saved."""
        if not strategy_id:
            return None
        return self.load_all().get(strategy_id)

    def symbols_for(self, strategy_name: object) -> tuple[str, ...]:
        """Saved symbols for a configured strategy name (empty when none)."""
        universe = self.get(universe_strategy_id(strategy_name))
        return universe.symbols if universe is not None else ()

    def instrument_ids_for(self, strategy_name: object) -> tuple[str, ...]:
        """Authoritative internal ids for a strategy (empty when none)."""
        universe = self.get(universe_strategy_id(strategy_name))
        return universe.instrument_ids if universe is not None else ()

    def migration_report(self) -> dict[str, dict[str, Any]]:
        """Explicit per-strategy migration outcome.

        ``{strategy_id: {"resolved": <n>, "unresolved": [(symbol, reason)]}}``
        — unresolved entries are named with their resolver status so the
        operator sees exactly what did not carry over and why.
        """
        report: dict[str, dict[str, Any]] = {}
        for strategy_id, universe in sorted(self.load_all().items()):
            report[strategy_id] = {
                "resolved": len(universe.symbols),
                "unresolved": list(universe.unresolved),
            }
        return report

    def save(self, strategy_name: object, symbols: tuple[str, ...] | list[str]) -> StrategyUniverse:
        """Replace one strategy's universe (validated, resolved, atomic).

        Raises UniverseStoreError when there is no owning strategy, when any
        symbol is not a Phase-1 NSE symbol, when a symbol is unknown to the
        NSE Equity registry, or when it is inactive. Never touches — and
        never merges with — any other strategy's entry.
        """
        strategy_id = universe_strategy_id(strategy_name)
        if not strategy_id:
            raise UniverseStoreError("select a strategy before configuring its universe")
        accepted, rejected = validate_symbols(tuple(symbols))
        if rejected:
            raise UniverseStoreError(
                f"only NSE symbols allowed in Phase 1; rejected: {', '.join(rejected)}"
            )
        ids, displays, unresolved = _resolve_displays(accepted)
        not_found = [symbol for symbol, status in unresolved if status == NOT_FOUND]
        if not_found:
            raise UniverseStoreError(f"{NOT_FOUND_MESSAGE} {', '.join(not_found)}")
        inactive = [symbol for symbol, status in unresolved if status == INACTIVE]
        if inactive:
            raise UniverseStoreError(f"{INACTIVE_MESSAGE} {', '.join(inactive)}")
        if unresolved:
            names = ", ".join(f"{symbol} ({status})" for symbol, status in unresolved)
            raise UniverseStoreError(f"cannot configure unresolved instruments: {names}")
        universe = StrategyUniverse(
            strategy_id=strategy_id,
            symbols=displays,
            instrument_ids=ids,
            updated_at=_utcnow_iso(),
        )
        stored = self.load_all()
        stored[strategy_id] = universe
        self._write(stored)
        return universe

    def _write(self, universes: dict[str, StrategyUniverse]) -> None:
        payload: dict[str, Any] = {
            "kind": _STORE_KIND,
            "version": _STORE_VERSION,
            "universes": {
                strategy_id: {
                    "instruments": list(universe.instrument_ids),
                    "symbols": list(universe.symbols),
                    "unresolved": [list(pair) for pair in universe.unresolved],
                    "updated_at": universe.updated_at,
                }
                for strategy_id, universe in sorted(universes.items())
            },
        }
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


__all__ = [
    "StrategyUniverseStore",
    "UniverseStoreError",
    "universe_strategy_id",
    "NOT_FOUND_MESSAGE",
    "INACTIVE_MESSAGE",
]
