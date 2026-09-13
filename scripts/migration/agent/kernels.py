"""Risk-kernel harnesses: golden, fuzz and shadow differential runners.

Hand-reviewed infrastructure. Per-unit unknowns (oracle module, golden
fixtures) are agent-generated; this module only executes the comparison
with honest verdicts — missing oracles are INCONCLUSIVE, never PASS.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

BASE_TS = "2026-01-05 09:15:00"
BASE_EPOCH = 1767580500.0


@dataclass
class KernelOutcome:
    unit_id: str
    verdict: str
    cases: int = 0
    passed: int = 0
    failed: int = 0
    mismatches: list[str] = field(default_factory=list)
    detail: str = ""
    details: tuple = ()


@dataclass
class KernelFuzz:
    unit_id: str
    verdict: str
    trials: int = 0
    failures: list[str] = field(default_factory=list)
    detail: str = ""
    details: tuple = ()


def _base_policy(**overrides: object) -> dict:
    policy: dict = {
        "max_position_qty": 1000.0,
        "max_order_qty": 500.0,
        "max_notional": None,
        "max_exposure_pct": None,
        "daily_loss_limit": None,
        "strategy_loss_limit": None,
        "allowed_symbols": (),
        "spread_limit_pct": None,
        "slippage_limit_pct": None,
        "require_fresh_data_seconds": None,
        "session_start": None,
        "session_end": None,
        "cooldown_seconds": 0.0,
        "max_orders_per_day": None,
    }
    policy.update(overrides)
    return policy


def _base_request(**overrides: object) -> dict:
    request: dict = {
        "intent_id": "case",
        "strategy_id": "s",
        "symbol": "TEST",
        "side": "BUY",
        "quantity": 10.0,
        "price": 100.0,
        "timestamp": BASE_TS,
        "position_qty": 0.0,
        "day_pnl": 0.0,
        "strategy_day_pnl": 0.0,
        "equity": 100000.0,
        "available_capital": 50000.0,
        "spread_pct": None,
        "data_age_seconds": None,
        "broker_healthy": True,
        "orders_today": 0,
        "last_order_epoch": None,
        "now_epoch": BASE_EPOCH,
    }
    request.update(overrides)
    return request


def build_risk_golden() -> list[dict]:
    """Deterministic golden cases: normal, boundary, invalid, disabled gates."""
    cases: list[dict] = []

    def add(kind: str, policy: dict, request: dict, halted: bool = False) -> None:
        cases.append({"kind": kind, "policy": policy, "request": request, "halted": halted})

    add("normal", _base_policy(), _base_request(intent_id="approve-1"))
    add("normal", _base_policy(), _base_request(intent_id="sell-1", side="SELL"))
    add("boundary", _base_policy(), _base_request(intent_id="b-qty", quantity=500.0))
    add(
        "boundary",
        _base_policy(max_notional=50000.0),
        _base_request(intent_id="b-not", quantity=500.0),
    )
    add("boundary", _base_policy(), _base_request(intent_id="b-pos", position_qty=990.0))
    add(
        "boundary",
        _base_policy(max_exposure_pct=50.0),
        _base_request(intent_id="b-exp", quantity=500.0, equity=100000.0),
    )
    add(
        "boundary",
        _base_policy(cooldown_seconds=60.0),
        _base_request(intent_id="b-cool", last_order_epoch=BASE_EPOCH - 60.0),
    )
    add(
        "boundary",
        _base_policy(max_orders_per_day=5),
        _base_request(intent_id="b-rate", orders_today=4),
    )
    add(
        "boundary",
        _base_policy(spread_limit_pct=0.1),
        _base_request(intent_id="b-spread", spread_pct=0.1),
    )
    add(
        "boundary",
        _base_policy(require_fresh_data_seconds=60.0),
        _base_request(intent_id="b-fresh", data_age_seconds=60.0),
    )
    add("invalid", _base_policy(), _base_request(intent_id="bad-qty", quantity=-5.0))
    add("invalid", _base_policy(), _base_request(intent_id="bad-price", price=0.0))
    add("invalid", _base_policy(), _base_request(intent_id="bad-cap", available_capital=0.0))
    add("failure", _base_policy(), _base_request(intent_id="halt-1"), halted=True)
    add(
        "failure",
        _base_policy(),
        _base_request(intent_id="unhealthy", broker_healthy=False),
    )
    add(
        "failure",
        _base_policy(allowed_symbols=("OTHER",)),
        _base_request(intent_id="symbol"),
    )
    add(
        "failure",
        _base_policy(session_start="09:15", session_end="15:30"),
        _base_request(intent_id="session", timestamp="2026-01-05 08:00:00"),
    )
    add(
        "failure",
        _base_policy(),
        _base_request(intent_id="clock", timestamp="2026-06-05 09:15:00"),
    )
    add(
        "failure",
        _base_policy(require_fresh_data_seconds=60.0),
        _base_request(intent_id="stale", data_age_seconds=61.0),
    )
    add(
        "failure",
        _base_policy(spread_limit_pct=0.1),
        _base_request(intent_id="wide", spread_pct=0.5),
    )
    add(
        "failure",
        _base_policy(cooldown_seconds=60.0),
        _base_request(intent_id="cooling", last_order_epoch=BASE_EPOCH - 10.0),
    )
    add(
        "failure",
        _base_policy(max_orders_per_day=5),
        _base_request(intent_id="rate", orders_today=5),
    )
    add(
        "failure",
        _base_policy(),
        _base_request(intent_id="pos", position_qty=995.0, quantity=500.0),
    )
    add(
        "failure",
        _base_policy(max_exposure_pct=10.0),
        _base_request(intent_id="expo", quantity=500.0, equity=100000.0),
    )
    add(
        "failure",
        _base_policy(daily_loss_limit=1000.0),
        _base_request(intent_id="dloss", day_pnl=-1000.01),
    )
    add(
        "boundary",
        _base_policy(daily_loss_limit=1000.0),
        _base_request(intent_id="dloss-ok", day_pnl=-1000.0),
    )
    add(
        "failure",
        _base_policy(strategy_loss_limit=500.0),
        _base_request(intent_id="sloss", strategy_day_pnl=-600.0),
    )
    add(
        "failure",
        _base_policy(max_order_qty=500.0),
        _base_request(intent_id="qty", quantity=500.01),
    )
    add(
        "failure",
        _base_policy(max_notional=10000.0),
        _base_request(intent_id="notional", quantity=500.0),
    )
    return cases


def decision_tuple(decision: object) -> tuple:
    checks = tuple((c.name, c.passed) for c in decision.checks)  # type: ignore[attr-defined]
    return (decision.approved, checks, tuple(decision.reasons))  # type: ignore[attr-defined]


def _load_oracle():  # type: ignore[no-untyped-def]
    import importlib

    try:
        return importlib.import_module("scripts.migration.agent.oracles.risk_engine_oracle_v1"), ""
    except ImportError as exc:
        return None, f"frozen oracle missing (run agent wire first): {exc}"


def _construct(policy_dict: dict, request_dict: dict):  # type: ignore[no-untyped-def]
    from risk import RiskPolicy, RiskRequest

    policy_kwargs = dict(policy_dict)
    if isinstance(policy_kwargs.get("allowed_symbols"), list):
        policy_kwargs["allowed_symbols"] = tuple(policy_kwargs["allowed_symbols"])
    return RiskPolicy(**policy_kwargs), RiskRequest(**request_dict)


def golden_tamper_check() -> tuple[bool, str]:
    """Builder output must equal persisted fixtures (hand-edits refused)."""
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "90_brain"
        / "migration"
        / "golden"
        / "risk_engine_evaluate.json"
    )
    if not path.is_file():
        return True, "no persisted fixtures yet"
    canonical = json.dumps(build_risk_golden(), indent=2, sort_keys=True) + "\n"
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if content != canonical:
        return False, "golden fixtures differ from deterministic builder — hand-edit refused"
    return True, "fixtures match builder"


def run_risk_parity() -> KernelOutcome:
    """Golden differential: frozen oracle vs production engine, exact match."""
    intact, tamper_detail = golden_tamper_check()
    if not intact:
        return KernelOutcome("risk.engine.evaluate", "FAIL", detail=tamper_detail)
    oracle, problem = _load_oracle()
    if oracle is None:
        return KernelOutcome("risk.engine.evaluate", "INCONCLUSIVE", detail=problem)
    from risk import RiskEngine

    mismatches: list[str] = []
    passed = 0
    cases = build_risk_golden()
    for index, case in enumerate(cases):
        policy, request = _construct(case["policy"], case["request"])
        if case["halted"]:
            from risk.kill_switch import KillSwitch

            switch = KillSwitch()
            switch.engage("golden-drill")
            engine = RiskEngine(policy, switch)
        else:
            engine = RiskEngine(policy)
        got = decision_tuple(engine.evaluate(request))
        want = decision_tuple(oracle.oracle_evaluate(policy, request, case["halted"], set()))
        if got == want:
            passed += 1
        else:
            mismatches.append(f"case={index} kind={case['kind']} want={want} got={got}")
    # Stateful duplicate slice: same intent twice must deny the second time.
    policy, request = _construct(_base_policy(), _base_request(intent_id="dup-1"))
    engine = RiskEngine(policy)
    first = decision_tuple(engine.evaluate(request))
    second = decision_tuple(engine.evaluate(request))
    oracle_seen: set = set()
    want_first = decision_tuple(oracle.oracle_evaluate(policy, request, False, oracle_seen))
    want_second = decision_tuple(oracle.oracle_evaluate(policy, request, False, oracle_seen))
    total = len(cases) + 1
    if (first, second) == (want_first, want_second) and not second[0]:
        passed += 1
    else:
        mismatches.append(f"duplicate slice mismatch: {first} {second}")
    verdict = "PASS" if not mismatches else "FAIL"
    return KernelOutcome(
        "risk.engine.evaluate", verdict, total, passed, total - passed, mismatches[:10]
    )


def run_risk_fuzz(trials: int = 150, seed: int = 777) -> KernelFuzz:
    oracle, problem = _load_oracle()
    if oracle is None:
        return KernelFuzz("risk.engine.evaluate", "INCONCLUSIVE", 0, [], problem)
    from risk import RiskEngine

    rng = random.Random(seed)
    failures: list[str] = []
    details: list[dict] = []
    for trial in range(trials):
        policy = _base_policy(
            max_order_qty=rng.choice([100.0, 500.0, 1000.0]),
            max_notional=rng.choice([None, 10000.0, 100000.0]),
            max_exposure_pct=rng.choice([None, 10.0, 90.0]),
            daily_loss_limit=rng.choice([None, 500.0, 5000.0]),
            spread_limit_pct=rng.choice([None, 0.05, 0.5]),
            require_fresh_data_seconds=rng.choice([None, 30.0, 120.0]),
            cooldown_seconds=rng.choice([0.0, 30.0, 300.0]),
            max_orders_per_day=rng.choice([None, 3, 50]),
            max_position_qty=rng.choice([100.0, 1000.0, 10000.0]),
        )
        request = _base_request(
            intent_id=f"fuzz-{trial}",
            side=rng.choice(["BUY", "SELL"]),
            quantity=rng.uniform(-10.0, 1200.0),
            price=rng.uniform(0.0, 500.0),
            position_qty=rng.uniform(-500.0, 500.0),
            equity=rng.choice([0.0, 50000.0, 200000.0]),
            available_capital=rng.choice([0.0, 10000.0]),
            spread_pct=rng.choice([None, 0.01, 0.4]),
            data_age_seconds=rng.choice([None, 5.0, 400.0]),
            orders_today=rng.randint(0, 60),
            last_order_epoch=rng.choice([None, BASE_EPOCH - rng.uniform(0, 600.0)]),
            day_pnl=rng.uniform(-6000.0, 1000.0),
            strategy_day_pnl=rng.uniform(-2000.0, 500.0),
        )
        halted = rng.random() < 0.1
        policy_o, request_o = _construct(policy, request)
        if halted:
            from risk.kill_switch import KillSwitch

            switch = KillSwitch()
            switch.engage("fuzz-drill")
            got = decision_tuple(RiskEngine(policy_o, switch).evaluate(request_o))
        else:
            got = decision_tuple(RiskEngine(policy_o).evaluate(request_o))
        want = decision_tuple(oracle.oracle_evaluate(policy_o, request_o, halted, set()))
        import hashlib as _hashlib
        import json as _json

        fingerprint = _hashlib.sha256(
            _json.dumps({"p": policy, "r": request}, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        match = got == want
        details.append({"fp": fingerprint, "match": match})
        if not match:
            failures.append(f"trial={trial} fp={fingerprint} mismatch: want={want} got={got}")
    verdict = "FAIL" if failures else "PASS"
    return KernelFuzz("risk.engine.evaluate", verdict, trials, failures[:10], "", tuple(details))


def run_risk_shadow(count: int = 60) -> KernelOutcome:
    """Shadow differential on random inputs; telemetry recorded by caller."""
    fuzz = run_risk_fuzz(trials=count, seed=5150)
    return KernelOutcome(
        "risk.engine.evaluate",
        fuzz.verdict,
        fuzz.trials,
        fuzz.trials - len(fuzz.failures),
        len(fuzz.failures),
        fuzz.failures[:10],
        fuzz.detail,
        fuzz.details,
    )


__all__ = [
    "KernelOutcome",
    "KernelFuzz",
    "build_risk_golden",
    "decision_tuple",
    "run_risk_parity",
    "run_risk_fuzz",
    "run_risk_shadow",
]
