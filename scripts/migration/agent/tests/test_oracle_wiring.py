"""Oracle + wiring: frozen semantics and surgical production patch."""

from __future__ import annotations

import ast

from risk import RiskEngine, RiskPolicy, RiskRequest

from scripts.migration.agent import generator_oracle as oracle_gen
from scripts.migration.agent import wiring as _wiring
from scripts.migration.agent.analyzer import extract_risk_spec, pristine_source

PRISTINE_ENGINE = pristine_source("07_risk/risk/engine.py")
PRISTINE_LIB = pristine_source("rust/vayren-core/src/lib.rs")


def test_oracle_matches_live_engine() -> None:
    source = PRISTINE_ENGINE
    digest = oracle_gen.snapshot_hash(source)
    oracle_src = oracle_gen.render_oracle_module(source, digest)
    namespace: dict = {}
    exec(compile(oracle_src, "oracle", "exec"), namespace)
    oracle = namespace["oracle_evaluate"]
    policy = RiskPolicy(max_notional=20000.0, cooldown_seconds=30.0)
    request = RiskRequest(
        intent_id="o1",
        strategy_id="s",
        symbol="TEST",
        side="BUY",
        quantity=100.0,
        price=150.0,
        timestamp="2026-01-05 09:15:00",
        now_epoch=1767580500.0,
        last_order_epoch=1767580400.0,
    )
    want = oracle(policy, request, False, set())
    got = RiskEngine(policy).evaluate(request)
    assert want.approved == got.approved
    assert [(c.name, c.passed) for c in want.checks] == [(c.name, c.passed) for c in got.checks]
    assert tuple(want.reasons) == tuple(got.reasons)


def test_engine_patch_is_surgical() -> None:
    spec = extract_risk_spec(PRISTINE_ENGINE).to_dict()
    names = [c["name"] for c in spec["checks"]]
    bits = [f"BIT_{name.upper()}" for name in names]
    source = PRISTINE_ENGINE
    patched = _wiring.patch_engine(source, names, bits)
    tree = ast.parse(patched)
    evaluate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate"
    )
    order: list[str] = []
    positioned: list[tuple[int, int, str]] = []
    for node in ast.walk(evaluate):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "_check":
            name = node.args[1]
            assert isinstance(name, ast.Constant) and isinstance(name.value, str)
            positioned.append((node.lineno, node.col_offset, name.value))
    positioned.sort()
    order = [name for _, _, name in positioned]
    # First occurrence per check follows the contract order; orchestration
    # if/else arms legitimately keep both call sites (28 total, 18 names).
    firsts: list[str] = []
    for name in order:
        if name not in firsts:
            firsts.append(name)
    assert firsts == extract_risk_spec(PRISTINE_ENGINE).to_dict()["check_order"]
    assert len(order) == 28
    assert "scripts.migration" not in patched
    assert "_risk_check_mask_for(policy, request)" in patched
    for bit in bits:
        assert patched.count(bit) >= 1, bit
    # Aliases survive only while referenced (details recompute from them by
    # design); none may be dead (ruff F841 would fail the gate).
    loads = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    for node in ast.walk(evaluate):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in (
                "notional",
                "direction",
                "new_position",
                "exposure",
                "fresh",
                "tight",
                "cooled",
                "under",
            ):
                assert target.id in loads, target.id
    assert "within_session(" in patched
    for lineno, line in enumerate(patched.splitlines(), start=1):
        assert len(line) <= 100, (lineno, line)


def test_lib_patch_and_bridge_shapes() -> None:
    spec = extract_risk_spec(PRISTINE_ENGINE).to_dict()
    names = [c["name"] for c in spec["checks"]]
    lib = _wiring.patch_lib_rs(spec, PRISTINE_LIB)
    assert "pub mod risk;" in lib
    assert "vy_risk_kernel" in lib
    assert "catch_unwind" in lib
    bridge = _wiring.render_bridge(spec, names)
    ast.parse(bridge)
    assert "check_mask_for" in bridge
    assert bridge.count("BIT_") >= len(names) * 2
    for lineno, line in enumerate(bridge.splitlines(), start=1):
        assert len(line) <= 100, (lineno, line)
