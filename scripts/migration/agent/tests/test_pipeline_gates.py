"""Sandbox mechanics, repair honesty and pipeline gate discipline."""

from __future__ import annotations

from scripts.migration.agent import repair as _repair
from scripts.migration.agent import sandbox as _sandbox
from scripts.migration.agent import wiring as _wiring
from scripts.migration.agent.pipeline import run_unit
from scripts.migration.hashing import sha256_file


def test_repair_fixes_cast_only() -> None:
    source = "pub fn f(x: i64) -> f64 {\n    x\n}\n"
    log = (
        "error[E0308]: mismatched types\n --> src/risk_kernel.rs:2:5\n  expected `f64`, found `i64`"
    )
    fixed, note = _repair.repair_candidate(source, log)
    assert fixed is not None and "as f64" in fixed
    assert "f64" in note


def test_repair_refuses_semantic_failures() -> None:
    fixed, _ = _repair.repair_candidate("fn f() {}", "test result: FAILED. 0 passed; 1 failed")
    assert fixed is None
    fixed, _ = _repair.repair_candidate("fn f() {}", "panicked at 'index out of bounds'")
    assert fixed is None
    fixed, _ = _repair.repair_candidate("fn f() {}", "cannot find value `foo` in this scope")
    assert fixed is None


def test_sandbox_proves_trivial_kernel() -> None:
    kernel = "pub fn answer() -> u32 {\n    42\n}\n"
    harness = "#[test]\nfn t() {\n    assert_eq!(kernel_probe::risk_kernel::answer(), 42);\n}\n"
    result = _sandbox.run_sandbox_gate(kernel, harness)
    assert result.ok, result.log_tail
    assert result.attempts == 1


def test_dry_run_writes_nothing() -> None:
    before = sha256_file(_wiring.ENGINE_PY)
    outcome = run_unit("risk.engine.evaluate", dry_run=True)
    assert outcome.verdict == "BLOCKED"
    assert sha256_file(_wiring.ENGINE_PY) == before


def test_unknown_unit_fails_cleanly() -> None:
    outcome = run_unit("no.such.unit", dry_run=True)
    assert outcome.verdict == "FAILED"
