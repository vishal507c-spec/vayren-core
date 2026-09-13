"""Validator: no false PASS under any uncertainty."""

from __future__ import annotations

from scripts.migration import manifest_store, validator
from scripts.migration.models import (
    IntegrationSummary,
    MigrationManifest,
    ParitySummary,
    ShadowSummary,
)


def _manifest(**overrides: object) -> MigrationManifest:
    base = {
        "migration_id": "vayren-test-v1",
        "unit": "backtest.metrics.drawdown",
        "source": "06_backtest/backtest/engine/metrics.py",
        "target": "rust/vayren-core/src/metrics.rs",
        "dependencies": (),
        "state": "PARITY_TESTING",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return MigrationManifest(**base)  # type: ignore[arg-type]


def test_missing_hashes_in_advanced_state_is_stale() -> None:
    manifest = _manifest(state="PARITY_VERIFIED")
    verdict, _ = validator.check_unit(manifest, {})
    assert verdict == "STALE"


def test_false_canonical_claim_fails() -> None:
    manifest = _manifest(
        state="RUST_CANONICAL",
        source_hash="deadbeef",
        target_hash="deadbeef",
        parity=ParitySummary(status="PASS", cases=10, passed=10),
        shadow=ShadowSummary(status="PASS", comparisons=60),
        integration=IntegrationSummary(
            status="PASS", production_uses_rust=True, python_still_authoritative=False
        ),
    )
    verdict, _ = validator.check_unit(manifest, {})
    assert verdict in ("FAIL", "STALE")


def test_early_states_never_pass() -> None:
    for state in ("DISCOVERED", "ANALYZED", "PLANNED", "BLOCKED", "READY"):
        verdict, _ = validator.check_unit(_manifest(state=state), {})
        assert verdict in ("BLOCKED", "INCONCLUSIVE"), state


def test_secret_in_evidence_fails() -> None:
    manifest = _manifest(
        state="PARITY_TESTING",
        evidence=("parity run with api_key=ABC123",),
    )
    verdict, reasons = validator.check_unit(manifest, {})
    assert verdict == "FAIL"
    assert any("secret" in reason for reason in reasons)


def test_production_rust_path_proven_for_migrated_kernels() -> None:
    for unit in (
        "execution.order_lifecycle",
        "backtest.metrics.drawdown",
        "market.timeframe.aggregate",
        "market.timeframe.mode",
    ):
        ok, _ = validator.production_uses_rust(unit)
        assert ok, unit


def test_gate_reports_known_units_only() -> None:
    code, report = validator.validate_all()
    assert report["units"] >= 12
    assert code in (0, 1)
    _ = manifest_store.load_all()
