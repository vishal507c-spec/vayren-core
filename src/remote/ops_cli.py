"""CLI interface for VAYREN Server Operations and Agent Management.

Can be run locally on the server by the VAYREN Agent, administrative scripts,
or AWS Systems Manager documents. Returns clean structured JSON or human summaries.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_src_path() -> None:
    try:
        anchor = Path(__file__).resolve()
        repo_root = anchor.parents[2]
        src = repo_root / "src"
        if src.is_dir() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
    except Exception:
        pass


_bootstrap_src_path()

from remote.server_ops import APPROVED_OPERATIONS, execute_server_op  # noqa: E402


def default_data_dir() -> str:
    home = Path.home()
    return str(home / ".vayren" / "data")


def default_strategy_dir() -> str:
    home = Path.home()
    return str(home / ".vayren" / "strategies")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vayren-ops",
        description="VAYREN Server Management and Agent Operations CLI",
    )
    parser.add_argument(
        "operation",
        choices=sorted(APPROVED_OPERATIONS),
        help="Approved operation to execute",
    )
    parser.add_argument("--data-dir", default=default_data_dir(), help="Data directory")
    parser.add_argument("--strategy-dir", default=default_strategy_dir(), help="Strategy directory")
    parser.add_argument(
        "--confirm", action="store_true", help="Confirm disruptive operation (deploy, restart)"
    )
    parser.add_argument("--lines", type=int, default=50, help="Max lines for log retrieval")
    parser.add_argument("--level", default="ERROR", help="Log level filter (ERROR, WARNING, INFO)")
    parser.add_argument("--all-days", action="store_true", help="Include logs prior to today")
    parser.add_argument("--json", action="store_true", help="Output raw JSON format only")

    args = parser.parse_args(argv)

    params = {
        "confirmed": args.confirm,
        "lines": args.lines,
        "level": args.level,
        "today": not args.all_days,
    }

    result = execute_server_op(
        op_name=args.operation,
        params=params,
        data_dir=args.data_dir,
        strategy_dir=args.strategy_dir,
        caller="agent_cli",
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok", False) else 1

    # Human-readable format
    op = args.operation
    if op == "health":
        print("=== VAYREN EC2 HEALTH CHECK ===")
        print(f"Status:             {result.get('ec2_status')}")
        print(
            f"Gateway Service:    {result.get('gateway_status')} (PID: {result.get('gateway_pid')})"
        )
        w_status = result.get("trading_worker_status")
        w_running = result.get("trading_worker_running")
        print(f"Trading Worker:     {w_status} (Running: {w_running})")
        b_sel = result.get("selected_broker")
        b_stat = result.get("broker_live_status")
        print(f"Selected Broker:    {b_sel} ({b_stat})")
        print(f"System Uptime:      {result.get('uptime')}")
        print(f"Load Average:       {result.get('load_average')}")
        mem = result.get("memory", {})
        m_used, m_tot, m_pct = mem.get("used_mb"), mem.get("total_mb"), mem.get("percent")
        print(f"Memory:             {m_used}MB / {m_tot}MB ({m_pct}%)")
        disk = result.get("disk", {})
        d_used, d_tot, d_pct = disk.get("used_gb"), disk.get("total_gb"), disk.get("percent")
        print(f"Disk:               {d_used}GB / {d_tot}GB ({d_pct}%)")
        print(f"Active Strategy:    {result.get('active_strategy') or 'None'}")
        print(f"Symbols Count:      {result.get('configured_symbols')}")
    elif op == "status":
        print("=== VAYREN GATEWAY & WORKER STATUS ===")
        svc = result.get("service", {})
        print(f"Gateway Service:    {svc.get('status')} (PID: {svc.get('main_pid')})")
        port_ok = "LISTENING" if result.get("port_8765_listening") else "NOT LISTENING"
        print(f"Port 8765 Loopback: {port_ok}")
        worker = result.get("trading_worker", {})
        print(f"Trading Worker:     {worker.get('worker_status')} (Mode: {worker.get('mode')})")
        print(f"Active Strategy:    {worker.get('strategy') or 'None'}")
        print(f"Broker:             {worker.get('broker_name')} ({worker.get('broker_status')})")
        if worker.get("start_blockers"):
            print(f"Blockers:           {'; '.join(worker['start_blockers'])}")
    elif op == "logs":
        l_cnt = result.get("lines_count")
        l_lvl = result.get("level_filter")
        print(f"=== RECENT LOGS ({l_cnt} lines, level: {l_lvl}) ===")
        for line in result.get("logs", []):
            print(line)
    elif op == "backup":
        print("=== VAYREN BACKUP VERIFICATION ===")
        if result.get("ok"):
            print(f"Status:             {result.get('status')}")
            print(f"Archive:            {result.get('archive_path')}")
            print(f"Size:               {round(result.get('size_bytes', 0) / 1024, 1)} KB")
            print(f"SHA-256:            {result.get('sha256')}")
        else:
            print(f"FAILED:             {result.get('error')}")
    elif op == "deploy":
        print("=== VAYREN DEPLOYMENT ===")
        if result.get("ok"):
            print(f"Status:             {result.get('status')}")
            print(f"Deployed Revision:  {result.get('deployed_revision')}")
            print(f"Pre-deploy Backup:  {result.get('backup_created')}")
        else:
            print(f"FAILED:             {result.get('error')}")
    elif op == "restart":
        print("=== VAYREN GATEWAY RESTART ===")
        if result.get("ok"):
            print(f"Status:             {result.get('status')}")
            print("Gateway restarted successfully and verified active.")
        else:
            print(f"FAILED:             {result.get('error')}")

    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    sys.exit(main())
