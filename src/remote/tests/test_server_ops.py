"""Tests for Server Operations (remote.server_ops and remote.ops_cli)."""

from __future__ import annotations

from pathlib import Path

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

    res = execute_server_op(
        "health", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
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

    res = execute_server_op(
        "status", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is True
    assert "service" in res
    assert "trading_worker" in res


def test_execute_op_logs(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op(
        "logs", params={"lines": 10}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    # On Windows without journalctl, it catches exception and returns ok=False or logs
    assert "logs" in res or "error" in res


def test_execute_op_backup(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "test.json").write_text('{"key": "value"}', encoding="utf-8")

    res = execute_server_op(
        "backup", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
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

    res = execute_server_op(
        "drop_tables", params={}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is False
    assert "not in approved allowlist" in res["error"]


def test_execute_op_restart_requires_confirm(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op(
        "restart", params={"confirmed": False}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is False
    assert "requires confirmed=True" in res["error"]


def test_restart_failure_points_to_ssh_without_new_privilege(tmp_path, monkeypatch):
    """Sandboxed gateway can never sudo-restart itself: with no helper
    installed the error must name the sandbox and the operator SSH command,
    never echo sudo's stderr."""
    import subprocess

    from remote import server_ops

    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    class _Proc:
        returncode = 1
        stderr = "sudo: The 'no new privileges' flag is set"

    monkeypatch.setattr(server_ops, "_get_worker_runtime_status", lambda *_args: {"running": False})
    monkeypatch.setattr(server_ops, "_no_new_privileges", lambda: True)
    monkeypatch.setattr(server_ops, "_restart_helper_available", lambda _dir: False)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Proc())
    res = execute_server_op(
        "restart", params={"confirmed": True}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is False
    assert "RESTART_UNAVAILABLE" in res["error"]
    assert "NoNewPrivileges=1" in res["error"]
    assert "sudo systemctl restart vayren-remote.service" in res["error"]
    assert "no new privileges' flag" not in res["error"]


def test_restart_direct_sudo_path_verifies_service_active(tmp_path, monkeypatch):
    """Operator CLI/SSH path: a successful sudo restart is verified active."""
    import subprocess

    from remote import server_ops

    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    class _Proc:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(server_ops, "_get_worker_runtime_status", lambda *_args: {"running": False})
    monkeypatch.setattr(server_ops, "_no_new_privileges", lambda: False)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Proc())
    monkeypatch.setattr(
        server_ops,
        "_get_service_status",
        lambda name: {"name": name, "active": True, "status": "ACTIVE", "main_pid": 123},
    )
    res = execute_server_op(
        "restart", params={"confirmed": True}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is True
    assert res["status"] == "RESTARTED"
    assert res["service"]["active"] is True


def test_restart_direct_sudo_denied_reports_real_cause(tmp_path, monkeypatch):
    """Non-sandboxed denial (missing sudo rule): the real sudo cause is the
    error, with the operator SSH command as the fallback."""
    import subprocess

    from remote import server_ops

    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    class _Proc:
        returncode = 1
        stderr = "sudo: a password is required"

    monkeypatch.setattr(server_ops, "_get_worker_runtime_status", lambda *_args: {"running": False})
    monkeypatch.setattr(server_ops, "_no_new_privileges", lambda: False)
    monkeypatch.setattr(server_ops, "_restart_helper_available", lambda _dir: False)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Proc())
    res = execute_server_op(
        "restart", params={"confirmed": True}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is False
    assert "RESTART_UNAVAILABLE" in res["error"]
    # The sudo cause is surfaced (secret scrubber may redact the value, the
    # cause stays readable).
    assert "direct restart failed" in res["error"]
    assert "required" in res["error"]
    assert "sudo systemctl restart vayren-remote.service" in res["error"]


def test_restart_blocked_while_session_running(tmp_path, monkeypatch):
    """A RUNNING live session blocks restart before any privilege is used."""
    import subprocess

    from remote import server_ops

    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    def _boom(*_args, **_kwargs):
        raise AssertionError("no restart may be attempted while a session is RUNNING")

    monkeypatch.setattr(server_ops, "_get_worker_runtime_status", lambda *_args: {"running": True})
    monkeypatch.setattr(subprocess, "run", _boom)
    res = execute_server_op(
        "restart", params={"confirmed": True}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is False
    assert "RESTART_BLOCKED" in res["error"]


def test_sandboxed_restart_requests_helper_marker(tmp_path, monkeypatch):
    """Sandboxed gateway + installed helper: one audit-ready request marker,
    then the root path unit restarts the service. No sudo is attempted."""
    import json
    import subprocess

    from remote import server_ops

    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)
    path_unit = tmp_path / "vayren-remote-restart.path"
    path_unit.write_text("[Path]\nPathExists=x\n", encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise AssertionError("sudo must not run inside the sandbox")

    monkeypatch.setattr(server_ops, "_get_worker_runtime_status", lambda *_args: {"running": False})
    monkeypatch.setattr(server_ops, "_no_new_privileges", lambda: True)
    monkeypatch.setattr(server_ops, "RESTART_HELPER_PATH_UNIT", path_unit)
    monkeypatch.setattr(server_ops, "RESTART_HELPER_DATA_DIR", data_dir)
    monkeypatch.setattr(server_ops, "RESTART_REQUEST_DELAY_S", 0.0)
    monkeypatch.setattr(subprocess, "run", _boom)
    res = execute_server_op(
        "restart",
        params={
            "confirmed": True,
            # Unauthorized/ignored fields: the helper payload is fixed and
            # must never echo caller-supplied executable input.
            "unit": "evil.service",
            "command": "rm -rf /",
            "path": "/etc/shadow",
        },
        data_dir=str(data_dir),
        strategy_dir=str(strat_dir),
        caller="operator:test-conn",
    )
    assert res["ok"] is True
    assert res["status"] == "RESTART_REQUESTED"
    marker = data_dir / "restart.request"
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["operation"] == "restart"
    assert payload["unit"] == "vayren-remote.service"
    assert payload["caller"] == "operator:test-conn"
    assert "command" not in payload and "path" not in payload
    assert not (data_dir / "restart.request.tmp").exists()


def test_helper_availability_requires_canonical_data_dir(tmp_path, monkeypatch):
    """The helper is usable only for the exact data directory it watches;
    any other gateway fails closed instead of writing a dead marker."""
    from remote import server_ops

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    other_dir = tmp_path / "other"
    other_dir.mkdir(parents=True, exist_ok=True)
    path_unit = tmp_path / "vayren-remote-restart.path"
    path_unit.write_text("[Path]\nPathExists=x\n", encoding="utf-8")

    monkeypatch.setattr(server_ops, "RESTART_HELPER_PATH_UNIT", path_unit)
    monkeypatch.setattr(server_ops, "RESTART_HELPER_DATA_DIR", data_dir)
    assert server_ops._restart_helper_available(str(data_dir)) is True
    assert server_ops._restart_helper_available(str(other_dir)) is False

    path_unit.unlink()
    assert server_ops._restart_helper_available(str(data_dir)) is False


def test_helper_restart_publishes_marker_after_delay(tmp_path):
    """Delayed publish: the command acknowledgement can leave the socket
    before systemd restarts the gateway (the marker appears afterwards)."""
    import json
    import time

    from remote import server_ops

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    res = server_ops._request_helper_restart(str(data_dir), "operator:cli", delay_s=0.05)
    assert res["ok"] is True
    marker = data_dir / "restart.request"
    deadline = time.monotonic() + 3.0
    while not marker.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["caller"] == "operator:cli"


def test_execute_op_deploy_requires_confirm(tmp_path):
    data_dir = tmp_path / "data"
    strat_dir = tmp_path / "strategies"
    data_dir.mkdir(parents=True, exist_ok=True)
    strat_dir.mkdir(parents=True, exist_ok=True)

    res = execute_server_op(
        "deploy", params={"confirmed": False}, data_dir=str(data_dir), strategy_dir=str(strat_dir)
    )
    assert res["ok"] is False
    assert "requires confirmed=True" in res["error"]


def test_execute_op_check_code(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    s = tmp_path / "strategies"
    s.mkdir()

    res = execute_server_op(
        op_name="check_code",
        params={"force": True},
        data_dir=str(d),
        strategy_dir=str(s),
    )
    assert res["ok"] is True
    assert res["status"] in ("SERVER CODE: UPDATED", "SERVER CODE: NOT UPDATED")
    assert "updated" in res
    assert "github_sha" in res
    assert "deployed_sha" in res
    assert "running_sha" in res
