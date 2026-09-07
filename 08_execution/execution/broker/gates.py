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
    """Pure sanity check on the active risk policy (no market data needed)."""
    reasons: list[str] = []
    if policy.max_position_qty <= 0:
        reasons.append("max_position_qty must be positive")
    if policy.max_order_qty <= 0:
        reasons.append("max_order_qty must be positive")
    if policy.max_order_qty > policy.max_position_qty:
        reasons.append("max_order_qty exceeds max_position_qty")
    for name in ("max_notional", "max_exposure_pct", "daily_loss_limit", "strategy_loss_limit"):
        value = getattr(policy, name, None)
        if value is not None and value <= 0:
            reasons.append(f"{name} must be positive when set")
    if policy.cooldown_seconds < 0:
        reasons.append("cooldown_seconds must not be negative")
    if policy.max_orders_per_day is not None and policy.max_orders_per_day <= 0:
        reasons.append("max_orders_per_day must be positive when set")
    if policy.require_fresh_data_seconds is not None and policy.require_fresh_data_seconds <= 0:
        reasons.append("require_fresh_data_seconds must be positive when set")
    return (not reasons, tuple(reasons))


def confirm_account(
    adapter: BrokerAdapter | None,
    expected_account_id: str,
    expected_environment: str,
) -> tuple[bool, tuple[str, ...]]:
    """Verify broker/account identity through the adapter.

    A sandbox account never confirms as LIVE, even when the account id
    matches — environment is part of identity.
    """
    if adapter is None:
        return False, ("no broker adapter available",)
    try:
        info = adapter.account()
    except Exception as exc:
        return False, (f"account query failed: {exc}",)
    account_id = str(info.get("account_id", ""))
    environment = str(info.get("environment", ""))
    if not account_id:
        return False, ("adapter reports no account_id",)
    reasons: list[str] = []
    if expected_account_id and account_id != expected_account_id:
        reasons.append(f"account mismatch: adapter={account_id!r} expected={expected_account_id!r}")
    if expected_environment:
        if environment != expected_environment:
            reasons.append(
                f"environment mismatch: adapter={environment!r} expected={expected_environment!r}"
            )
        if expected_environment == "live" and environment != "live":
            reasons.append("sandbox account must never be treated as LIVE")
    return (not reasons, tuple(reasons))


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
    """Evaluate the five live gates. Fail-closed: any false → not ready."""
    gates: list[GateResult] = []
    if adapter is None:
        gates.append(GateResult(BROKER_ADAPTER_READY, False, adapter_error or "no adapter"))
    else:
        try:
            healthy, reason = adapter.health()
        except Exception as exc:
            healthy, reason = False, f"health check failed: {exc}"
        gates.append(GateResult(BROKER_ADAPTER_READY, bool(healthy), "" if healthy else reason))
    creds = credentials or BrokerCredentials()
    creds_ok, creds_reasons = validate_credentials(
        creds, credential_store, require_secrets=True, expected_environment=expected_environment
    )
    gates.append(
        GateResult(CREDENTIALS_READY, creds_ok, "" if creds_ok else "; ".join(creds_reasons))
    )
    if adapter is None:
        gates.append(GateResult(ACCOUNT_CONFIRMED, False, "no adapter to confirm against"))
    else:
        account_ok, account_reasons = confirm_account(
            adapter, expected_account_id, expected_environment
        )
        gates.append(
            GateResult(
                ACCOUNT_CONFIRMED, account_ok, "" if account_ok else "; ".join(account_reasons)
            )
        )
    if risk_policy is None:
        gates.append(GateResult(RISK_CONFIGURATION_VALID, False, "no risk policy active"))
    else:
        policy_ok, policy_reasons = risk_configuration_valid(risk_policy)
        gates.append(
            GateResult(
                RISK_CONFIGURATION_VALID, policy_ok, "" if policy_ok else "; ".join(policy_reasons)
            )
        )
    gates.append(
        GateResult(
            EXECUTION_SAFETY_ENABLED, not kill_halted, "kill switch engaged" if kill_halted else ""
        )
    )
    return LiveGatesReport(ready=all(g.passed for g in gates), gates=tuple(gates))


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
    denies. Zero available capital can never authorize a live order.
    """
    reasons: list[str] = []
    if snapshot.available <= 0:
        reasons.append(f"available capital not positive: {snapshot.available}")
    if snapshot.equity <= 0:
        reasons.append(f"equity not positive: {snapshot.equity}")
    if not snapshot.currency or not snapshot.currency.strip():
        reasons.append("funds currency missing")
    return (not reasons, tuple(reasons))
