"""Reconciliation — local ledger vs broker truth, with blocking rule."""

from __future__ import annotations

from dataclasses import dataclass, field

from execution.models.position import Position


@dataclass(frozen=True)
class ReconciliationMismatch:
    kind: str  # position | order
    symbol_or_id: str
    local: str
    broker: str


@dataclass(frozen=True)
class ReconciliationReport:
    matched: bool
    mismatches: tuple[ReconciliationMismatch, ...] = ()
    checked_at: str = ""

    @property
    def blocks_live(self) -> bool:
        """Unresolved mismatch blocks live execution. Paper only reports."""
        return not self.matched


def _qty_of(position: dict) -> float:
    try:
        return float(position.get("quantity", 0.0))
    except Exception:
        return 0.0


def reconcile_positions(
    local: tuple[Position, ...],
    broker_positions: list[dict],
    tolerance: float = 1e-9,
    checked_at: str = "",
) -> ReconciliationReport:
    """Compare local ledger against broker snapshot. Pure function."""
    mismatches: list[ReconciliationMismatch] = []
    broker_qty = {str(p.get("symbol", "")): _qty_of(p) for p in broker_positions}
    local_qty = {p.symbol: p.quantity for p in local}
    for symbol in sorted(set(local_qty) | set(broker_qty)):
        mine = local_qty.get(symbol, 0.0)
        theirs = broker_qty.get(symbol, 0.0)
        if abs(mine - theirs) > tolerance:
            mismatches.append(
                ReconciliationMismatch(
                    kind="position",
                    symbol_or_id=symbol,
                    local=f"{mine}",
                    broker=f"{theirs}",
                )
            )
    return ReconciliationReport(
        matched=not mismatches, mismatches=tuple(mismatches), checked_at=checked_at
    )


def reconcile_orders(
    local_open_ids: tuple[str, ...],
    broker_open_ids: tuple[str, ...],
    checked_at: str = "",
) -> ReconciliationReport:
    """Compare open-order id sets. Pure function."""
    mismatches: list[ReconciliationMismatch] = []
    for cid in sorted(set(local_open_ids) ^ set(broker_open_ids)):
        side = "local-only" if cid in local_open_ids else "broker-only"
        mismatches.append(
            ReconciliationMismatch(kind="order", symbol_or_id=cid, local=side, broker=side)
        )
    return ReconciliationReport(
        matched=not mismatches, mismatches=tuple(mismatches), checked_at=checked_at
    )


@dataclass
class ReconciliationState:
    """Latest reports; the session consults blocks_live before trading."""

    positions: ReconciliationReport = field(
        default_factory=lambda: ReconciliationReport(matched=True)
    )
    orders: ReconciliationReport = field(default_factory=lambda: ReconciliationReport(matched=True))

    @property
    def blocks_live(self) -> bool:
        return self.positions.blocks_live or self.orders.blocks_live
