"""Reconciliation — local ledger vs broker truth, with blocking rule.

FINAL §L: positions, orders AND funds reconcile into one
:class:`ReconciliationVerdict` with status SAFE / WARNING / BLOCKED.
SAFE journals info; WARNING and BLOCKED both block LIVE. Startup must
reconcile (RECOVER → RECONCILE → …) before LIVE; every verdict is
journaled. Ledger and strategy execution state rebuild from fills, so
positions/fills coverage flows through the same reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from execution.models.position import Position
from execution.native_execution import native_reconcile_funds, native_verdict_blocks_live


class ReconcileStatus(StrEnum):
    """Verdict status. WARNING and BLOCKED both block LIVE."""

    SAFE = "SAFE"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ReconciliationMismatch:
    kind: str  # position | order | funds
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


def _money_of(payload: dict, key: str) -> float | None:
    try:
        value = payload.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


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


def reconcile_funds(
    local_equity: float,
    broker_funds: dict[str, Any],
    tolerance: float = 1e-9,
    checked_at: str = "",
) -> ReconciliationReport:
    """Compare ledger equity against the broker funds snapshot (FINAL §L).

    Missing/unparseable broker equity is a mismatch (UNKNOWN ≠ zero —
    never treated as matching). Pure function.
    """
    broker_equity = _money_of(broker_funds, "equity")
    if broker_equity is None:
        return ReconciliationReport(
            matched=False,
            mismatches=(
                ReconciliationMismatch(
                    kind="funds",
                    symbol_or_id="equity",
                    local=f"{local_equity}",
                    broker="unknown",
                ),
            ),
            checked_at=checked_at,
        )
    if not native_reconcile_funds(float(local_equity), True, broker_equity, float(tolerance)):
        return ReconciliationReport(
            matched=False,
            mismatches=(
                ReconciliationMismatch(
                    kind="funds",
                    symbol_or_id="equity",
                    local=f"{local_equity}",
                    broker=f"{broker_equity}",
                ),
            ),
            checked_at=checked_at,
        )
    return ReconciliationReport(matched=True, checked_at=checked_at)


@dataclass(frozen=True)
class ReconciliationVerdict:
    """One combined verdict across positions/orders/funds (FINAL §L)."""

    status: ReconcileStatus
    reports: tuple[ReconciliationReport, ...] = ()
    checked_at: str = ""
    reasons: tuple[str, ...] = ()

    @property
    def blocks_live(self) -> bool:
        """SAFE never blocks; WARNING and BLOCKED always block LIVE."""
        return native_verdict_blocks_live(self.status.value)

    def journal_payload(self) -> dict[str, Any]:
        """JSON-safe payload for the RECONCILED journal entry."""
        return {
            "status": self.status.value,
            "checked_at": self.checked_at,
            "reasons": list(self.reasons),
            "mismatches": [
                {
                    "kind": mismatch.kind,
                    "id": mismatch.symbol_or_id,
                    "local": mismatch.local,
                    "broker": mismatch.broker,
                }
                for report in self.reports
                for mismatch in report.mismatches
            ],
        }


def verdict_of(
    reports: tuple[ReconciliationReport, ...],
    *,
    evaluated: bool = True,
    checked_at: str = "",
) -> ReconciliationVerdict:
    """Combine reports: unevaluated → WARNING; any mismatch → BLOCKED;
    all matched → SAFE. Pure function, never raises."""
    if not evaluated:
        return ReconciliationVerdict(
            status=ReconcileStatus.WARNING,
            reports=reports,
            checked_at=checked_at,
            reasons=("reconciliation not evaluated",),
        )
    reasons: list[str] = []
    for report in reports:
        for mismatch in report.mismatches:
            reasons.append(
                f"{mismatch.kind} {mismatch.symbol_or_id}: "
                f"local={mismatch.local} broker={mismatch.broker}"
            )
    if reasons:
        return ReconciliationVerdict(
            status=ReconcileStatus.BLOCKED,
            reports=reports,
            checked_at=checked_at,
            reasons=tuple(reasons),
        )
    return ReconciliationVerdict(
        status=ReconcileStatus.SAFE, reports=reports, checked_at=checked_at
    )


def record_verdict(journal: Any, verdict: ReconciliationVerdict) -> None:
    """Journal one RECONCILED entry (FINAL §L: every verdict is journaled)."""
    journal.record("RECONCILED", **verdict.journal_payload())


@dataclass
class ReconciliationState:
    """Latest reports; the session consults blocks_live before trading."""

    positions: ReconciliationReport = field(
        default_factory=lambda: ReconciliationReport(matched=True)
    )
    orders: ReconciliationReport = field(default_factory=lambda: ReconciliationReport(matched=True))
    funds: ReconciliationReport = field(default_factory=lambda: ReconciliationReport(matched=True))

    @property
    def blocks_live(self) -> bool:
        return self.positions.blocks_live or self.orders.blocks_live or self.funds.blocks_live

    def verdict(self, *, evaluated: bool = True) -> ReconciliationVerdict:
        """Combined SAFE/WARNING/BLOCKED verdict (FINAL §L)."""
        return verdict_of((self.positions, self.orders, self.funds), evaluated=evaluated)
