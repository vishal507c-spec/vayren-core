"""Strategy Lab per-strategy NSE universe — the backend side of the selector.

Thin orchestration over the authoritative pieces; it owns no data of its own:

- persistence: :class:`strategy.universe_store.StrategyUniverseStore`
  (``live/strategy_universes.json``), the single source of saved universes.
- identity: the canonical registry via the store's resolve path.
- catalog: :mod:`strategy.instrument_master` (verified NSE equity master) for
  search, with an explicit source/staleness/error state.

Saving a universe writes ONLY that strategy's entry in the universe store.
Saving strategy code is a separate command writing a ``.py`` file, so the two
can never overwrite each other. Nothing here starts, stops or places trades.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from strategy.instrument_master import (
    CatalogSnapshot,
    catalog_path,
    load_catalog,
    refresh_catalog,
)
from strategy.instrument_registry import NSE_EQUITY_SEED, get_instrument_registry
from strategy.universe_store import (
    StrategyUniverseStore,
    UniverseStoreError,
    universe_strategy_id,
)

logger = logging.getLogger(__name__)

#: Strategy Lab save states reported back to the UI.
SAVE_SAVED = "saved"
SAVE_REFUSED = "refused"


class LabUniverseService:
    """Per-strategy universe read/write and catalog state for the Lab."""

    def __init__(self, data_dir: Path | str) -> None:
        self._data_dir = Path(data_dir)
        self._store = StrategyUniverseStore(self._data_dir / "live" / "strategy_universes.json")
        self._catalog_path = catalog_path(self._data_dir)

    # ── catalog ────────────────────────────────────────────────────────

    def catalog(self) -> CatalogSnapshot:
        snap = load_catalog(self._catalog_path, seed=NSE_EQUITY_SEED)
        get_instrument_registry().load_symbols(snap.symbols)
        return snap

    def refresh(self) -> CatalogSnapshot:
        snap = refresh_catalog(self._catalog_path)
        get_instrument_registry().load_symbols(snap.symbols)
        return snap

    def catalog_summary(self) -> dict[str, Any]:
        catalog = self.catalog()
        return {
            "source": catalog.source,
            "count": len(catalog.symbols),
            "fetched_at": catalog.fetched_at,
            "stale": catalog.stale,
            "authoritative": catalog.is_authoritative,
            "error": catalog.error,
            "notes": list(catalog.notes),
        }

    def refresh_summary(self) -> dict[str, Any]:
        refreshed = self.refresh()
        return {"ok": not refreshed.error, **self.catalog_summary(), "error": refreshed.error}

    # ── per-strategy universe ─────────────────────────────────────────

    def saved_symbols(self, strategy_name: str) -> tuple[str, ...]:
        """Saved NSE display symbols for exactly this strategy (never others)."""
        return self._store.symbols_for(strategy_name)

    def saved_universe(self, strategy_name: str):
        """The stored universe record for this strategy, or None when never saved."""
        return self._store.get(universe_strategy_id(strategy_name))

    def save(self, strategy_name: str, symbols: list[str]) -> dict[str, Any]:
        """Replace one strategy's universe. Returns a state dict for the UI.

        Refusals (unknown/inactive/non-NSE symbols, no strategy) leave the
        previously saved universe untouched and are reported, never raised.
        """
        self.catalog()
        canonical = [s if ":" in str(s) else f"NSE:{str(s).strip().upper()}" for s in symbols]
        try:
            saved = self._store.save(strategy_name, tuple(canonical))
        except UniverseStoreError as exc:
            logger.info("Lab universe save refused for %r: %s", strategy_name, exc)
            return self._result(strategy_name, SAVE_REFUSED, error=str(exc))
        except OSError as exc:
            logger.error("Lab universe persistence failed for %r: %s", strategy_name, exc)
            return self._result(
                strategy_name, SAVE_REFUSED, error=f"could not write universe: {exc}"
            )
        return self._result(strategy_name, SAVE_SAVED, symbols=saved.symbols)

    def view(self, strategy_name: str) -> dict[str, Any]:
        """Snapshot block for one strategy: saved symbols + catalog state."""
        return self._result(strategy_name, "", symbols=self.saved_symbols(strategy_name))

    def _result(
        self,
        strategy_name: str,
        state: str,
        *,
        symbols: tuple[str, ...] = (),
        error: str = "",
    ) -> dict[str, Any]:
        catalog = self.catalog()
        return {
            "strategy_id": universe_strategy_id(strategy_name),
            "state": state,
            "symbols": list(symbols),
            "count": len(symbols),
            "error": error,
            "catalog": {
                "source": catalog.source,
                "count": len(catalog.symbols),
                "fetched_at": catalog.fetched_at,
                "stale": catalog.stale,
                "authoritative": catalog.is_authoritative,
                "error": catalog.error,
                "notes": list(catalog.notes),
            },
        }

    def search(self, query: str, limit: int = 50) -> list[str]:
        """Case-insensitive prefix-then-substring search over the catalog."""
        catalog = self.catalog()
        needle = str(query or "").strip().upper()
        if not needle:
            return list(catalog.symbols[:limit])
        prefix = [s for s in catalog.symbols if s.startswith(needle)]
        contains = [s for s in catalog.symbols if needle in s and not s.startswith(needle)]
        return (prefix + contains)[:limit]


__all__ = ["LabUniverseService", "SAVE_REFUSED", "SAVE_SAVED"]
