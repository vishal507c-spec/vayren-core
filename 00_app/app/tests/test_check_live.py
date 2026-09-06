"""--check-live diagnostic tests: never places orders, reports gates honestly."""

from pathlib import Path

import pytest
from execution.broker.gates import format_gates_report

from app import App, parse_args
from app.services.paper_service import PaperRunConfig, PaperService, run_check_live
from app.tests.test_paper import _seed


def test_check_live_flag_parses() -> None:
    assert parse_args(["--check-live"]).check_live is True
    assert parse_args([]).check_live is False


def test_check_live_reports_not_ready_without_config(tmp_path: Path) -> None:
    import argparse

    data_dir, strategy_dir = _seed(tmp_path)
    args = argparse.Namespace(
        data_dir=str(data_dir),
        strategy_dir=str(strategy_dir),
        paper_symbol=None,
        paper_strategy=None,
        paper_timeframe=None,
        limit=None,
    )
    assert run_check_live(args) == 0


def test_check_live_places_no_orders(tmp_path: Path) -> None:
    data_dir, strategy_dir = _seed(tmp_path)
    service = PaperService(PaperRunConfig(data_dir=data_dir, strategy_dir=strategy_dir))
    service.prepare()
    journal = service._session_dir / "journal.jsonl"
    # prepare() alone writes no journal: nothing ran, nothing ordered
    assert not journal.is_file()
    assert service._session is not None
    assert len(service._session.engine._orders) == 0


def test_check_live_via_app_main(tmp_path: Path, capsys: object) -> None:
    data_dir, strategy_dir = _seed(tmp_path)
    rc = App.main(
        [
            "--check-live",
            "--data-dir",
            str(data_dir),
            "--strategy-dir",
            str(strategy_dir),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "LIVE NOT READY" in out
    assert "BROKER_ADAPTER_READY" in out
    assert "CREDENTIALS_READY" in out
    assert "ACCOUNT_CONFIRMED" in out
    assert "RISK_CONFIGURATION_VALID" in out
    assert "EXECUTION_SAFETY_ENABLED" in out
    assert "STRATEGY_REQUIREMENTS" in out
    assert "DATA_REQUIREMENTS" in out


def test_check_live_missing_strategy_reports_preparation(tmp_path: Path, capsys: object) -> None:
    data_dir, _ = _seed(tmp_path)
    rc = App.main(
        [
            "--check-live",
            "--data-dir",
            str(data_dir),
            "--strategy-dir",
            str(tmp_path / "empty-strategies"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "LIVE NOT READY" in out
    assert "PREPARATION" in out


def test_gates_report_never_leaks_secrets() -> None:
    from execution.broker.credentials import BrokerCredentials, EnvCredentialStore
    from execution.broker.gates import evaluate_live_gates

    store = EnvCredentialStore(env={"VAYREN_BROKER_API_KEY": "topsecret-1"})
    creds = BrokerCredentials(account_id="a", environment="live", key_refs=("API_KEY",))
    report = evaluate_live_gates(adapter=None, credentials=creds, credential_store=store)
    text = format_gates_report(report)
    assert "topsecret-1" not in text
    assert "LIVE NOT READY" in text


def test_check_live_cli_output_never_leaks_secrets(
    tmp_path: Path, capsys: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, strategy_dir = _seed(tmp_path)
    monkeypatch.setenv("VAYREN_BROKER_API_KEY", "zz-cli-secret-1")
    monkeypatch.setenv("VAYREN_BROKER_API_SECRET", "zz-cli-secret-2")
    monkeypatch.setenv("VAYREN_BROKER_ACCOUNT_ID", "live-acct-9")
    rc = App.main(
        [
            "--check-live",
            "--data-dir",
            str(data_dir),
            "--strategy-dir",
            str(strategy_dir),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "zz-cli-secret-1" not in out
    assert "zz-cli-secret-2" not in out
    assert "LIVE NOT READY" in out
