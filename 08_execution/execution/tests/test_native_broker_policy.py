"""Parity: Rust broker-boundary policy vs the retired Python authorities.

Each reference below is a verbatim copy of logic that used to decide live
behaviour inside ``execution/broker`` (the retry table, the throttle window,
the budget validators, credential and account identity, funds preconditions,
the five-gate assembly, the twelve-step activation ceremony and the sandbox
fill script). The kernel
now owns those answers, wording included; the copies stay here as oracles so
drift fails a test instead of silently changing how the system behaves
against a venue.
"""

from __future__ import annotations

import itertools
import math
import random
from collections import deque
from dataclasses import dataclass

import pytest

from execution.broker.activation import evaluate_activation
from execution.broker.credentials import BrokerCredentials, validate_credentials
from execution.broker.gates import GateResult, LiveGatesReport
from execution.broker.native_policy import (
    native_account_reasons,
    native_credential_reasons,
    native_funds_reasons,
    native_gate_verdict,
    native_settle_decision,
)
from execution.broker.resilience import (
    BackoffPolicy,
    RateLimiter,
    ReconnectPolicy,
    RetryKind,
    TimeoutPolicy,
    classify_retry,
    clock_drift_ok,
)
from execution.modes import LiveArm
from execution.portfolio.reconcile import ReconcileStatus, ReconciliationVerdict

# ── retry classification ──────────────────────────────────────────────────

_REF_RETRY_TABLE = {
    "health": RetryKind.SAFE_TO_RETRY,
    "account": RetryKind.SAFE_TO_RETRY,
    "positions": RetryKind.SAFE_TO_RETRY,
    "open_orders": RetryKind.SAFE_TO_RETRY,
    "stream_poll": RetryKind.SAFE_TO_RETRY,
    "place_order": RetryKind.NOT_SAFE_TO_RETRY,
    "cancel_order": RetryKind.REQUIRES_RECONCILIATION,
    "modify_order": RetryKind.REQUIRES_RECONCILIATION,
    "settle": RetryKind.REQUIRES_RECONCILIATION,
}


def _ref_classify(operation: str) -> RetryKind:
    """Old ``_RETRY_TABLE.get`` with the fail-closed default, verbatim."""
    return _REF_RETRY_TABLE.get(operation, RetryKind.NOT_SAFE_TO_RETRY)


def test_the_retry_table_answers_identically() -> None:
    for operation in [*list(_REF_RETRY_TABLE), "", " ", "Health", "place_Order", "x" * 40]:
        assert classify_retry(operation) is _ref_classify(operation), operation


# ── sliding-window throttle ──────────────────────────────────────────────


class _RefLimiter:
    """Old ``RateLimiter``, verbatim (deque window plus the 429 counter)."""

    def __init__(self, max_requests: int = 100, window_seconds: float = 60.0) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.hits: deque[float] = deque()
        self.rejections_429 = 0

    def allow(self, now_epoch: float) -> bool:
        cutoff = now_epoch - self.window_seconds
        while self.hits and self.hits[0] <= cutoff:
            self.hits.popleft()
        if len(self.hits) >= self.max_requests:
            return False
        self.hits.append(now_epoch)
        return True

    def record_429(self) -> None:
        self.rejections_429 += 1


def test_the_throttle_denies_and_slides_like_the_retired_rule() -> None:
    rng = random.Random(20260921)
    for max_requests, window in [(1, 1.0), (2, 10.0), (5, 60.0), (100, 3600.0)]:
        live = RateLimiter(max_requests=max_requests, window_seconds=window)
        ref = _RefLimiter(max_requests=max_requests, window_seconds=window)
        now = 0.0
        for _ in range(300):
            now += rng.uniform(0.0, window * 1.5)
            assert live.allow(now) is ref.allow(now), (max_requests, window, now)
            assert live.used == len(ref.hits)
            if rng.random() < 0.2:
                live.record_429()
                ref.record_429()
            assert live.rejections_429 == ref.rejections_429


def test_the_throttle_rejects_what_the_retired_constructor_rejected() -> None:
    for kwargs, message in [
        ({"max_requests": 0}, "max_requests must be positive"),
        ({"max_requests": -3}, "max_requests must be positive"),
        ({"window_seconds": 0.0}, "window_seconds must be positive"),
        ({"window_seconds": -1.0}, "window_seconds must be positive"),
    ]:
        with pytest.raises(ValueError) as live_exc:
            RateLimiter(**kwargs)  # type: ignore[arg-type]
        with pytest.raises(ValueError) as ref_exc:
            _RefLimiter(**kwargs)  # type: ignore[arg-type]
        assert str(live_exc.value) == str(ref_exc.value) == message, kwargs
    # The retired validator compared with `<=`, so a NaN window was accepted.
    assert RateLimiter(window_seconds=math.nan).allow(1.0) is True


# ── clock drift ──────────────────────────────────────────────────────────


def _ref_clock_drift_ok(local_epoch: object, reference_epoch: object, max_drift: float) -> bool:
    """Old ``clock_drift_ok``, verbatim (conversion included)."""
    try:
        local = float(local_epoch)  # type: ignore[arg-type]
        reference = float(reference_epoch)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if max_drift < 0:
        return False
    return abs(local - reference) <= max_drift


def test_clock_drift_gate_matches_the_retired_rule() -> None:
    inputs: list[object] = [100.0, 100, "100", "x", None, math.nan, math.inf, -1e18, 1e18, object()]
    for local in inputs:
        for reference in inputs:
            for max_drift in (0.0, 5.0, -1.0, math.nan):
                assert clock_drift_ok(local, reference, max_drift) == _ref_clock_drift_ok(
                    local, reference, max_drift
                ), (local, reference, max_drift)


# ── timeout / backoff / reconnect budgets ────────────────────────────────

_TIMEOUT_FIELDS = ("connect_seconds", "read_seconds", "submit_seconds", "reconcile_seconds")


def _ref_timeout_problem(**values: float) -> str:
    """Old ``TimeoutPolicy.__post_init__``, verbatim."""
    for name in _TIMEOUT_FIELDS:
        if values[name] <= 0:
            return f"{name} must be positive"
    return ""


def _ref_backoff_problem(base: float, factor: float, cap: float, attempts: int) -> str:
    """Old ``BackoffPolicy.__post_init__``, verbatim."""
    if base <= 0 or factor < 1.0 or cap <= 0:
        return "backoff requires positive base/max and factor >= 1"
    if attempts < 1:
        return "backoff max_attempts must be >= 1"
    return ""


def _ref_backoff_delay(base: float, factor: float, cap: float, attempt: int) -> float:
    """Old ``BackoffPolicy.delay``, verbatim."""
    if attempt < 1:
        return base
    return min(cap, base * (factor ** (attempt - 1)))


def _ref_reconnect_problem(attempts: int) -> str:
    """Old ``ReconnectPolicy.__post_init__``, verbatim."""
    return "" if attempts >= 1 else "reconnect max_attempts must be >= 1"


def test_timeout_budgets_name_the_same_first_field() -> None:
    assert TimeoutPolicy()
    rng = random.Random(11)
    for _ in range(200):
        values = {name: rng.choice([0.0, -1.0, 5.0, math.nan, 1e9]) for name in _TIMEOUT_FIELDS}
        expected = _ref_timeout_problem(**values)
        if not expected:
            TimeoutPolicy(**values)  # type: ignore[arg-type]
            continue
        with pytest.raises(ValueError) as exc:
            TimeoutPolicy(**values)  # type: ignore[arg-type]
        assert str(exc.value) == expected, values


def test_backoff_and_reconnect_budgets_agree_with_the_retired_rules() -> None:
    rng = random.Random(20260921)
    for _ in range(200):
        base = rng.choice([1.0, 0.0, -2.0, math.nan, 0.5, 3.0])
        factor = rng.choice([2.0, 1.0, 0.5, math.nan])
        cap = rng.choice([30.0, 0.0, math.nan, 1e6])
        attempts = rng.choice([5, 1, 0, -4])
        expected = _ref_backoff_problem(base, factor, cap, attempts)
        if expected:
            with pytest.raises(ValueError) as exc:
                BackoffPolicy(base, factor, cap, attempts)
            assert str(exc.value) == expected, (base, factor, cap, attempts)
            continue
        policy = BackoffPolicy(base, factor, cap, attempts)
        for attempt in range(-2, 14):
            live = policy.delay(attempt)
            ref = _ref_backoff_delay(base, factor, cap, attempt)
            assert live == ref or (math.isnan(live) and math.isnan(ref)), (
                base,
                factor,
                cap,
                attempt,
            )
        assert policy.exhausted(attempts) is True
        assert policy.exhausted(attempts - 1) is False


def test_reconnect_budgets_agree_with_the_retired_rule() -> None:
    for attempts in (0, -4):
        with pytest.raises(ValueError) as exc:
            ReconnectPolicy(BackoffPolicy(), attempts)
        assert str(exc.value) == _ref_reconnect_problem(attempts), attempts
    for attempts in (1, 5, 12):
        reconnect = ReconnectPolicy(BackoffPolicy(), attempts)
        assert reconnect.exhausted(attempts) is True
        assert reconnect.exhausted(attempts - 1) is False


def test_the_defaults_still_behave_as_the_documented_budgets() -> None:
    policy = BackoffPolicy()
    assert [policy.delay(n) for n in range(0, 7)] == [1.0, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
    assert policy.exhausted(4) is False
    assert policy.exhausted(5) is True
    assert TimeoutPolicy().reconcile_seconds == 30.0
    assert ReconnectPolicy().max_attempts == 5


# ── credential identity ──────────────────────────────────────────────────


def _ref_credential_reasons(
    account_id: str,
    environment: str,
    refs: tuple[str, ...],
    resolvable: tuple[bool, ...],
    require_secrets: bool,
    store_present: bool,
    expected: str,
) -> tuple[str, ...]:
    """Old ``validate_credentials``, verbatim."""
    reasons: list[str] = []
    if not account_id:
        reasons.append("missing account_id")
    if not environment:
        reasons.append("missing environment")
    elif expected and environment != expected:
        reasons.append(
            f"environment mismatch: credentials say {environment!r}, expected {expected!r}"
        )
    if require_secrets:
        if not store_present:
            reasons.append("no credential store configured")
        else:
            for index, ref in enumerate(refs):
                if not resolvable[index]:
                    reasons.append(f"secret not resolvable: {ref}")
        if not refs:
            reasons.append("no secret key_refs declared")
    return tuple(reasons)


def test_credential_identity_agrees_with_the_retired_rule() -> None:
    ref_sets: tuple[tuple[str, ...], ...] = ((), ("API_KEY",), ("API_KEY", "API_SECRET"))
    for account_id, environment, expected, refs, require, present in itertools.product(
        ("", "a"),
        ("", "live", "sandbox"),
        ("", "live", "sandbox"),
        ref_sets,
        (False, True),
        (False, True),
    ):
        resolvable = tuple(index % 2 == 0 for index in range(len(refs)))
        live = native_credential_reasons(
            account_id=account_id,
            environment=environment,
            expected_environment=expected,
            key_refs=refs,
            resolvable=resolvable,
            require_secrets=require,
            store_present=present,
        )
        reference = _ref_credential_reasons(
            account_id, environment, refs, resolvable, require, present, expected
        )
        assert live == reference, (account_id, environment, expected, refs, require, present)


class _StoreThatMustStayClosed:
    """Proves the facade never resolves a secret name it was not asked about."""

    def get(self, name: str) -> str | None:
        raise RuntimeError(f"secret {name!r} resolved when it was not required")


def test_the_facade_asks_the_store_only_when_secrets_are_required() -> None:
    creds = BrokerCredentials(account_id="a", environment="live", key_refs=("API_KEY", "NOPE"))
    assert validate_credentials(creds, _StoreThatMustStayClosed(), require_secrets=False) == (
        True,
        (),
    )
    with pytest.raises(RuntimeError):
        validate_credentials(creds, _StoreThatMustStayClosed(), require_secrets=True)


# ── adapter-reported account identity ────────────────────────────────────


def _ref_account_reasons(
    account_id: str, environment: str, expected_id: str, expected_env: str
) -> tuple[str, ...]:
    """Old ``confirm_account`` identity rule, verbatim."""
    if not account_id:
        return ("adapter reports no account_id",)
    reasons: list[str] = []
    if expected_id and account_id != expected_id:
        reasons.append(f"account mismatch: adapter={account_id!r} expected={expected_id!r}")
    if expected_env:
        if environment != expected_env:
            reasons.append(
                f"environment mismatch: adapter={environment!r} expected={expected_env!r}"
            )
        if expected_env == "live" and environment != "live":
            reasons.append("sandbox account must never be treated as LIVE")
    return tuple(reasons)


def test_account_identity_agrees_with_the_retired_rule() -> None:
    for account_id, environment, expected_id, expected_env in itertools.product(
        ("", "sbx-1", "a-1"), ("", "live", "sandbox"), ("", "sbx-1"), ("", "live", "sandbox")
    ):
        live = native_account_reasons(
            account_id=account_id,
            environment=environment,
            expected_account_id=expected_id,
            expected_environment=expected_env,
        )
        assert live == _ref_account_reasons(account_id, environment, expected_id, expected_env), (
            account_id,
            environment,
            expected_id,
            expected_env,
        )


# ── funds preconditions ──────────────────────────────────────────────────


def _ref_funds_reasons(available: float, equity: float, currency: str) -> tuple[str, ...]:
    """Old ``funds_valid_for_live``, verbatim (float formatting included)."""
    reasons: list[str] = []
    if available <= 0:
        reasons.append(f"available capital not positive: {available}")
    if equity <= 0:
        reasons.append(f"equity not positive: {equity}")
    if not currency or not currency.strip():
        reasons.append("funds currency missing")
    return tuple(reasons)


def test_funds_wording_agrees_with_the_retired_rule() -> None:
    values = (0.0, -0.0, -5.0, 100.0, 0.25, 1.0 / 3.0, 1e16, 1e-7, math.nan)
    for available, equity, currency in itertools.product(values, values, ("INR", "  ", "")):
        live = native_funds_reasons(available=available, equity=equity, currency=currency)
        reference = _ref_funds_reasons(available, equity, currency)
        assert live == reference, (available, equity, currency)


# ── the five live gates ──────────────────────────────────────────────────

_JOIN = "; "


def _ref_gates(
    adapter_present: bool,
    adapter_error: str,
    health_ok: bool,
    health_detail: str,
    credentials_ok: bool,
    credential_reasons: tuple[str, ...],
    account_evaluated: bool,
    account_ok: bool,
    account_reasons: tuple[str, ...],
    risk_present: bool,
    risk_ok: bool,
    risk_reasons: tuple[str, ...],
    kill_halted: bool,
) -> tuple[bool, tuple[tuple[str, bool, str], ...]]:
    """Old ``evaluate_live_gates`` assembly, verbatim (order and joins)."""
    gates: list[tuple[str, bool, str]] = []
    if not adapter_present:
        gates.append(("BROKER_ADAPTER_READY", False, adapter_error or "no adapter"))
    else:
        gates.append(("BROKER_ADAPTER_READY", health_ok, "" if health_ok else health_detail))
    gates.append(
        (
            "CREDENTIALS_READY",
            credentials_ok,
            "" if credentials_ok else _JOIN.join(credential_reasons),
        )
    )
    if not account_evaluated:
        gates.append(("ACCOUNT_CONFIRMED", False, "no adapter to confirm against"))
    else:
        gates.append(
            ("ACCOUNT_CONFIRMED", account_ok, "" if account_ok else _JOIN.join(account_reasons))
        )
    if not risk_present:
        gates.append(("RISK_CONFIGURATION_VALID", False, "no risk policy active"))
    else:
        gates.append(
            ("RISK_CONFIGURATION_VALID", risk_ok, "" if risk_ok else _JOIN.join(risk_reasons))
        )
    gates.append(
        ("EXECUTION_SAFETY_ENABLED", not kill_halted, "kill switch engaged" if kill_halted else "")
    )
    return all(gate[1] for gate in gates), tuple(gates)


def test_the_five_gates_assemble_exactly_as_the_retired_rule() -> None:
    adapter_cases = (
        (True, "", True, ""),
        (True, "", False, "stale quotes"),
        (True, "ignored", False, ""),
        (False, "", False, ""),
        (False, "adapter build failed", False, "ignored"),
    )
    credential_cases = (
        (True, ()),
        (False, ("missing account_id",)),
        (False, ("a:|b\nc", "no secret key_refs declared")),
        (False, ()),
    )
    account_cases = (
        (True, ()),
        (False, ("sandbox account must never be treated as LIVE",)),
        (False, ("adapter='' expected='live'",)),
        (False, ()),
    )
    risk_cases = (
        (True, True, ()),
        (True, False, ("max_order_qty exceeds max_position_qty",)),
        (True, False, ()),
        (False, False, ()),
    )
    for present, error, health_ok, health_detail in adapter_cases:
        for credential_case, account_case, (
            risk_present,
            risk_ok,
            risk_reasons,
        ), kill in itertools.product(credential_cases, account_cases, risk_cases, (True, False)):
            credentials_ok, credential_reasons = credential_case
            account_ok, account_reasons = account_case
            live = native_gate_verdict(
                adapter_present=present,
                adapter_error=error,
                health_ok=health_ok,
                health_detail=health_detail,
                credentials_ok=credentials_ok,
                credential_reasons=credential_reasons,
                account_evaluated=present,
                account_ok=account_ok,
                account_reasons=account_reasons,
                risk_present=risk_present,
                risk_ok=risk_ok,
                risk_reasons=risk_reasons,
                kill_halted=kill,
            )
            reference = _ref_gates(
                present,
                error,
                health_ok,
                health_detail,
                credentials_ok,
                credential_reasons,
                present,
                account_ok,
                account_reasons,
                risk_present,
                risk_ok,
                risk_reasons,
                kill,
            )
            assert live == reference, (
                present,
                error,
                health_ok,
                credential_case,
                account_case,
                risk_present,
                risk_ok,
                kill,
            )


# ── the twelve-step activation ceremony ──────────────────────────────────


def _ref_ceremony(
    broker_name: str,
    environment: str,
    credentials_ok: bool,
    credential_reasons: tuple[str, ...],
    account_ok: bool,
    account_reasons: tuple[str, ...],
    market_healthy: bool,
    market_reason: str,
    funds_ok: bool,
    funds_reasons: tuple[str, ...],
    risk_ok: bool,
    risk_reasons: tuple[str, ...],
    verdict_present: bool,
    verdict_blocks: bool,
    verdict_reasons: tuple[str, ...],
    kill_halted: bool,
    gates: tuple[tuple[str, bool, str], ...] | None,
    armed_value: str,
) -> tuple[bool, tuple[tuple[int, str, bool, str], ...], tuple[str, ...]]:
    """Old ``evaluate_activation``, verbatim."""
    gates_ready = gates is not None and all(gate[1] for gate in gates)
    gate_lines = (
        () if gates is None else tuple(f"{name}: {detail}" for name, ok, detail in gates if not ok)
    )
    steps = [
        (1, "SELECT_BROKER", bool(broker_name), "" if broker_name else "no broker selected"),
        (
            2,
            "LIVE_ENVIRONMENT",
            environment == "live",
            "" if environment == "live" else f"environment is {environment!r}, not 'live'",
        ),
        (
            3,
            "VALIDATE_CREDENTIALS",
            credentials_ok,
            "" if credentials_ok else _JOIN.join(credential_reasons) or "credentials invalid",
        ),
        (
            4,
            "CONFIRM_ACCOUNT",
            account_ok,
            "" if account_ok else _JOIN.join(account_reasons) or "account unconfirmed",
        ),
        (
            5,
            "VERIFY_MARKET_DATA",
            market_healthy,
            "" if market_healthy else market_reason or "market data unhealthy",
        ),
        (
            6,
            "VERIFY_FUNDS",
            funds_ok,
            "" if funds_ok else _JOIN.join(funds_reasons) or "funds invalid",
        ),
        (
            7,
            "VALIDATE_RISK",
            risk_ok,
            "" if risk_ok else _JOIN.join(risk_reasons) or "risk invalid",
        ),
        (
            8,
            "RECONCILE",
            verdict_present and not verdict_blocks,
            "reconciliation not evaluated"
            if not verdict_present
            else (
                ""
                if not verdict_blocks
                else _JOIN.join(verdict_reasons) or "reconciliation blocks live"
            ),
        ),
        (
            9,
            "EVALUATE_GATES",
            gates_ready,
            "gates not evaluated"
            if gates is None
            else ("" if gates_ready else _JOIN.join(gate_lines) or "gates failing"),
        ),
        (10, "VERIFY_KILL_SWITCH", not kill_halted, "kill switch engaged" if kill_halted else ""),
        (
            11,
            "EXPLICIT_ARM",
            armed_value == "ARMED",
            "" if armed_value == "ARMED" else f"arming is {armed_value}, not ARMED",
        ),
    ]
    ready = all(step[2] for step in steps)
    blockers = tuple(f"{name}: {detail}" for _, name, ok, detail in steps if not ok and detail)
    steps.append(
        (
            12,
            "START_LIVE",
            False,
            "ceremony green — start remains an explicit operator act"
            if ready
            else "blocked: ceremony not fully green",
        )
    )
    return ready, tuple(steps), blockers


@dataclass(frozen=True)
class _Scenario:
    """One ceremony input set; the defaults are a green, disarmed ceremony."""

    broker_name: str = "ubl"
    environment: str = "live"
    credentials_ok: bool = True
    credential_reasons: tuple[str, ...] = ()
    account_ok: bool = True
    account_reasons: tuple[str, ...] = ()
    market_healthy: bool = True
    market_reason: str = ""
    funds_ok: bool = True
    funds_reasons: tuple[str, ...] = ()
    risk_ok: bool = True
    risk_reasons: tuple[str, ...] = ()
    verdict_present: bool = True
    verdict_blocks: bool = False
    verdict_reasons: tuple[str, ...] = ()
    kill_halted: bool = False
    gates: tuple[tuple[str, bool, str], ...] | None = None
    armed_value: str = "DISARMED"


_CEREMONY_SCENARIOS: tuple[_Scenario, ...] = (
    _Scenario(),
    _Scenario(armed_value="ARMED"),
    _Scenario(
        broker_name="",
        environment="sandbox",
        credentials_ok=False,
        credential_reasons=("missing account_id", "secret not resolvable: API_KEY"),
        account_ok=False,
        account_reasons=("adapter reports no account_id",),
        market_healthy=False,
        funds_ok=False,
        risk_ok=False,
        kill_halted=True,
        verdict_present=False,
    ),
    _Scenario(
        credentials_ok=False,
        account_ok=False,
        market_healthy=False,
        funds_ok=False,
        risk_ok=False,
        verdict_blocks=True,
        gates=(("CREDENTIALS_READY", False, ""),),
    ),
    _Scenario(
        gates=(
            ("ACCOUNT_CONFIRMED", False, "adapter lag"),
            ("CREDENTIALS_READY", False, "a:|b\nc"),
        )
    ),
    _Scenario(gates=(("RISK_CONFIGURATION_VALID", True, "never shown"),)),
    _Scenario(armed_value="ARMED", market_healthy=False),
    _Scenario(environment="", market_healthy=False, market_reason="no feed on :|instrument\nname"),
)


def test_the_ceremony_agrees_with_the_retired_rule() -> None:
    for case in _CEREMONY_SCENARIOS:
        report = (
            None
            if case.gates is None
            else LiveGatesReport(
                ready=all(gate[1] for gate in case.gates),
                gates=tuple(GateResult(name, ok, detail) for name, ok, detail in case.gates),
            )
        )
        verdict = (
            None
            if not case.verdict_present
            else ReconciliationVerdict(
                status=ReconcileStatus.BLOCKED if case.verdict_blocks else ReconcileStatus.SAFE,
                reasons=case.verdict_reasons,
            )
        )
        live = evaluate_activation(
            broker_name=case.broker_name,
            environment=case.environment,
            credentials_ok=case.credentials_ok,
            credential_reasons=case.credential_reasons,
            account_ok=case.account_ok,
            account_reasons=case.account_reasons,
            market_healthy=case.market_healthy,
            market_reason=case.market_reason,
            funds_ok=case.funds_ok,
            funds_reasons=case.funds_reasons,
            risk_ok=case.risk_ok,
            risk_reasons=case.risk_reasons,
            verdict=verdict,
            kill_halted=case.kill_halted,
            gates=report,
            armed=LiveArm.ARMED if case.armed_value == "ARMED" else LiveArm.DISARMED,
        )
        reference = _ref_ceremony(
            case.broker_name,
            case.environment,
            case.credentials_ok,
            case.credential_reasons,
            case.account_ok,
            case.account_reasons,
            case.market_healthy,
            case.market_reason,
            case.funds_ok,
            case.funds_reasons,
            case.risk_ok,
            case.risk_reasons,
            case.verdict_present,
            case.verdict_blocks,
            case.verdict_reasons,
            case.kill_halted,
            case.gates,
            case.armed_value,
        )
        assert (
            live.ready,
            tuple((step.index, step.name, step.ok, step.detail) for step in live.steps),
            live.blockers,
        ) == reference, case


# ── sandbox fill-script policy ────────────────────────────────────────────


def _ref_decision(policy: str) -> tuple[str, str, float | None]:
    """The retired ``SandboxBroker.settle`` script, verbatim, as an answer."""
    if policy.startswith("delay:"):
        try:
            remaining = int(policy.split(":", 1)[1])
        except ValueError:
            return "REJECT", f"bad delay policy: {policy}", None
        if remaining > 0:
            return "WAIT", f"delay:{remaining - 1}", None
        policy = "full"
    if policy.startswith("reject:"):
        return "REJECT", policy.split(":", 1)[1] or "venue reject", None
    if policy == "full":
        return "FULL", "", None
    if policy.startswith("partial:"):
        try:
            return "PARTIAL", "", float(policy.split(":", 1)[1])
        except ValueError:
            return "REJECT", f"bad partial policy: {policy}", None
    return "REJECT", f"unknown policy: {policy}", None


_SCRIPTS = (
    "full",
    "FULL",
    "",
    "freeze",
    "delay:",
    "delay:0",
    "delay:1",
    "delay:3",
    "delay:-4",
    "delay:+2",
    "delay: 2 ",
    "delay:1_0",
    "delay:0_0",
    "delay:_1",
    "delay:1_",
    "delay:2.0",
    "delay:abc",
    "delay:reject:x",
    "reject:",
    "reject:margin",
    "reject:no liquidity",
    "reject::x",
    "reject:a:|b\nc",
    "partial:",
    "partial:0",
    "partial:5",
    "partial:2.5",
    "partial:-1",
    "partial: 1e3 ",
    "partial:1_0.5",
    "partial:.5",
    "partial:1.",
    "partial:1.e5",
    "partial:1e-7",
    "partial:1e+2",
    "partial:1e1_0",
    "partial:00012.500",
    "partial:inf",
    "partial:Infinity",
    "partial:-inf",
    "partial:nan",
    "partial:NAN",
    "partial:-0.0",
    "partial:1e",
    "partial:1e2.5",
    "partial:0x10",
    "partial:1.2.3",
    "partial:--1",
    "partial:1 2",
    "partial:abc",
    "partial:999999999999999999999999e999",
    "unknown policy: x",
    "delay:0:extra",
    "partial:5:extra",
)


@pytest.mark.parametrize("policy", _SCRIPTS)
def test_the_script_grammar_answers_as_the_retired_rule(policy: str) -> None:
    kind, detail, quantity = native_settle_decision(policy)
    ref_kind, ref_detail, ref_qty = _ref_decision(policy)
    assert (kind, detail) == (ref_kind, ref_detail), policy
    if quantity is None or ref_qty is None:
        assert quantity is None and ref_qty is None, policy
    elif math.isnan(quantity) or math.isnan(ref_qty):
        assert math.isnan(quantity) and math.isnan(ref_qty), policy
    else:
        assert quantity == ref_qty, policy


def test_a_delay_too_wide_for_i64_fails_closed() -> None:
    """Documented narrowing: CPython would keep counting down forever."""
    assert native_settle_decision("delay:99999999999999999999") == (
        "REJECT",
        "bad delay policy: delay:99999999999999999999",
        None,
    )
