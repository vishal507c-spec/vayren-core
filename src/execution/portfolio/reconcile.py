"""Reconciliation — local ledger vs broker truth, with blocking rule.

FINAL §L: positions, orders AND funds reconcile into one
:class:`ReconciliationVerdict` with status SAFE / WARNING / BLOCKED.
SAFE journals info; WARNING and BLOCKED both block LIVE. Startup must
reconcile (RECOVER → RECONCILE → …) before LIVE; every verdict is
journaled. Ledger and strategy execution state rebuild from fills, so
positions/fills coverage flows through the same reports.

Every comparison rule is owned by Rust (`rust/vayren-core`, the
`execution_engine` module): the quantity tolerance, the sorted symbol union,
the parse-or-zero broker quantity, the ``local-only``/``broker-only``
labelling, the unknown-equity mismatch, the SAFE/WARNING/BLOCKED combination
and both blocking rules. What is left here selects payload, rebuilds the
kernel's mismatch records as value objects and renders the reason text — the
message wording stays in the bridge, the decision does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from execution.models.position import Position
from execution.native_execution import (
    native_reconcile_funds,
    native_reconcile_orders,
    native_reconcile_positions,
    native_report_blocks_live,
    native_verdict_blocks_live,
    native_verdict_status,
)

_NOT_EVALUATED = "reconciliation not evaluated"


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
        return native_report_blocks_live(self.matched)


def _report_of(records: list[tuple[str, str, str, str]], checked_at: str) -> ReconciliationReport:
    """Rebuild one kernel report — an empty mismatch list IS its matched flag."""
    mismatches = tuple(
        ReconciliationMismatch(kind=kind, symbol_or_id=ident, local=local, broker=broker)
        for kind, ident, local, broker in records
    )
    return ReconciliationReport(
        matched=not mismatches, mismatches=mismatches, checked_at=checked_at
    )


def reconcile_positions(
    local: tuple[Position, ...],
    broker_positions: list[dict],
    tolerance: float = 1e-9,
    checked_at: str = "",
) -> ReconciliationReport:
    """Compare local ledger against broker snapshot. Pure function."""
    records = native_reconcile_positions(
        [(position.symbol, position.quantity) for position in local],
        [
            (str(payload.get("symbol", "")), payload.get("quantity", ""))
            for payload in broker_positions
        ],
        tolerance,
    )
    return _report_of(records, checked_at)


def reconcile_orders(
    local_open_ids: tuple[str, ...],
    broker_open_ids: tuple[str, ...],
    checked_at: str = "",
) -> ReconciliationReport:
    """Compare open-order id sets. Pure function."""
    records = native_reconcile_orders(local_open_ids, broker_open_ids)
    return _report_of(records, checked_at)


def reconcile_funds(
    local_equity: float,
    broker_funds: dict[str, Any],
    tolerance: float = 1e-9,
    checked_at: str = "",
) -> ReconciliationReport:
    """Compare ledger equity against the broker funds snapshot (FINAL §L).

    The payload value travels as text: the kernel decides that a missing or
    unparseable equity is UNKNOWN (never treated as matching) and applies the
    tolerance compare. Pure function.
    """
    records = native_reconcile_funds(local_equity, broker_funds.get("equity"), tolerance)
    return _report_of(records, checked_at)


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
    """Combine reports into the kernel's SAFE/WARNING/BLOCKED verdict.

    Rendering only: the status comes from ``native_verdict_status`` given the
    evaluation flag and the total mismatch count, never from a Python branch.
    Pure function, never raises.
    """
    if not evaluated:
        status = ReconcileStatus(native_verdict_status(False, 0))
        reasons: tuple[str, ...] = (_NOT_EVALUATED,)
    else:
        reasons = tuple(
            f"{mismatch.kind} {mismatch.symbol_or_id}: "
            f"local={mismatch.local} broker={mismatch.broker}"
            for report in reports
            for mismatch in report.mismatches
        )
        status = ReconcileStatus(native_verdict_status(True, len(reasons)))
    return ReconciliationVerdict(
        status=status, reports=reports, checked_at=checked_at, reasons=reasons
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
        return native_verdict_blocks_live(self.verdict().status.value)

    def verdict(self, *, evaluated: bool = True) -> ReconciliationVerdict:
        """Combined SAFE/WARNING/BLOCKED verdict (FINAL §L)."""
        return verdict_of((self.positions, self.orders, self.funds), evaluated=evaluated)
