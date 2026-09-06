"""Speed instrumentation CLI (Phase 18, measurement only).

Usage:
    python scripts/speed/__main__.py mark --task T --kind milestone --name TASK_START
    python scripts/speed/__main__.py mark --task T --kind phase --name DISCOVERY
    python scripts/speed/__main__.py record --task T --class SMALL --spec "..." --repairs 0
    python scripts/speed/__main__.py show --task T
    python scripts/speed/__main__.py fingerprint --class SMALL --spec "..."
    python scripts/speed/__main__.py compile-context --spec "..." --class SMALL
    python scripts/speed/__main__.py recommend
    python scripts/speed/__main__.py dashboard --format md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bottleneck import recommend  # noqa: E402
from context import compile_context, fingerprint  # noqa: E402
from dashboard import cell as _cell  # noqa: E402
from dashboard import dashboard_lines  # noqa: E402
from dashboard import snapshot as _snapshot
from edits import first_pass, repair_tax  # noqa: E402
from journal import (  # noqa: E402
    TASK_CLASSES,
    append_jsonl,
    append_mark,
    build_record,
    git_revision,
    journal_path,
    load_jsonl,
    marks_path,
    speed_tasks,
)


def cmd_mark(args: argparse.Namespace) -> int:
    record = append_mark(
        args.task,
        args.kind,
        args.name,
        file=args.file,
        note=args.note,
        duration_ms=args.duration_ms,
        ts=args.ts,
    )
    print(f"marked {record['task']}/{record['kind']}/{record['name']} @ {record['ts']}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    marks = load_jsonl(marks_path())
    if args.fingerprint:
        fingerprint_value = args.fingerprint
    elif args.spec:
        fingerprint_value = fingerprint(args.task_class, args.spec, tuple(args.files or ()))
    else:
        fingerprint_value = None
    record = build_record(
        args.task,
        args.task_class,
        marks,
        reuse=args.reuse,
        fingerprint=fingerprint_value,
        validation_s=args.validation_s,
        tests=args.tests,
        failures=args.failures,
        repairs=args.repairs,
        files_changed=args.files_changed,
        lines_added=args.lines_added,
        lines_removed=args.lines_removed,
        human_interventions=args.human,
        validation_ref=args.validation_ref,
        revision=git_revision(),
        notes=args.notes,
    )
    append_jsonl(journal_path(), record)
    print(f"recorded {args.task} -> {journal_path().name} (total={record['total_s']})")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    journal = speed_tasks(load_jsonl(journal_path()))
    matches = [r for r in journal if r.get("task") == args.task]
    if not matches:
        print(f"no journal record for {args.task}")
        return 1
    record = matches[-1]
    print(f"task: {record.get('task')} class={record.get('task_class')}")
    for key, value in (record.get("segments") or {}).items():
        print(f"  {key}: {_cell(value, 's')}")
    print(f"  first_pass: {first_pass(record)} repair_tax: {repair_tax(record)}")
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    print(fingerprint(args.task_class, args.spec, tuple(args.files or ())))
    return 0


def cmd_compile_context(args: argparse.Namespace) -> int:
    import json

    journal = load_jsonl(journal_path())
    package = compile_context(
        args.spec,
        task_class=args.task_class,
        files=tuple(args.files or ()),
        journal_records=journal,
        revision=git_revision(),
    )
    if args.format == "json":
        print(json.dumps(package, indent=2))
    else:
        print(f"fingerprint: {package['fingerprint']} class={package['task_class']}")
        print(f"domains: {', '.join(package['domains']) or 'none (inspect request manually)'}")
        print(f"files ({len(package['files'])}):")
        for name in package["files"]:
            print(f"  {name}")
        if package["truncated_files"]:
            print(f"  ... +{package['truncated_files']} truncated (relevance cap)")
        print("tests:")
        for name in package["tests"]:
            print(f"  {name}")
        plan = package.get("validation_plan") or {}
        print(
            f"impact plan: level {plan.get('level')} ({plan.get('reason')}) "
            f"pyright={plan.get('pyright')} full_gate={plan.get('full_gate')}"
        )
        for name in plan.get("pytest") or []:
            print(f"  pytest {name}")
        print("validators:")
        for name in package["validators"]:
            print(f"  {name}")
        print(f"plans ({len(package['plans'])}, stale excluded: {package['stale_excluded']}):")
        for plan in package["plans"]:
            print(f"  {plan['task']} [{plan['fingerprint']}] {plan.get('notes') or ''}")
        print(f"constraints ({package['constraint_provenance']}):")
        for name in package["constraints"]:
            print(f"  {name}")
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    journal = speed_tasks(load_jsonl(journal_path()))
    if args.task:
        journal = [r for r in journal if r.get("task") == args.task]
    snapshot = _snapshot(journal)
    for item in recommend(snapshot):
        print(f"[{item['confidence']}] {item['rule']}: {item['recommendation']}")
        print(f"    evidence: {item['evidence']}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    journal = speed_tasks(load_jsonl(journal_path()))
    lines = dashboard_lines(journal)
    if args.format == "md":
        print("\n".join(lines))
    else:
        for line in lines:
            if line.startswith(("|", "-", "#")) or not line:
                print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the speed CLI parser."""
    parser = argparse.ArgumentParser(prog="speed", description="VAYREN speed instrumentation")
    sub = parser.add_subparsers(dest="command", required=True)

    mark = sub.add_parser("mark", help="append one marker event")
    mark.add_argument("--task", required=True)
    mark.add_argument("--kind", choices=("milestone", "phase"), required=True)
    mark.add_argument("--name", required=True)
    mark.add_argument("--file", default=None)
    mark.add_argument("--note", default=None)
    mark.add_argument("--duration-ms", type=int, default=None)
    mark.add_argument(
        "--ts",
        default=None,
        help="explicit ISO-8601 timestamp (only with citable evidence in --note)",
    )
    mark.set_defaults(func=cmd_mark)

    record = sub.add_parser("record", help="append one journal record from markers")
    record.add_argument("--task", required=True)
    record.add_argument("--class", dest="task_class", choices=TASK_CLASSES, required=True)
    record.add_argument("--reuse", choices=("cold", "warm"), default=None)
    record.add_argument("--spec", default=None)
    record.add_argument("--fingerprint", default=None)
    record.add_argument("--files", nargs="*", default=None)
    record.add_argument("--validation-s", type=float, default=None)
    record.add_argument("--tests", type=int, default=None)
    record.add_argument("--failures", type=int, default=None)
    record.add_argument("--repairs", type=int, default=None)
    record.add_argument("--files-changed", type=int, default=None)
    record.add_argument("--lines-added", type=int, default=None)
    record.add_argument("--lines-removed", type=int, default=None)
    record.add_argument("--human", type=int, default=0)
    record.add_argument("--validation-ref", default=None)
    record.add_argument("--notes", default=None)
    record.set_defaults(func=cmd_record)

    show = sub.add_parser("show", help="show the latest journal record for a task")
    show.add_argument("--task", required=True)
    show.set_defaults(func=cmd_show)

    fp = sub.add_parser("fingerprint", help="print the task fingerprint")
    fp.add_argument("--class", dest="task_class", required=True)
    fp.add_argument("--spec", required=True)
    fp.add_argument("--files", nargs="*", default=None)
    fp.set_defaults(func=cmd_fingerprint)

    compile_p = sub.add_parser("compile-context", help="print the minimal context package")
    compile_p.add_argument("--spec", required=True)
    compile_p.add_argument("--class", dest="task_class", default="UNCLASSIFIED")
    compile_p.add_argument("--files", nargs="*", default=None)
    compile_p.add_argument("--format", choices=("text", "json"), default="text")
    compile_p.set_defaults(func=cmd_compile_context)

    recommend_p = sub.add_parser("recommend", help="print next-optimization recommendations")
    recommend_p.add_argument("--task", default=None)
    recommend_p.set_defaults(func=cmd_recommend)

    dashboard = sub.add_parser("dashboard", help="print the speed dashboard")
    dashboard.add_argument("--format", choices=("md", "text"), default="md")
    dashboard.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
