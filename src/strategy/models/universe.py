"""StrategyUniverse — per-strategy symbol ownership (Phase 1 + Phase 2).

A universe belongs to exactly one strategy and never leaks into another:
``Strategy(strategy_id) -> StrategyUniverse(symbols) -> Configured Symbols``.
Only NSE symbols are accepted (``NSE:RELIANCE``); anything else is
rejected, never normalized into something tradeable.

Phase 2: ``symbols`` stays the canonical user-facing display projection
(``NSE:*``), while ``instrument_ids`` carries the authoritative internal
identity (stable ``NSE:EQUITY:*`` ids resolved through the canonical
instrument registry). ``unresolved`` records migration leftovers
explicitly — entries are reported, never silently deleted.

Deliberately NOT here (future phases): provider/broker routing, dynamic
universe rules, universe versioning, data failover.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NSE_SYMBOL_RE = re.compile(r"^NSE:[A-Z0-9][A-Z0-9._-]*$")


def normalize_symbol(raw: object) -> str | None:
    """Normalize one user-facing symbol, or None when Phase 1 rejects it.

    Accepts ``NSE:RELIANCE`` (any case/whitespace) and returns the canonical
    uppercase form. Everything else — other exchanges, bare names without
    the ``NSE:`` prefix, empties — returns None so the caller refuses it
    instead of trading something the operator did not name exactly.
    """
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().upper()
    if not cleaned:
        return None
    if _NSE_SYMBOL_RE.match(cleaned):
        return cleaned
    return None


def validate_symbols(
    symbols: tuple[str, ...] | list[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split symbols into (accepted, rejected), order-preserving, de-duplicated.

    Accepted symbols are canonical ``NSE:*`` forms. Rejected entries keep
    their original text so refusal messages name the exact offender.
    """
    accepted: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        clean = normalize_symbol(raw)
        if clean is None:
            rejected.append(str(raw))
        elif clean not in seen:
            seen.add(clean)
            accepted.append(clean)
    return tuple(accepted), tuple(rejected)


@dataclass(frozen=True)
class StrategyUniverse:
    """The saved symbol set owned by one strategy.

    Attributes:
        strategy_id: Stable owner key (registry definition id when the
            strategy is registered, otherwise the configured strategy name).
        symbols: Canonical ``NSE:*`` display symbols, order-preserved,
            unique — the user-facing projection of ``instrument_ids``.
        instrument_ids: Authoritative internal identity (stable
            ``NSE:EQUITY:*`` ids from the canonical instrument registry).
            Empty on pre-Phase-2 objects built by ``with_symbols``.
        unresolved: Migration leftovers as (symbol, reason) pairs —
            reported explicitly, never silently deleted.
        updated_at: ISO-8601 UTC timestamp of the last save.
    """

    strategy_id: str
    symbols: tuple[str, ...] = ()
    instrument_ids: tuple[str, ...] = ()
    unresolved: tuple[tuple[str, str], ...] = ()
    updated_at: str = ""

    def __post_init__(self) -> None:
        """Reject empty owner keys and non-NSE symbols."""
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("StrategyUniverse strategy_id must be a non-empty string")
        for symbol in self.symbols:
            if normalize_symbol(symbol) != symbol:
                raise ValueError(f"StrategyUniverse rejects non-NSE symbol: {symbol!r}")

    @property
    def symbol_count(self) -> int:
        """Configured symbols for this strategy."""
        return len(self.symbols)

    @property
    def is_empty(self) -> bool:
        """True when the strategy has no configured symbols yet."""
        return not self.symbols

    def with_symbols(
        self, symbols: tuple[str, ...] | list[str], updated_at: str = ""
    ) -> StrategyUniverse:
        """Return a copy carrying exactly ``symbols`` (already validated).

        Pre-Phase-2 compatibility shape: display symbols without resolved
        identities. The store always builds fully-resolved universes; this
        stays for callers that only carry display strings.
        """
        return StrategyUniverse(
            strategy_id=self.strategy_id,
            symbols=tuple(symbols),
            updated_at=updated_at or self.updated_at,
        )

    def with_instruments(
        self,
        symbols: tuple[str, ...],
        instrument_ids: tuple[str, ...],
        unresolved: tuple[tuple[str, str], ...] = (),
        updated_at: str = "",
    ) -> StrategyUniverse:
        """Return a fully-resolved copy (the store's canonical shape)."""
        if len(symbols) != len(instrument_ids):
            raise ValueError("symbols and instrument_ids must align one-to-one")
        return StrategyUniverse(
            strategy_id=self.strategy_id,
            symbols=tuple(symbols),
            instrument_ids=tuple(instrument_ids),
            unresolved=tuple(unresolved),
            updated_at=updated_at or self.updated_at,
        )


__all__ = ["StrategyUniverse", "normalize_symbol", "validate_symbols"]
