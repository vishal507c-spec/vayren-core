"""Authorized server management operations for VAYREN on EC2.

Provides an allowlist of approved administrative and health operations:
- health_check: system health (CPU, RAM, Disk, Uptime, Gateway service, Worker status)
- gateway_status: service status of vayren-remote and network transport
- get_logs: sanitized logs (secrets/credentials/tokens scrubbed)
- create_backup: verified tarball backup of data and strategies with SHA-256
- deploy_approved: safe git update with pre-deploy backup, health check, rollback info
- restart_gateway: safe restart of vayren-remote.service with active session protection

Every operation is audited to ~/.vayren/data/audit_ops.log.
Arbitrary shell execution is strictly disallowed.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("remote.server_ops")

SERVICE_NAME = "vayren-remote"
# Root-owned systemd path unit (installed from deploy/). It grants exactly one
# operation: restart vayren-remote.service when the gateway writes a request
# marker into its own data directory. The gateway never gains sudo/root.
RESTART_HELPER_PATH_UNIT = Path("/etc/systemd/system/vayren-remote-restart.path")
# The helper watches ONLY the production data directory. A gateway started
# against another directory fails closed instead of writing a marker that
# will never be actioned.
RESTART_HELPER_DATA_DIR = Path("/home/ubuntu/.vayren/data")
# Wait briefly so the command result reaches the client before the service
# (and therefore the WebSocket) is restarted.
RESTART_REQUEST_DELAY_S = 0.75

APPROVED_OPERATIONS = frozenset(
    {
        "health",
        "status",
        "logs",
        "backup",
        "deploy",
        "restart",
    }
)


def validate_op(op_name: str) -> bool:
    """Check if an operation is in the approved allowlist."""
    return str(op_name or "").strip().lower() in APPROVED_OPERATIONS


# Substrings that must never appear in returned logs or command results
FORBIDDEN_PATTERNS = [
    re.compile(r"(?i)(token|password|passwd|secret|key|totp|auth_code)[\s:=]+([^\s,;]+)"),
    re.compile(r"PL_[A-Za-z0-9_-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{30,}"),
]


def scrub_sensitive_text(text: str) -> str:
    """Scrub tokens, passwords, and secrets from text before returning to caller."""
    scrubbed = text
    # Pattern with group 1
    scrubbed = re.sub(
        r"(?i)(token|password|passwd|secret|key|totp|auth_code)[\s:=]+([^\s,;]+)",
        r"\1: [REDACTED]",
        scrubbed,
    )
    # Token hashes and JWT patterns without groups
    for pat in (r"PL_[A-Za-z0-9_-]{20,}", r"eyJ[A-Za-z0-9_-]{30,}"):
        scrubbed = re.sub(pat, "[REDACTED]", scrubbed)
    return scrubbed


def _get_uptime_string() -> str:
    """Read Linux system uptime."""
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            total_seconds = float(f.readline().split()[0])
            days = int(total_seconds // 86400)
            hours = int((total_seconds % 86400) // 3600)
            minutes = int((total_seconds % 3600) // 60)
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0 or days > 0:
                parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            return " ".join(parts)
    except Exception:
        return "unknown"


def _get_memory_info() -> dict[str, Any]:
    """Read memory stats from /proc/meminfo."""
    try:
        mem: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    if val.isdigit():
                        mem[k] = int(val) // 1024  # Convert kB to MB
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        used = max(0, total - avail)
        pct = round((used / total * 100), 1) if total > 0 else 0.0
        return {"total_mb": total, "used_mb": used, "free_mb": avail, "percent": pct}
    except Exception:
        return {"total_mb": 0, "used_mb": 0, "free_mb": 0, "percent": 0.0}


def _get_load_average() -> list[float]:
    """Get system load averages."""
    try:
        fn = getattr(os, "getloadavg", None)
        if callable(fn):
            raw = fn()
            if isinstance(raw, (tuple, list)):
                return [round(float(x), 2) for x in raw]
        return [0.0, 0.0, 0.0]
    except Exception:
        return [0.0, 0.0, 0.0]


def _get_disk_info(path: str) -> dict[str, Any]:
    """Get filesystem disk usage."""
    try:
        usage = shutil.disk_usage(path)
        total_gb = round(usage.total / (1024**3), 2)
        free_gb = round(usage.free / (1024**3), 2)
        used_gb = round(usage.used / (1024**3), 2)
        pct = round((usage.used / usage.total * 100), 1) if usage.total > 0 else 0.0
        return {"total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb, "percent": pct}
    except Exception:
        return {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0}


def _get_service_status(service_name: str = "vayren-remote") -> dict[str, Any]:
    """Check systemd service status."""
    try:
        res = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        is_active = res.stdout.strip() == "active"
        show_res = subprocess.run(
            ["systemctl", "show", service_name, "--property=MainPID,ActiveEnterTimestamp"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        props: dict[str, str] = {}
        for line in show_res.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        return {
            "name": service_name,
            "active": is_active,
            "status": "ACTIVE" if is_active else res.stdout.strip().upper(),
            "main_pid": int(props.get("MainPID", "0") or "0"),
            "active_since": props.get("ActiveEnterTimestamp", ""),
        }
    except Exception as exc:
        return {"name": service_name, "active": False, "status": f"UNKNOWN: {exc}", "main_pid": 0}


def _get_worker_runtime_status(data_dir: str, strategy_dir: str) -> dict[str, Any]:
    """Inspect current in-process or persisted trading worker state."""
    try:
        from app.headless import _trading_service_snapshot

        snap = _trading_service_snapshot(data_dir, strategy_dir)
        session_status = str(snap.get("session_status", "STOPPED") or "STOPPED").upper()
        mode = str(snap.get("mode", "BACKTEST") or "BACKTEST").upper()
        strat = snap.get("strategy", {})
        strat_name = str(strat.get("id", "") or "") if isinstance(strat, dict) else ""
        symbols = snap.get("selected_symbols", []) or []
        broker_dict = snap.get("broker", {}) or {}
        broker_name = str(broker_dict.get("name", "") or "")
        broker_status = str(broker_dict.get("status", "") or "")
        start_blockers = list(snap.get("start_blockers", []) or [])
        return {
            "worker_status": session_status,
            "running": session_status == "RUNNING",
            "mode": mode,
            "strategy": strat_name,
            "symbols_count": len(symbols),
            "broker_name": broker_name,
            "broker_status": broker_status,
            "start_blockers": start_blockers,
            "uptime_seconds": snap.get("elapsed_seconds", 0),
        }
    except Exception as exc:
        return {
            "worker_status": "UNKNOWN",
            "running": False,
            "mode": "BACKTEST",
            "strategy": "",
            "symbols_count": 0,
            "broker_name": "",
            "broker_status": "",
            "start_blockers": [f"snapshot unavailable: {exc}"],
            "uptime_seconds": 0,
        }


def op_health_check(data_dir: str, strategy_dir: str) -> dict[str, Any]:
    """Execute complete health check of EC2 host, gateway service, and trading worker."""
    service_info = _get_service_status("vayren-remote")
    worker_info = _get_worker_runtime_status(data_dir, strategy_dir)
    mem_info = _get_memory_info()
    disk_info = _get_disk_info(data_dir)
    load_avg = _get_load_average()
    uptime_str = _get_uptime_string()

    # Read selected broker directly from disk store
    broker_selection = "unknown"
    try:
        sel_path = Path(data_dir) / "broker_selection.json"
        if sel_path.is_file():
            with open(sel_path, encoding="utf-8") as f:
                broker_selection = json.load(f).get("name", "unknown")
    except Exception:
        pass

    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    return {
        "ok": True,
        "ec2_status": "HEALTHY"
        if mem_info["percent"] < 95.0 and disk_info["percent"] < 95.0
        else "DEGRADED",
        "gateway_status": service_info.get("status", "UNKNOWN"),
        "gateway_pid": service_info.get("main_pid", 0),
        "trading_worker_status": worker_info.get("worker_status", "STOPPED"),
        "trading_worker_running": worker_info.get("running", False),
        "selected_broker": broker_selection,
        "broker_live_status": worker_info.get("broker_status", "UNKNOWN"),
        "uptime": uptime_str,
        "load_average": load_avg,
        "memory": mem_info,
        "disk": disk_info,
        "active_strategy": worker_info.get("strategy", ""),
        "configured_symbols": worker_info.get("symbols_count", 0),
        "timestamp": now_iso,
    }


def op_gateway_status(data_dir: str, strategy_dir: str) -> dict[str, Any]:
    """Get detailed gateway service and network status."""
    service_info = _get_service_status("vayren-remote")
    worker_info = _get_worker_runtime_status(data_dir, strategy_dir)

    # Check loopback listening port
    port_listening = False
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            port_listening = s.connect_ex(("127.0.0.1", 8765)) == 0
    except Exception:
        pass

    return {
        "ok": True,
        "service": service_info,
        "port_8765_listening": port_listening,
        "trading_worker": worker_info,
        "data_dir": data_dir,
        "strategy_dir": strategy_dir,
    }


def op_get_logs(lines: int = 50, level: str = "ERROR", today_only: bool = True) -> dict[str, Any]:
    """Retrieve recent journalctl logs for vayren-remote safely scrubbed."""
    cmd = ["journalctl", "-u", "vayren-remote", "-n", str(min(200, max(10, lines))), "--no-pager"]
    if today_only:
        cmd.extend(["--since", "today"])

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)
        raw_lines = res.stdout.splitlines()
        scrubbed_lines: list[str] = []
        for line in raw_lines:
            if level.upper() == "ERROR" and not any(
                k in line.upper() for k in ("ERROR", "WARNING", "FAIL", "EXCEPTION", "REJECT")
            ):
                continue
            scrubbed_lines.append(scrub_sensitive_text(line))
        return {
            "ok": True,
            "lines_count": len(scrubbed_lines),
            "level_filter": level,
            "today_only": today_only,
            "logs": scrubbed_lines,
        }
    except Exception as exc:
        return {"ok": False, "error": f"failed to read logs: {exc}", "logs": []}


def op_create_backup(data_dir: str, strategy_dir: str) -> dict[str, Any]:
    """Create timestamped, SHA-256 verified backup of VAYREN data and strategies."""
    backup_dir = Path(data_dir).parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    archive_name = f"vayren_backup_{stamp}.tar.gz"
    archive_path = backup_dir / archive_name

    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            if Path(data_dir).is_dir():
                tar.add(
                    data_dir,
                    arcname="data",
                    filter=lambda ti: None if ti.name.endswith(".sock") else ti,
                )
            if Path(strategy_dir).is_dir():
                tar.add(strategy_dir, arcname="strategies")

        # Verify tar integrity
        if not tarfile.is_tarfile(archive_path):
            archive_path.unlink(missing_ok=True)
            return {"ok": False, "error": "created archive failed integrity check"}

        # Calculate SHA256
        hasher = hashlib.sha256()
        with open(archive_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        size_bytes = archive_path.stat().st_size

        return {
            "ok": True,
            "archive_path": str(archive_path),
            "archive_name": archive_name,
            "sha256": digest,
            "size_bytes": size_bytes,
            "status": "VERIFIED",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        return {"ok": False, "error": f"backup creation failed: {exc}"}


def _no_new_privileges() -> bool:
    """True when this process runs under the systemd NoNewPrivileges sandbox.

    ``PR_SET_NO_NEW_PRIVS`` is irreversible and visible in
    ``/proc/self/status``. Inside that sandbox a setuid ``sudo`` can never
    elevate, so the direct path is skipped instead of producing misleading
    sudo stderr.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("NoNewPrivs:"):
                    return line.split(":", 1)[1].strip() == "1"
    except OSError:
        return False
    return False


def _restart_helper_available(data_dir: str) -> bool:
    """The root helper exists AND watches this exact data directory."""
    try:
        return (
            RESTART_HELPER_PATH_UNIT.is_file()
            and Path(data_dir).resolve() == RESTART_HELPER_DATA_DIR.resolve()
        )
    except OSError:
        return False


def _restart_marker(data_dir: str) -> Path:
    return Path(data_dir) / "restart.request"


def _request_helper_restart(
    data_dir: str, caller: str, delay_s: float | None = None
) -> dict[str, Any]:
    """Publish an operator-auditable restart request for the root helper.

    The payload is data only: the helper unit has a fixed ExecStart and never
    parses or executes this file. Writing the temp file synchronously proves
    the gateway may write the marker; publishing it is delayed so the command
    acknowledgement leaves the socket before systemd restarts the service.
    """
    wait = RESTART_REQUEST_DELAY_S if delay_s is None else max(0.0, float(delay_s))
    marker = _restart_marker(data_dir)
    payload = {
        "requested_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "caller": str(caller or "operator"),
        "operation": "restart",
        "unit": f"{SERVICE_NAME}.service",
    }
    temp = marker.with_name(marker.name + ".tmp")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "error": f"RESTART_UNAVAILABLE: cannot write restart request {marker}: {exc}",
        }

    def _publish() -> None:
        try:
            os.replace(temp, marker)
        except OSError:
            logger.exception("failed to publish restart request %s", marker)

    if wait > 0:
        timer = threading.Timer(wait, _publish)
        timer.daemon = True
        timer.start()
    else:
        _publish()
    return {
        "ok": True,
        "status": "RESTART_REQUESTED",
        "helper": "vayren-remote-restart.path",
        "unit": f"{SERVICE_NAME}.service",
        "request": str(marker),
        "detail": (
            "operator-authorized restart requested; the gateway restarts "
            "within ~1s and clients reconnect automatically"
        ),
    }


def _direct_restart() -> dict[str, Any]:
    """Restart via the operator's own sudo rights (SSH/ops-cli path only)."""
    try:
        proc = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - reported as the real cause
        return {"ok": False, "error": f"sudo/systemctl could not run: {exc}"}
    if proc.returncode != 0:
        cause = (proc.stderr or proc.stdout or "").strip()
        return {
            "ok": False,
            "error": scrub_sensitive_text(cause)
            or f"sudo systemctl restart exited {proc.returncode}",
        }
    verify = _get_service_status(SERVICE_NAME)
    if not verify.get("active"):
        return {
            "ok": False,
            "error": (
                "restart command succeeded but the service is not active "
                f"(status: {verify.get('status', 'UNKNOWN')})"
            ),
            "service": verify,
        }
    return {"ok": True, "status": "RESTARTED", "service": verify}


def _restart_gateway_service(data_dir: str, caller: str) -> dict[str, Any]:
    """Authorized restart: direct operator privilege first, helper second.

    - Operator SSH/CLI: the caller already has the host's sudo policy; a
      non-interactive ``sudo -n systemctl restart`` works and is verified.
    - Sandboxed gateway (NoNewPrivileges): sudo is impossible by design. If
      the narrowly scoped root helper is installed, publish its request
      marker; otherwise fail with the exact cause and the operator command.
    """
    sandboxed = _no_new_privileges()
    direct_error = ""
    if not sandboxed:
        direct = _direct_restart()
        if direct.get("ok"):
            return direct
        direct_error = str(direct.get("error", "") or "direct restart failed")

    if _restart_helper_available(data_dir):
        requested = _request_helper_restart(data_dir, caller)
        if direct_error and requested.get("ok"):
            requested["direct_error"] = direct_error
        return requested

    cause = (
        "gateway is sandboxed (NoNewPrivileges=1)"
        if sandboxed
        else f"direct restart failed: {direct_error}"
    )
    return {
        "ok": False,
        "error": (
            f"RESTART_UNAVAILABLE: {cause} and the vayren-remote-restart.path "
            "helper is not installed for this data directory; restart from an "
            "operator SSH session: sudo systemctl restart "
            f"{SERVICE_NAME}.service"
        ),
    }


def op_deploy_approved(
    data_dir: str,
    strategy_dir: str,
    repo_dir: str = "/home/ubuntu/vayren-core",
    confirmed: bool = False,
    caller: str = "operator",
) -> dict[str, Any]:
    """Safe deployment: active session check, backup, git, and restart."""
    if not confirmed:
        return {
            "ok": False,
            "error": "DEPLOY_BLOCKED: requires confirmed=True with operator consent.",
        }

    # 1. Active session guard: never interrupt a live trading session without explicit confirmation
    worker_info = _get_worker_runtime_status(data_dir, strategy_dir)
    if worker_info.get("running"):
        return {
            "ok": False,
            "error": "DEPLOY_BLOCKED: live trading session is RUNNING. Stop before deploying.",
            "worker_status": worker_info,
        }

    # 2. Pre-deployment backup
    backup_result = op_create_backup(data_dir, strategy_dir)
    if not backup_result.get("ok"):
        return {"ok": False, "error": f"Pre-deployment backup failed: {backup_result.get('error')}"}

    repo_path = Path(repo_dir)
    if not (repo_path / ".git").is_dir():
        return {
            "ok": False,
            "error": f"Repository directory {repo_dir} is not a valid git repository",
        }

    try:
        # Check current working tree
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        rev_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Pull updates safely
        pull_proc = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=repo_dir, capture_output=True, text=True, check=False
        )
        if pull_proc.returncode != 0:
            pull_err = pull_proc.stderr.strip() or pull_proc.stdout.strip()
            return {
                "ok": False,
                "error": f"git pull --ff-only failed: {pull_err}",
                "rollback_backup": backup_result.get("archive_path"),
            }

        rev_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Restart gateway service through the authorized mechanism
        restart = _restart_gateway_service(data_dir, caller)
        if not restart.get("ok"):
            return {
                "ok": False,
                "error": str(restart.get("error", "restart failed")),
                "rollback_backup": backup_result.get("archive_path"),
            }

        # Post-deployment health verification. An async helper request cannot
        # be verified from this process (it is about to be restarted); the
        # client reconnects and the fresh process reports its own health.
        post_health = (
            op_health_check(data_dir, strategy_dir)
            if restart.get("status") == "RESTARTED"
            else None
        )

        return {
            "ok": True,
            "deployed_revision": rev_after,
            "previous_revision": rev_before,
            "backup_created": backup_result.get("archive_path"),
            "restart": restart,
            "post_deploy_health": post_health,
            "status": "DEPLOYED_AND_VERIFIED" if post_health else "DEPLOYED_RESTART_REQUESTED",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"deployment failed: {exc}",
            "rollback_backup": backup_result.get("archive_path"),
        }


def op_restart_gateway(
    data_dir: str, strategy_dir: str, confirmed: bool = False, caller: str = "operator"
) -> dict[str, Any]:
    """Safely restart the vayren-remote service with active-session protection."""
    if not confirmed:
        return {
            "ok": False,
            "error": "RESTART_BLOCKED: restart requires confirmed=True with operator consent.",
        }

    worker_info = _get_worker_runtime_status(data_dir, strategy_dir)
    if worker_info.get("running"):
        return {
            "ok": False,
            "error": "RESTART_BLOCKED: live trading session is RUNNING. Stop before restart.",
        }

    return _restart_gateway_service(data_dir, caller)


def record_audit_log(data_dir: str, op_name: str, caller: str, outcome: dict[str, Any]) -> None:
    """Record an audit trail entry for every administrative operation."""
    audit_file = Path(data_dir) / "audit_ops.log"
    entry = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "caller": caller,
        "operation": op_name,
        "ok": outcome.get("ok", False),
        "status": outcome.get("status") or outcome.get("error", "UNKNOWN"),
    }
    try:
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("Failed to record audit log: %s", exc)


def execute_server_op(
    op_name: str,
    params: dict[str, Any],
    data_dir: str,
    strategy_dir: str,
    caller: str = "operator",
) -> dict[str, Any]:
    """Dispatcher for approved server operations. Enforces allowlist strictly."""
    op = str(op_name or "").strip().lower()
    if op not in APPROVED_OPERATIONS:
        allowed = sorted(APPROVED_OPERATIONS)
        res = {
            "ok": False,
            "error": f"UNKNOWN_OPERATION: {op!r} is not in approved allowlist ({allowed})",
        }
        record_audit_log(data_dir, op, caller, res)
        return res

    args = params if isinstance(params, dict) else {}

    if op == "health":
        res = op_health_check(data_dir, strategy_dir)
    elif op == "status":
        res = op_gateway_status(data_dir, strategy_dir)
    elif op == "logs":
        lines = int(args.get("lines", 50) or 50)
        level = str(args.get("level", "ERROR") or "ERROR")
        today = bool(args.get("today", True))
        res = op_get_logs(lines=lines, level=level, today_only=today)
    elif op == "backup":
        res = op_create_backup(data_dir, strategy_dir)
    elif op == "deploy":
        confirmed = bool(args.get("confirmed", False))
        res = op_deploy_approved(data_dir, strategy_dir, confirmed=confirmed, caller=caller)
    elif op == "restart":
        confirmed = bool(args.get("confirmed", False))
        res = op_restart_gateway(data_dir, strategy_dir, confirmed=confirmed, caller=caller)
    else:
        res = {"ok": False, "error": f"unhandled operation {op}"}

    if isinstance(res, dict):
        res.setdefault("action", "server_op")
        res.setdefault("op", op)

    record_audit_log(data_dir, op, caller, res)
    return res
