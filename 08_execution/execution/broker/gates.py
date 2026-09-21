"""Live readiness gates — five explicit checks, fail-closed with reasons.

Gate vocabulary (mission §4), computed from existing safety primitives —
no duplicated gate logic:
1. BROKER_ADAPTER_READY — an adapter resolves for the requested venue and
   reports healthy (reuses factory registry + adapter.health()).
2. CREDENTIALS_READY — identity present and secrets resolvable when the
   environment requires them (reuses credentials validation).
3. ACCOUNT_CONFIRMED — adapter-reported identity matches the expected
   account/environment; a sandbox account is never a live account.
4. RISK_CONFIGURATION_VALID — the active RiskPolicy is sane.
5. EXECUTION_SAFETY_ENABLED — kill switch disengaged (reuses KillSwitch).

Ownership: every verdict, reason string, join, fallback and the gate order
themselves live in Rust (``rust/vayren-core/src/live_readiness.rs``). What
stays here is the report types, the re-exported gate names, presentation and
the IO a kernel cannot perform — asking an adapter, a store and the risk
policy what their current facts are (constitution §1, migration §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from broker.funds import FundsSnapshot, require_funds
from risk import RiskPolicy

from execution.broker.adapter import BrokerAdapter
from execution.broker.credentials import (
    BrokerCredentials,
    CredentialStore,
    validate_credentials,
)
from execution.broker.native_policy import (
    native_account_reasons,
    native_funds_reasons,
    native_gate_verdict,
    native_risk_reasons,
)

BROKER_ADAPTER_READY = "BROKER_ADAPTER_READY"
CREDENTIALS_READY = "CREDENTIALS_READY"
ACCOUNT_CONFIRMED = "ACCOUNT_CONFIRMED"
RISK_CONFIGURATION_VALID = "RISK_CONFIGURATION_VALID"
EXECUTION_SAFETY_ENABLED = "EXECUTION_SAFETY_ENABLED"

GATE_NAMES = (
    BROKER_ADAPTER_READY,
    CREDENTIALS_READY,
    ACCOUNT_CONFIRMED,
    RISK_CONFIGURATION_VALID,
    EXECUTION_SAFETY_ENABLED,
)


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class LiveGatesReport:
    ready: bool
    gates: tuple[GateResult, ...] = field(default_factory=tuple)

    def failed(self) -> tuple[GateResult, ...]:
        return tuple(g for g in self.gates if not g.passed)


def risk_configuration_valid(policy: RiskPolicy) -> tuple[bool, tuple[str, ...]]:
    """Pure sanity check on the active risk policy (no market data needed).

    The kernel (``live_readiness::risk_reasons``) owns the ten comparisons
    and the words it denies with; this gathers the policy's limits.
    """
    reasons = native_risk_reasons(
        max_position_qty=policy.max_position_qty,
        max_order_qty=policy.max_order_qty,
        max_notional=policy.max_notional,
        max_exposure_pct=policy.max_exposure_pct,
        daily_loss_limit=policy.daily_loss_limit,
        strategy_loss_limit=policy.strategy_loss_limit,
        cooldown_seconds=policy.cooldown_seconds,
        max_orders_per_day=policy.max_orders_per_day,
        require_fresh_data_seconds=policy.require_fresh_data_seconds,
    )
    return (not reasons, reasons)


def confirm_account(
    adapter: BrokerAdapter | None,
    expected_account_id: str,
    expected_environment: str,
) -> tuple[bool, tuple[str, ...]]:
    """Verify broker/account identity through the adapter.

    A sandbox account never confirms as LIVE, even when the account id
    matches — environment is part of identity, and that rule is the
    kernel's: this function only reads what the adapter reports.
    """
    if adapter is None:
        return False, ("no broker adapter available",)
    try:
        info = adapter.account()
    except Exception as exc:
        return False, (f"account query failed: {exc}",)
    reasons = native_account_reasons(
        account_id=str(info.get("account_id", "")),
        environment=str(info.get("environment", "")),
        expected_account_id=expected_account_id,
        expected_environment=expected_environment,
    )
    return (not reasons, reasons)


def evaluate_live_gates(
    *,
    adapter: BrokerAdapter | None,
    adapter_error: str = "",
    credentials: BrokerCredentials | None = None,
    credential_store: CredentialStore | None = None,
    expected_account_id: str = "",
    expected_environment: str = "live",
    risk_policy: RiskPolicy | None = None,
    kill_halted: bool = True,
) -> LiveGatesReport:
    """Evaluate the five live gates. Fail-closed: any false → not ready.

    Only facts are gathered here — an adapter answers its own health and
    identity, a store answers secret resolvability. The kernel assembles the
    gate list, so names, order, joins and fallback wording never exist twice.
    """
    adapter_present = adapter is not None
    health_ok, health_detail = False, ""
    if adapter is not None:
        try:
            healthy, reason = adapter.health()
        except Exception as exc:
            healthy, reason = False, f"health check failed: {exc}"
        health_ok, health_detail = bool(healthy), "" if healthy else str(reason)
    creds = credentials or BrokerCredentials()
    creds_ok, creds_reasons = validate_credentials(
        creds, credential_store, require_secrets=True, expected_environment=expected_environment
    )
    account_ok, account_reasons = (
        confirm_account(adapter, expected_account_id, expected_environment)
        if adapter is not None
        else (False, ())
    )
    risk_ok, risk_reasons = (
        risk_configuration_valid(risk_policy) if risk_policy is not None else (False, ())
    )
    ready, gates = native_gate_verdict(
        adapter_present=adapter_present,
        adapter_error=adapter_error,
        health_ok=health_ok,
        health_detail=health_detail,
        credentials_ok=creds_ok,
        credential_reasons=creds_reasons,
        account_evaluated=adapter_present,
        account_ok=account_ok,
        account_reasons=account_reasons,
        risk_present=risk_policy is not None,
        risk_ok=risk_ok,
        risk_reasons=risk_reasons,
        kill_halted=kill_halted,
    )
    return LiveGatesReport(
        ready=ready,
        gates=tuple(GateResult(name, passed, detail) for name, passed, detail in gates),
    )


def format_gates_report(report: LiveGatesReport) -> str:
    """Operational rendering for --check-live. Never includes secrets."""
    lines = ["LIVE READY" if report.ready else "LIVE NOT READY"]
    for gate in report.gates:
        mark = "ok" if gate.passed else "FAIL"
        detail = f" — {gate.detail}" if gate.detail else ""
        lines.append(f"[{mark}] {gate.name}{detail}")
    return "\n".join(lines)


def funds_snapshot_from_face(face: object) -> FundsSnapshot:
    """Capability-gated funds read: raises ``UnsupportedCapabilityError``
    when the venue does not advertise ``account.funds`` (never fake zeros).

    Accepts both UBL faces (``capabilities()`` method) and execution
    venues (``capabilities`` tuple property) — the id checked is the same
    byte-identical ``account.funds`` string in both vocabularies.
    """
    from broker.vocab import UnsupportedCapabilityError

    capabilities = getattr(face, "capabilities", None)
    advertised: tuple[str, ...]
    if callable(capabilities):
        return FundsSnapshot.from_dict(require_funds(face).funds())  # type: ignore[attr-defined]
    try:
        advertised = tuple(capabilities or ())
    except TypeError as exc:
        raise UnsupportedCapabilityError(
            f"broker {getattr(face, 'name', '?')!r} has no readable capabilities "
            f"(funds unavailable): {exc}"
        ) from exc
    if "account.funds" not in advertised:
        raise UnsupportedCapabilityError(
            f"broker {getattr(face, 'name', '?')!r} does not provide account.funds capability"
        )
    funds = getattr(face, "funds", None)
    if not callable(funds):
        raise UnsupportedCapabilityError(
            f"broker {getattr(face, 'name', '?')!r} advertises account.funds but exposes no funds()"
        )
    payload = funds()
    if not isinstance(payload, dict):
        raise UnsupportedCapabilityError(
            f"broker {getattr(face, 'name', '?')!r} returned non-dict funds payload"
        )
    return FundsSnapshot.from_dict(payload)


def risk_capital_from_funds(snapshot: FundsSnapshot) -> tuple[float, float]:
    """FINAL §H mapping: ``(available → available_capital, equity → equity)``.

    Pure projection for ``RiskRequest`` construction. Missing funds must
    never authorize trading — callers gate on ``funds_valid_for_live``.
    """
    return float(snapshot.available), float(snapshot.equity)


def funds_valid_for_live(snapshot: FundsSnapshot) -> tuple[bool, tuple[str, ...]]:
    """Funds preconditions for LIVE (FINAL §H/§S).

    Fail-closed: non-positive availability/equity or an empty currency
    denies. Zero available capital can never authorize a live order — the
    comparisons and their wording are the kernel's.
    """
    reasons = native_funds_reasons(
        available=float(snapshot.available),
        equity=float(snapshot.equity),
        currency=snapshot.currency,
    )
    return (not reasons, reasons)
