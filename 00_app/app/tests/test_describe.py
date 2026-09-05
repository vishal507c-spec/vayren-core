"""Tests for `vayren --describe` (read-only architecture snapshot)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtWidgets import QApplication

from app import App, parse_args
from app.bootstrap.bootstrap import Bootstrap
from app.describe import describe_architecture
from app.tests.conftest import seed_symbol_directory


def _data_dir(tmp_path: Path) -> Path:
    return seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 50, "BPCL": 30})


def test_describe_flag_parses() -> None:
    assert parse_args(["--describe"]).describe is True
    assert parse_args([]).describe is False


def test_describe_matches_live_snapshot(qt_app: QApplication, tmp_path: Path) -> None:
    assert qt_app is not None  # fixture provides the QApplication widgets require
    data_dir = _data_dir(tmp_path)
    rendered = describe_architecture(data_dir, 50)
    expected = Bootstrap(data_dir=data_dir, limit=50).system_model.snapshot().render()
    assert rendered == expected
    assert rendered.startswith("system model - components:")
    assert "provides:" in rendered


def test_describe_main_returns_zero_without_event_loop(
    qt_app: QApplication, tmp_path: Path, capsys: object
) -> None:
    assert qt_app is not None  # fixture provides the QApplication widgets require
    data_dir = _data_dir(tmp_path)
    rc = App.main(["--describe", "--data-dir", str(data_dir), "--limit", "50"])
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "system model - components:" in out
