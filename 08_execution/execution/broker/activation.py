"""LIVE activation ceremony — the FINAL ordered gate review (FINAL §T).

Twelve explicit steps, evaluated purely and journaled. This module never
arms, never starts order flow, never touches the network: it VERIFIES.
Any failure → STOP, fail closed, no order. LIVE additionally requires the
operator's explicit ARM (a passed ceremony alone authorizes nothing).

Ownership: the ceremony — step order, names, verdicts, detail wording and
blockers — is Rust (``rust/vayren-core/src/live_readiness.rs``). This module
keeps the immutable report types, converts already-gathered verdicts into
kernel facts, and journals the result (migration §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from execution.broker.gates import LiveGatesReport
from execution.broker.native_policy import native_activation_verdict
from execution.modes import LiveArm
from execution.portfolio.reconcile import ReconciliationVerdict


@dataclass(frozen=True)
class ActivationStep:
    """One ordered ceremony step: verdict + human-readable detail."""

    index: int
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class ActivationReport:
    """The full ceremony verdict. ``ready`` means the eleven verifiable
    steps are green (step 11 reviews an already-explicit ARM — arming is
    never performed here). Step 12 START is never auto-ok: going live
    remains an explicit operator act after a green ceremony."""

    ready: bool
    steps: tuple[ActivationStep, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)


def evaluate_activation(
    *,
    broker_name: str = "",
    environment: str = "",
    credentials_ok: bool = False,
    credential_reasons: tuple[str, ...] = (),
    account_ok: bool = False,
    account_reasons: tuple[str, ...] = (),
    market_healthy: bool = False,
    market_reason: str = "",
    funds_ok: bool = False,
    funds_reasons: tuple[str, ...] = (),
    risk_ok: bool = False,
    risk_reasons: tuple[str, ...] = (),
    verdict: ReconciliationVerdict | None = None,
    kill_halted: bool = True,
    gates: LiveGatesReport | None = None,
    armed: LiveArm = LiveArm.DISARMED,
) -> ActivationReport:
    """Evaluate the twelve activation steps in order. Pure, fail-closed.

    The ceremony itself — step names, order, verdicts, detail wording and
    blocker composition — belongs to Rust
    (``live_readiness::evaluate_ceremony``). This converts the gathered
    verdicts into kernel facts and materialises the frozen report.
    """
    verdict_blocks = False
    verdict_reasons: tuple[str, ...] = ()
    if verdict is not None:
        verdict_blocks = bool(verdict.blocks_live)
        verdict_reasons = tuple(verdict.reasons)
    failed_gates = gates.failed() if gates is not None else ()
    ready, steps, blockers = native_activation_verdict(
        broker_name=broker_name,
        environment=environment,
        credentials_ok=credentials_ok,
        credential_reasons=tuple(credential_reasons),
        account_ok=account_ok,
        account_reasons=tuple(account_reasons),
        market_healthy=market_healthy,
        market_reason=market_reason,
        funds_ok=funds_ok,
        funds_reasons=tuple(funds_reasons),
        risk_ok=risk_ok,
        risk_reasons=tuple(risk_reasons),
        verdict_present=verdict is not None,
        verdict_blocks=verdict_blocks,
        verdict_reasons=verdict_reasons,
        kill_halted=kill_halted,
        gates_present=gates is not None,
        gates_ready=bool(gates is not None and gates.ready),
        failed_gate_names=tuple(gate.name for gate in failed_gates),
        failed_gate_details=tuple(gate.detail for gate in failed_gates),
        armed_value=armed.value,
    )
    return ActivationReport(
        ready=ready,
        steps=tuple(
            ActivationStep(index=index, name=name, ok=ok, detail=detail)
            for index, name, ok, detail in steps
        ),
        blockers=blockers,
    )


def record_activation(journal: Any, report: ActivationReport) -> None:
    """Journal one ACTIVATION_EVALUATED entry (step verdicts, no secrets)."""
    journal.record(
        "ACTIVATION_EVALUATED",
        ready=report.ready,
        steps=[
            {"index": step.index, "name": step.name, "ok": step.ok, "detail": step.detail}
            for step in report.steps
        ],
        blockers=list(report.blockers),
    )


__all__ = [
    "ActivationReport",
    "ActivationStep",
    "evaluate_activation",
    "record_activation",
]
