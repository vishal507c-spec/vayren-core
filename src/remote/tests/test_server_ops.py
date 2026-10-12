"""Tests for Server Operations (remote.server_ops and remote.ops_cli)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import pytest

from remote.server_ops import (
    APPROVED_OPERATIONS,
    execute_server_op,
    scrub_sensitive_text,
    validate_op,
)


def test_allowed_operations_list():
    assert "health" in APPROVED_OPERATIONS
    assert "status" in APPROVED_OPERATIONS
    assert "logs" in APPROVED_OPERATIONS
    assert "backup" in APPROVED_OPERATIONS
    assert "deploy" in APPROVED_OPERATIONS
    assert "restart" in APPROVED_OPERATIONS
    assert "rm" not in APPROVED_OPERATIONS
    assert "reboot" not in APPROVED_OPERATIONS


def test_validate_op():
    assert validate_op("health") is True
    assert validate_op("status") is True
    assert validate_op("logs") is True
    assert validate_op("backup") is True
    assert validate_op("deploy") is True
    assert validate_op("restart") is True

    assert validate_op("malicious_op") is False
    assert validate_op("rm -rf /") is False
    assert validate_op("exec") is False
    assert validate_op("") is False


def test_scrub_sensitive_text():
    raw_text = (
        "Connected with token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret and "
        "app_id: FYERS_APP_1234567890\n"
        "Authorization: Bearer abcd1234efgh5678ijkl90\n"
        "password: supersecretpassword123\n"
        "api_secret = qwertyuiopasdfghjklzxcvbnm\n"
        "Normal log line without secrets: Order placed for RELIANCE at 2500.0"
    )
    scrubbed = scrub_sensitive_text(raw_text)

    assert "supersecretpassword123" not in scrubbed
    assert "[REDACTED]" in scrubbed
    assert "RELIANCE at 2500.0" in scrubbed


def test_execute_op_health(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op("health", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir))
    assert res["ok"] is True
    assert "ec2_status" in res
    assert "gateway_status" in res
    assert "trading_worker_status" in res
    assert "memory" in res
    assert "disk" in res


def test_execute_op_status(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op("status", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir))
    assert res["ok"] is True
    assert "service" in res
    assert "trading_worker" in res


def test_execute_op_logs(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op("logs", params={"lines": 10}, data_dir=str(data_dir), strategy_dir=str(strat_dir))
    # On Windows without systemd/journalctl, it safely catches exception and returns ok=False with error or empty logs
    assert "logs" in res or "error" in res


def test_execute_op_backup(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "test.json").write_text('{"key": "value"}', encoding="utf-8")

    res = execute_server_op("backup", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir))
    assert res["ok"] is True
    assert "archive_path" in res
    assert "sha256" in res
    assert Path(res["archive_path"]).exists()
    assert len(res["sha256"]) == 64
    assert res["status"] == "VERIFIED"


def test_execute_op_disallowed(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op("drop_tables", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir))
    assert res["ok"] is False
    assert "not in the approved allowlist" in res["error"]


def test_execute_op_restart_requires_confirm(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op("restart", params={"confirmed": False}, data_dir=str(data_dir), strategy_dir=str(strat_dir))
    assert res["ok"] is False
    assert "requires confirmed=True" in res["error"]


def test_execute_op_deploy_requires_confirm(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op("deploy", params={"confirmed": False}, data_dir=str(data_dir), strategy_dir=str(strat_dir))
    assert res["ok"] is False
    assert "requires confirmed=True" in res["error"]
