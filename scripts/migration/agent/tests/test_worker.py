"""Background worker: lock discipline, dirty-tree guard, Slint exclusion."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.migration.agent import worker
from scripts.migration.agent.models import AgentOutcome


def _lock_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "worker.lock"
    monkeypatch.setenv("VAYREN_MIGRATION_LOCK", str(target))
    return target


def test_acquire_and_reacquire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _lock_env(monkeypatch, tmp_path)
    ok, _ = worker.acquire_lock()
    assert ok
    ok2, note = worker.acquire_lock()
    assert not ok2
    assert "live pid" in note
    worker.release_lock()
    ok3, _ = worker.acquire_lock()
    assert ok3
    worker.release_lock()


def test_stale_lock_is_stolen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = _lock_env(monkeypatch, tmp_path)
    target.write_text(
        json.dumps({"pid": 999999999, "started": "long-ago", "started_epoch": 1.0}),
        encoding="utf-8",
    )
    ok, note = worker.acquire_lock()
    assert ok
    assert "stale" in note
    worker.release_lock()


def test_run_background_skips_dirty_and_ui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _lock_env(monkeypatch, tmp_path)
    calls: list[str] = []

    def fake_run(unit_id: str) -> AgentOutcome:
        calls.append(unit_id)
        return AgentOutcome(unit_id, "BLOCKED", (), ())

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=60)
    subprocess.run(["git", "config", "user.email", "w@test"], cwd=repo, check=True, timeout=60)
    subprocess.run(["git", "config", "user.name", "w"], cwd=repo, check=True, timeout=60)
    dirty_rel = "07_risk/risk/engine.py"
    dirty_file = repo / dirty_rel
    dirty_file.parent.mkdir(parents=True, exist_ok=True)
    dirty_file.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, timeout=60)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True, timeout=60)
    dirty_file.write_text("x = 2\n", encoding="utf-8")
    qt_file = repo / "04_chart" / "chart" / "models" / "chart_viewport.py"
    qt_file.parent.mkdir(parents=True, exist_ok=True)
    qt_file.write_text("from PySide6.QtCore import QRect\n", encoding="utf-8")

    outcome = worker.run_background(
        units=["risk.engine.evaluate", "chart.viewport.math", "backtest.metrics.sharpe"],
        actor="test",
        root=repo,
        runner=fake_run,
    )
    assert outcome.processed == 1
    assert calls == ["backtest.metrics.sharpe"]
    assert any("risk.engine.evaluate" in skip and "uncommitted" in skip for skip in outcome.skipped)
    assert any("viewport" in skip and "Slint" in skip for skip in outcome.skipped)


def test_run_background_refuses_when_locked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _lock_env(monkeypatch, tmp_path)
    ok, _ = worker.acquire_lock()
    assert ok
    try:
        outcome = worker.run_background(units=["backtest.metrics.sharpe"], actor="test")
        assert outcome.processed == 0
        assert "live pid" in outcome.lock_note
    finally:
        worker.release_lock()


def test_unit_files_cover_source_target_bridge() -> None:
    files = worker.unit_files("risk.engine.evaluate")
    assert "07_risk/risk/engine.py" in files
    assert "rust/vayren-core/src/risk.rs" in files
    assert "07_risk/risk/native_checks.py" in files


def test_is_ui_unit() -> None:
    # chart.viewport.math source is Qt-coupled in the real tree.
    assert worker.is_ui_unit("chart.viewport.math") is True
    assert worker.is_ui_unit("risk.engine.evaluate") is False
    assert worker.is_ui_unit("no.such.unit") is False


def test_lock_path_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = _lock_env(monkeypatch, tmp_path)
    assert worker.lock_path() == target
