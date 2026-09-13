"""Gates: promotion, rollback, quarantine and finalization refuse unsafe moves."""

from __future__ import annotations

import pytest

from scripts.migration import finalizer, manifest_store, promotion, quarantine, rollback


def _with_tmp_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import scripts.migration.config as config

    target = tmp_path / "manifests"
    target.mkdir()
    monkeypatch.setattr(config, "MANIFESTS_DIR", target)
    monkeypatch.setattr(manifest_store, "MANIFESTS_DIR", target)


def test_promote_refuses_non_integrated(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _with_tmp_store(monkeypatch, tmp_path)
    ok, message = promotion.promote("backtest.metrics.drawdown")
    assert not ok
    assert "INTEGRATED" in message or "unknown" in message


def test_rollback_refuses_python_authority(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _with_tmp_store(monkeypatch, tmp_path)
    ok, _ = rollback.rollback("backtest.metrics.drawdown", "test")
    assert not ok


def test_quarantine_refuses_python_authority(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _with_tmp_store(monkeypatch, tmp_path)
    ok, _ = quarantine.quarantine("risk.engine.evaluate")
    assert not ok


def test_finalize_refuses_unready(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _with_tmp_store(monkeypatch, tmp_path)
    ok, message = finalizer.finalize("risk.engine.evaluate")
    assert not ok
    assert message


def test_removal_readiness_lists_missing_conditions() -> None:
    manifests = manifest_store.load_all()
    manifest = manifests["chart.viewport.math"]
    states = {uid: m.state for uid, m in manifests.items()}
    ready, missing = finalizer.removal_readiness(manifest, states)
    assert not ready
    assert missing
