"""--broker CLI behavior (M4): registry-resolved, fail-closed, precedence."""

from __future__ import annotations

from pathlib import Path

import pytest
from broker.selection import SelectionError

from app import establish_selection, parse_args


def test_broker_flag_parses() -> None:
    assert parse_args(["--broker", "sandbox"]).broker == "sandbox"
    assert parse_args([]).broker is None


def test_valid_broker_becomes_authoritative(tmp_path: Path) -> None:
    args = parse_args(["--broker", "sandbox", "--data-dir", str(tmp_path)])
    service = establish_selection(args)
    assert service.current().name == "sandbox"
    assert service.current().reason == "user-selected"


def test_unknown_broker_raises_selection_error(tmp_path: Path) -> None:
    args = parse_args(["--broker", "ghost", "--data-dir", str(tmp_path)])
    with pytest.raises(SelectionError, match="unknown broker"):
        establish_selection(args)


def test_invalid_broker_main_exits_2(tmp_path: Path, capsys: object) -> None:
    from app import App

    rc = App.main(["--broker", "ghost", "--data-dir", str(tmp_path)])
    assert rc == 2
    out = str(capsys.readouterr().out)  # type: ignore[union-attr]
    assert "BROKER SELECTION FAILED" in out
    assert "unknown broker" in out


def test_cli_selection_precedence_over_persisted(tmp_path: Path) -> None:
    # Persist an explicit choice first...
    base = parse_args(["--broker", "paper", "--data-dir", str(tmp_path)])
    establish_selection(base).current()
    # ...then a CLI selection wins for that invocation.
    args = parse_args(["--broker", "sandbox", "--data-dir", str(tmp_path)])
    assert establish_selection(args).current().name == "sandbox"


def test_no_broker_flag_uses_persisted_or_default(tmp_path: Path) -> None:
    args = parse_args(["--data-dir", str(tmp_path)])
    service = establish_selection(args)
    # No file yet → compatibility default (visible, not a fake user choice).
    assert service.current().name == "zerodha"
    assert "compatibility-default" in service.current().reason


def test_check_live_with_broker_stays_fail_closed(tmp_path: Path, capsys: object) -> None:
    """--broker sandbox --check-live: selection is shown, gates still rule."""
    from app.services.paper_service import run_check_live

    args = parse_args(["--broker", "sandbox", "--check-live", "--data-dir", str(tmp_path)])
    service = establish_selection(args)
    rc = run_check_live(args, selection_service=service)
    assert rc == 0
    out = str(capsys.readouterr().out)  # type: ignore[union-attr]
    assert "LIVE NOT READY" in out or "LIVE READY" not in out
