"""LIVE activation ceremony — the FINAL ordered gate review (FINAL §T).

Twelve explicit steps, evaluated purely and journaled. This module never
arms, never starts order flow, never touches the network: it VERIFIES.
Any failure → STOP, fail closed, no order. LIVE additionally requires the
operator's explicit ARM (a passed ceremony alone authorizes nothing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from execution.broker.gates import LiveGatesReport
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


def _step(index: int, name: str, ok: bool, detail: str = "") -> ActivationStep:
    return ActivationStep(index=index, name=name, ok=bool(ok), detail=detail)


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
    """Evaluate the twelve activation steps in order. Pure, fail-closed."""
    steps = (
        _step(1, "SELECT_BROKER", bool(broker_name), "" if broker_name else "no broker selected"),
        _step(
            2,
            "LIVE_ENVIRONMENT",
            environment == "live",
            "" if environment == "live" else f"environment is {environment!r}, not 'live'",
        ),
        _step(
            3,
            "VALIDATE_CREDENTIALS",
            credentials_ok,
            "" if credentials_ok else "; ".join(credential_reasons) or "credentials invalid",
        ),
        _step(
            4,
            "CONFIRM_ACCOUNT",
            account_ok,
            "" if account_ok else "; ".join(account_reasons) or "account unconfirmed",
        ),
        _step(
            5,
            "VERIFY_MARKET_DATA",
            market_healthy,
            "" if market_healthy else market_reason or "market data unhealthy",
        ),
        _step(
            6,
            "VERIFY_FUNDS",
            funds_ok,
            "" if funds_ok else "; ".join(funds_reasons) or "funds invalid",
        ),
        _step(
            7,
            "VALIDATE_RISK",
            risk_ok,
            "" if risk_ok else "; ".join(risk_reasons) or "risk invalid",
        ),
        _step(
            8,
            "RECONCILE",
            verdict is not None and not verdict.blocks_live,
            "reconciliation not evaluated"
            if verdict is None
            else (
                ""
                if not verdict.blocks_live
                else "; ".join(verdict.reasons) or "reconciliation blocks live"
            ),
        ),
        _step(
            9,
            "EVALUATE_GATES",
            gates is not None and gates.ready,
            "gates not evaluated"
            if gates is None
            else (
                ""
                if gates.ready
                else "; ".join(f"{g.name}: {g.detail}" for g in gates.failed()) or "gates failing"
            ),
        ),
        _step(
            10, "VERIFY_KILL_SWITCH", not kill_halted, "kill switch engaged" if kill_halted else ""
        ),
        _step(
            11,
            "EXPLICIT_ARM",
            armed == LiveArm.ARMED,
            "" if armed == LiveArm.ARMED else f"arming is {armed.value}, not ARMED",
        ),
        _step(0, "START_LIVE", False, "ceremony verifies only — start is an operator act"),
    )
    start, ordered = steps[-1], steps[:-1]
    verifiable_ready = all(step.ok for step in ordered)
    final_start = ActivationStep(
        index=12,
        name=start.name,
        ok=False,
        detail=(
            "ceremony green — start remains an explicit operator act"
            if verifiable_ready
            else "blocked: ceremony not fully green"
        ),
    )
    blockers = tuple(
        f"{step.name}: {step.detail}" for step in ordered if not step.ok and step.detail
    )
    return ActivationReport(
        ready=verifiable_ready, steps=(*ordered, final_start), blockers=blockers
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
