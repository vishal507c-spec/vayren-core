"""App argument parsing tests."""

import pytest

from app import parse_args


def test_defaults() -> None:
    args = parse_args([])
    assert args.symbol == "SPY"
    assert args.db == "data/vayren.db"
    assert args.limit == 5000
    assert args.log_level == "INFO"


def test_explicit_values() -> None:
    args = parse_args(
        ["--symbol", "AAPL", "--db", "custom.db", "--limit", "100", "--log-level", "DEBUG"]
    )
    assert args.symbol == "AAPL"
    assert args.db == "custom.db"
    assert args.limit == 100
    assert args.log_level == "DEBUG"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAYREN_SYMBOL", "QQQ")
    monkeypatch.setenv("VAYREN_DB", "env.db")
    args = parse_args([])
    assert args.symbol == "QQQ"
    assert args.db == "env.db"


def test_cli_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAYREN_SYMBOL", "QQQ")
    args = parse_args(["--symbol", "AAPL"])
    assert args.symbol == "AAPL"


def test_invalid_limit_rejected() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--limit", "not-a-number"])
