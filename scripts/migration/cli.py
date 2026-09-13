"""Migration CLI: scan, plan, status, verify, parity, shadow, gates, report.

No command allows unsafe manual status manipulation: there is no
``mark-migrated``. States advance only through ``verify`` (evidence) and the
explicit audited gates ``promote`` / ``rollback`` / ``quarantine`` /
``finalize``.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import engine as _engine
from . import finalizer as _finalizer
from . import fuzzing as _fuzzing
from . import golden as _golden
from . import graph as _graph
from . import manifest_store as _store
from . import parity as _parity
from . import planner as _planner
from . import promotion as _promotion
from . import quarantine as _quarantine
from . import reporter as _reporter
from . import rollback as _rollback
from . import shadow as _shadow
from . import validator as _validator
from .db_migrate import scan_data_dir
from .scanner import inventory_dict, scan


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_scan(args: argparse.Namespace) -> int:
    import json

    from .config import GRAPH_PATH, INVENTORY_PATH
    from .golden import persist_golden
    from .graph import build_graph, graph_dict

    inv = scan()
    summary = inventory_dict(inv)
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    GRAPH_PATH.write_text(
        json.dumps(graph_dict(build_graph()), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    persist_golden()
    if args.json:
        _print_json(summary)
    else:
        print(f"python files: {summary['python_files']}")
        print(f"rust-owned python: {summary['rust_owned_python']}")
        print(f"rust files: {len(summary['rust_files'])}")
        print(f"bridges: {len(summary['bridges'])}")
        print(f"retention missing: {len(summary['retention_missing'])}")
        print(f"retention orphaned: {len(summary['retention_orphaned'])}")
        print(f"rust unused: {summary['rust_unused'] or 'none'}")
        print(f"python authoritative: {len(summary['python_authoritative'])}")
        print(f"unresolved deps: {summary['unresolved_dependencies'] or 'none'}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    plan = _planner.build_plan()
    if args.json:
        _print_json([entry.__dict__ for entry in plan])
        return 0
    order = _graph.migration_order(_graph.build_graph())
    print(f"migration order: {' -> '.join(order[:6])}")
    for entry in plan:
        deps = ",".join(entry.dependencies) or "-"
        print(f"{entry.verdict:8} {entry.unit_id:32} state={entry.state:16} deps={deps}")
        if entry.verdict == "BLOCKED":
            print(f"         blocked-by={','.join(entry.blockers) or 'own-evidence'}")
            print(f"         action: {entry.required_action}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    manifests = _store.load_all()
    if args.json:
        _print_json({uid: m.to_dict() for uid, m in sorted(manifests.items())})
        return 0
    for uid, manifest in sorted(manifests.items()):
        print(
            f"{manifest.state:18} auth={manifest.authority:6} "
            f"parity={manifest.parity.status:12} shadow={manifest.shadow.status:12} {uid}"
        )
    return 0


def cmd_ready(_args: argparse.Namespace) -> int:
    for entry in _planner.ready_units():
        print(f"READY: {entry.unit_id}")
    return 0


def cmd_blockers(_args: argparse.Namespace) -> int:
    blocked = _planner.blocked_units()
    if not blocked:
        print("no blockers — every unit is migrated or ready")
        return 0
    for entry in blocked:
        print("")
        print("MIGRATION BLOCKED")
        print(f"Unit: {entry.unit_id}")
        print(f"Authority: {entry.owner}")
        print(f"Blocked by: {', '.join(entry.blockers) or 'own evidence missing'}")
        print(f"Reason: state={entry.state} parity={entry.parity} shadow={entry.shadow}")
        print(f"Required: {entry.required_action}")
        print("Status: BLOCKED")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    summary = _engine.verify(shadow_count=args.shadow_count)
    if args.json:
        _print_json(summary)
    else:
        for uid, state in summary["states"].items():
            print(f"{state:18} {uid}")
    code, gate = _validator.validate_all()
    print(f"gate: {'PASS' if code == 0 else 'FAIL'} ({gate['failures']} failures)")
    return code


def cmd_parity(args: argparse.Namespace) -> int:
    outcomes = _parity.run_all_parity() if args.unit is None else [_parity.run_parity(args.unit)]
    failed = False
    for outcome in outcomes:
        print(f"{outcome.verdict:12} {outcome.unit_id} {outcome.passed}/{outcome.cases} passed")
        for mismatch in outcome.mismatches[:5]:
            print(f"  mismatch: {mismatch}")
        if outcome.detail:
            print(f"  detail: {outcome.detail}")
        if outcome.verdict == "FAIL":
            failed = True
    return 1 if failed else 0


def cmd_fuzz(args: argparse.Namespace) -> int:
    outcomes = _fuzzing.run_fuzz(args.unit, trials=args.trials)
    failed = False
    for outcome in outcomes:
        print(f"{outcome.verdict:12} {outcome.unit_id} trials={outcome.trials}")
        for failure in outcome.failures[:5]:
            print(f"  failure: {failure}")
        if outcome.detail:
            print(f"  detail: {outcome.detail}")
        if outcome.verdict == "FAIL":
            failed = True
    return 1 if failed else 0


def cmd_shadow(args: argparse.Namespace) -> int:
    if args.unit is None:
        outcomes = _shadow.run_all_shadow(args.count)
    else:
        outcomes = [_shadow.run_shadow(args.unit, args.count)]
    failed = False
    for outcome in outcomes:
        print(
            f"{outcome.verdict:12} {outcome.unit_id} "
            f"{outcome.comparisons} comparisons, {outcome.mismatches} mismatches"
        )
        if outcome.detail:
            print(f"  detail: {outcome.detail}")
        if outcome.verdict == "FAIL":
            failed = True
    return 1 if failed else 0


def cmd_promote(args: argparse.Namespace) -> int:
    ok, message = _promotion.promote(args.unit, args.actor)
    print(message)
    return 0 if ok else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    ok, message = _rollback.rollback(args.unit, args.reason, args.actor)
    print(message)
    return 0 if ok else 1


def cmd_quarantine(args: argparse.Namespace) -> int:
    ok, message = _quarantine.quarantine(args.unit, args.actor)
    print(message)
    return 0 if ok else 1


def cmd_finalize(args: argparse.Namespace) -> int:
    ok, message = _finalizer.finalize(args.unit, args.actor)
    print(message)
    return 0 if ok else 1


def cmd_report(args: argparse.Namespace) -> int:
    json_path, md_path = _reporter.write_report()
    report = _reporter.build_report()
    if args.json:
        _print_json(report["summary"])
    else:
        print(f"wrote {json_path}")
        print(f"wrote {md_path}")
        for key, value in report["summary"].items():
            print(f"{key}: {value}")
    return 0


def cmd_db(args: argparse.Namespace) -> int:
    from pathlib import Path

    reports = scan_data_dir(Path(args.data_dir), limit=args.limit)
    if not reports:
        print(f"no databases under {args.data_dir}")
        return 0
    bad = 0
    for report in reports:
        from .db_migrate import plan_forward

        plan = plan_forward(report)
        status = "compatible" if report.compatible else "DRIFT"
        print(f"{status:10} {report.db} tables={len(report.tables)}")
        if not report.compatible:
            bad += 1
            for step in plan["forward_steps"][:5]:
                print(f"  forward: {step}")
    print(f"checked={len(reports)} drift={bad}")
    return 0


def cmd_golden(_args: argparse.Namespace) -> int:
    written = _golden.persist_golden()
    print(f"golden files: {len(written)}")
    for path in written:
        print(f"  {path}")
    return 0


def cmd_agent_order(args: argparse.Namespace) -> int:
    from .agent.order import blocked_order

    entries = blocked_order()
    if args.json:
        _print_json([entry.__dict__ for entry in entries])
        return 0
    for entry in entries:
        critical = "live-critical" if entry.live_critical else "standard"
        print(f"{entry.position}. {entry.unit_id} (depth {entry.depth}, {critical})")
        print(f"   blocked-by: {', '.join(entry.blocked_by) or 'none'}")
        print(f"   why: {entry.why}")
    return 0


def cmd_agent_analyze(args: argparse.Namespace) -> int:
    from .agent.analyzer import analyze

    report = analyze(args.unit)
    if args.json:
        _print_json(report.__dict__)
        return 0
    print(f"unit: {report.unit_id}")
    print(f"migratable: {report.migratable}")
    print(f"reason: {report.reason}")
    if report.kernel_checks:
        print(f"kernel: {', '.join(report.kernel_checks)}")
    if report.orchestration_checks:
        print(f"orchestration: {', '.join(report.orchestration_checks)}")
    return 0


def cmd_agent_run(args: argparse.Namespace) -> int:
    from .agent.pipeline import run_all

    only = args.only.split(",") if args.only else None
    outcomes = run_all(dry_run=args.dry_run, only=only)
    if not outcomes:
        print("no BLOCKED units to process")
        return 0
    failed = False
    for outcome in outcomes:
        print(f"{outcome.verdict:10} {outcome.unit_id}")
        for line in outcome.evidence[-4:]:
            print(f"  {line}")
        if outcome.verdict in ("FAILED",):
            failed = True
    return 1 if failed else 0


def cmd_route(args: argparse.Namespace) -> int:
    from .router import route_request

    record = route_request(args.request, files=args.file or None)
    if args.json:
        _print_json(record.to_dict())
        return 0
    print(f"domain:    {record.detected_domain}")
    print(f"behavior:  {record.detected_behavior}")
    print(f"language:  {record.canonical_language} (confidence: {record.confidence})")
    for layer in record.layers:
        print(
            f"  layer {layer.layer}: {layer.language} <- {', '.join(layer.paths) or '(paths TBD)'}"
        )
    if record.forbidden_paths:
        print("forbidden:")
        for path in record.forbidden_paths:
            print(f"  - {path}")
    if record.migration_status:
        print("migration: " + ", ".join(record.migration_status))
    print("validate:  " + ", ".join(record.validation_required))
    for note in record.notes:
        print(f"note: {note}")
    return 2 if record.decision_required else 0


def cmd_manifest(args: argparse.Namespace) -> int:
    from .router import build_architecture_manifest, write_architecture_manifest

    if args.write:
        print(f"wrote {write_architecture_manifest()}")
        return 0
    manifest = build_architecture_manifest()
    if args.json:
        _print_json(manifest)
        return 0
    for unit_id, entry in sorted(manifest["units"].items()):
        print(
            f"{entry['canonical_language']:8} {unit_id:32} "
            f"state={entry['migration_state']:16} {entry['canonical_path'] or '(path TBD)'}"
        )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    from .change_gate import check_changes

    report = check_changes(ref=args.ref)
    if args.json:
        _print_json(report.to_dict())
    elif report.verdict == "PASS":
        print(f"Architecture gate PASSED ({report.files_checked} files checked)")
        for warning in report.warnings[:20]:
            print(f"  warn: {warning}")
        if report.debt_created:
            print(f"  migration debt created: +{report.debt_created} definitions")
    elif report.verdict == "ERROR":
        print("Architecture gate ERROR:")
        for detail in report.details:
            print(f"  - {detail}")
    else:
        print("Architecture gate FAILED:")
        for violation in report.violations:
            print(f"  - {violation.file}: [{violation.rule}] {violation.message}")
    return {"PASS": 0, "FAIL": 1}.get(report.verdict, 2)


def cmd_background(args: argparse.Namespace) -> int:
    from .agent.worker import run_background

    units = args.units.split(",") if args.units else None
    outcome = run_background(
        units=units,
        max_units=args.max_units,
        include_dirty=args.include_dirty,
        actor=args.actor,
    )
    if args.json:
        _print_json(outcome.to_dict())
        return 0
    print(f"worker: {outcome.lock_note}")
    for skipped in outcome.skipped:
        print(f"  skipped: {skipped}")
    failed = False
    for item in outcome.outcomes:
        print(f"{item.verdict:10} {item.unit_id}")
        if item.verdict == "FAILED":
            failed = True
    if not outcome.outcomes and not outcome.skipped:
        print("  nothing to process")
    return 1 if failed else 0


def cmd_task_report(args: argparse.Namespace) -> int:
    from .task_report import build_task_report

    report = build_task_report(ref=args.ref)
    if args.json:
        _print_json(report)
        return 0
    print("ARCHITECTURE")
    for entry in report["architecture"]["files"]:
        print(f"  {entry['file']}: domain={entry['domain']} canonical={entry['canonical']}")
    print("CHANGES")
    for key in ("rust", "slint", "python", "accidental_python", "other"):
        values = report["changes"][key]
        print(f"  {key}: {', '.join(values) or '-'}")
    print("VALIDATION")
    print(f"  architecture gate: {report['validation']['architecture_gate']}")
    for warning in report["validation"]["gate_warnings"][:10]:
        print(f"  warn: {warning}")
    print("MIGRATION IMPACT")
    print(f"  new migration debt: +{report['migration_impact']['new_migration_debt']}")
    print(f"  python retained: {', '.join(report['migration_impact']['python_retained']) or '-'}")
    print("STATUS")
    print(f"  {report['status']}")
    return {"PASS": 0, "BLOCKED": 3, "FAILED": 1}.get(report["status"], 2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migration", description="VAYREN evidence-driven migration"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "plan", "status", "ready", "blockers", "report", "golden"):
        child = sub.add_parser(name)
        child.add_argument("--json", action="store_true")
        child.set_defaults(
            func={
                "scan": cmd_scan,
                "plan": cmd_plan,
                "status": cmd_status,
                "ready": cmd_ready,
                "blockers": cmd_blockers,
                "report": cmd_report,
                "golden": cmd_golden,
            }[name]
        )
    verify = sub.add_parser("verify")
    verify.add_argument("--json", action="store_true")
    verify.add_argument("--shadow-count", type=int, default=60)
    verify.set_defaults(func=cmd_verify)
    parity = sub.add_parser("parity")
    parity.add_argument("unit", nargs="?")
    parity.set_defaults(func=cmd_parity)
    fuzz = sub.add_parser("fuzz")
    fuzz.add_argument("unit", nargs="?")
    fuzz.add_argument("--trials", type=int, default=120)
    fuzz.set_defaults(func=cmd_fuzz)
    shadow = sub.add_parser("shadow")
    shadow.add_argument("unit", nargs="?")
    shadow.add_argument("--count", type=int, default=60)
    shadow.set_defaults(func=cmd_shadow)
    for name, func in (
        ("promote", cmd_promote),
        ("quarantine", cmd_quarantine),
        ("finalize", cmd_finalize),
    ):
        child = sub.add_parser(name)
        child.add_argument("unit")
        child.add_argument("--actor", default="migration-cli")
        child.set_defaults(func=func)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("unit")
    rollback.add_argument("--reason", default="post-cutover regression")
    rollback.add_argument("--actor", default="migration-cli")
    rollback.set_defaults(func=cmd_rollback)
    db = sub.add_parser("db")
    db.add_argument("--data-dir", default="~/.vayren/data")
    db.add_argument("--limit", type=int, default=25)
    db.set_defaults(func=cmd_db)
    agent_order = sub.add_parser("agent-order")
    agent_order.add_argument("--json", action="store_true")
    agent_order.set_defaults(func=cmd_agent_order)
    agent_analyze = sub.add_parser("agent-analyze")
    agent_analyze.add_argument("unit")
    agent_analyze.add_argument("--json", action="store_true")
    agent_analyze.set_defaults(func=cmd_agent_analyze)
    agent_run = sub.add_parser("agent-run")
    agent_run.add_argument("--only", default="")
    agent_run.add_argument("--dry-run", action="store_true")
    agent_run.set_defaults(func=cmd_agent_run)
    route = sub.add_parser("route")
    route.add_argument("request")
    route.add_argument("--file", action="append", default=[])
    route.add_argument("--json", action="store_true")
    route.set_defaults(func=cmd_route)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--json", action="store_true")
    manifest.add_argument("--write", action="store_true")
    manifest.set_defaults(func=cmd_manifest)
    gate = sub.add_parser("gate")
    gate.add_argument("--ref", default="HEAD")
    gate.add_argument("--json", action="store_true")
    gate.set_defaults(func=cmd_gate)
    background = sub.add_parser("background")
    background.add_argument("--units", default="")
    background.add_argument("--max-units", type=int, default=None)
    background.add_argument("--include-dirty", action="store_true")
    background.add_argument("--actor", default="background-worker")
    background.add_argument("--json", action="store_true")
    background.set_defaults(func=cmd_background)
    task_report = sub.add_parser("task-report")
    task_report.add_argument("--ref", default="HEAD")
    task_report.add_argument("--json", action="store_true")
    task_report.set_defaults(func=cmd_task_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
