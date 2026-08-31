"""VAYREN coding-time forensics — CLI entry point.

Automatic session boundaries come from the agent's own lifecycle log:
a task opens at the turn's first processed message and closes at the
turn's exit — no manual task-start/task-end commands.

Usage:
    python scripts/forensics/__main__.py report [--name "task"] [--full]
    python scripts/forensics/__main__.py mark --phase PHASE --action read_file --file path
    python scripts/forensics/__main__.py run --phase TESTING -- python -m pytest -q
    python scripts/forensics/__main__.py sweep
    python scripts/forensics/__main__.py status
    python scripts/forensics/__main__.py report --task-id ID [--out path]
    python scripts/forensics/__main__.py aggregate
    python scripts/forensics/__main__.py list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis import analyze_task  # noqa: E402
from recorder import (  # noqa: E402
    auto_manage,
    cmd_mark,
    cmd_run,
    cmd_status,
    cmd_sweep,
    session_task_id,
)
from report import (  # noqa: E402
    _task_ids,
    render_aggregate_report,
    render_task_report,
    render_task_report_details,
)


def cmd_report(args: argparse.Namespace) -> int:
    threshold_ms = args.idle_threshold * 1000
    task_id = args.task_id or session_task_id()
    if task_id is None:
        auto_manage(args.name)
        task_id = session_task_id()
    if task_id is None:
        print("no active session - nothing to report")
        return 0
    metrics = analyze_task(task_id, threshold_ms)
    text = render_task_report(metrics)
    if args.full:
        text += "\n\n" + render_task_report_details(metrics, threshold_ms)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"report written to {args.out}")
    return 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    del args
    print(render_aggregate_report())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    del args
    tasks = _task_ids()
    if not tasks:
        print("no tasks recorded")
        return 0
    for task_id in tasks:
        metrics = analyze_task(task_id)
        status = "closed" if metrics.end is not None else "open"
        print(f"{task_id}  {status}  {metrics.name or ''}  {metrics.elapsed_ms}ms")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forensics", description="VAYREN coding-time forensics")
    sub = parser.add_subparsers(dest="command", required=True)

    mark = sub.add_parser("mark", help="record a timestamped phase/action event")
    mark.add_argument("--name", help="name the current session")
    mark.add_argument("--phase")
    mark.add_argument("--action", required=True)
    mark.add_argument("--file")
    mark.add_argument("--command")
    mark.add_argument("--status")
    mark.add_argument("--note")
    mark.add_argument("--duration-ms", type=int)
    mark.set_defaults(func=cmd_mark)

    run = sub.add_parser("run", help="execute a command inside a measured phase")
    run.add_argument("--name", help="name the current session")
    run.add_argument("--phase", required=True)
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--capture", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)

    sweep_p = sub.add_parser("sweep", help="scan for file modifications now")
    sweep_p.set_defaults(func=cmd_sweep)

    report = sub.add_parser("report", help="render the forensic report")
    report.add_argument("--name", help="name the current session")
    report.add_argument("--task-id", help="report a specific task instead of the current one")
    report.add_argument(
        "--idle-threshold", type=int, default=120, help="gap (s) that becomes UNKNOWN"
    )
    report.add_argument("--full", action="store_true", help="append detailed audit sections")
    report.add_argument("--out")
    report.set_defaults(func=cmd_report)

    sub.add_parser("status", help="show the current session and agent turn").set_defaults(
        func=cmd_status
    )
    sub.add_parser("aggregate", help="render the multi-task aggregate report").set_defaults(
        func=cmd_aggregate
    )
    sub.add_parser("list", help="list recorded tasks").set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
