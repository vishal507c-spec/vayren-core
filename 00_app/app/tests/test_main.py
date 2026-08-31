"""App argument parsing tests."""

import pytest

from app import DEFAULT_DATA_DIR, parse_args


def test_defaults() -> None:
    args = parse_args([])
    assert args.data_dir == DEFAULT_DATA_DIR
    assert args.limit is None
    assert args.log_level == "INFO"


def test_explicit_values() -> None:
    args = parse_args(["--data-dir", "D:/Market", "--limit", "100", "--log-level", "DEBUG"])
    assert args.data_dir == "D:/Market"
    assert args.limit == 100
    assert args.log_level == "DEBUG"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAYREN_DATA_DIR", "D:/env-data")
    args = parse_args([])
    assert args.data_dir == "D:/env-data"


def test_cli_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAYREN_DATA_DIR", "D:/env-data")
    args = parse_args(["--data-dir", "D:/cli-data"])
    assert args.data_dir == "D:/cli-data"


def test_invalid_limit_rejected() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--limit", "not-a-number"])
